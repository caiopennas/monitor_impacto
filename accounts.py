"""Local accounts, revocable sessions and server-side audit trail."""
import hashlib
import hmac
import json
import os
import pathlib
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

DATA = pathlib.Path(os.environ.get('IMPACTO_DATA_DIR', str(pathlib.Path(os.environ.get('LOCALAPPDATA', str(pathlib.Path.home()))) / 'ImpactoMonitor')))
DB = DATA / 'accounts.sqlite3'
TTL = 8 * 3600

@contextmanager
def connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()

def initialize():
    with connection() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
          password TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL NOT NULL,
          actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, at REAL NOT NULL, username TEXT NOT NULL, address TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS attempts_at ON attempts(at);
        ''')

def password_hash(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ValueError('Use uma senha de 12 a 128 caracteres.')
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex()
    return salt + ':' + digest

def verify(password, encoded):
    try:
        return hmac.compare_digest(password_hash(password, encoded.split(':')[0]), encoded)
    except (ValueError, TypeError, AttributeError):
        return False

def event(actor, action, target='', result='ok', db=None):
    values = (time.time(), actor, action, str(target)[:100], result)
    if db is not None:
        db.execute('INSERT INTO audit(at,actor,action,target,result) VALUES(?,?,?,?,?)', values)
    else:
        with connection() as db:
            db.execute('INSERT INTO audit(at,actor,action,target,result) VALUES(?,?,?,?,?)', values)

def create_user(username, password, role, actor='configuracao-local', first=False):
    if not isinstance(username, str) or not re.fullmatch(r'[a-z0-9._-]{3,60}', username):
        raise ValueError('Usuário: 3 a 60 letras minúsculas, números, ponto, _ ou hífen.')
    if role not in ('admin', 'viewer'):
        raise ValueError('Perfil inválido.')
    encoded = password_hash(password)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if first and db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise ValueError('Já existe uma conta. Use a administração do painel.')
        try:
            cursor = db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)', (username, encoded, role))
        except sqlite3.IntegrityError:
            raise ValueError('Esse usuário já existe.') from None
        event(actor, 'usuario.criado', username, db=db)
        return cursor.lastrowid

def login(username, password, address):
    username = str(username)[:60].lower().strip()
    now = time.time()
    # Reserve each attempt before hashing so concurrent requests cannot bypass limits.
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM attempts WHERE at < ?', (now-900,))
        count = db.execute('SELECT COUNT(*) FROM attempts WHERE username=? OR address=?', (username, address)).fetchone()[0]
        if count >= 10:
            raise ValueError('Muitas tentativas. Aguarde 15 minutos e tente novamente.')
        db.execute('INSERT INTO attempts(at,username,address) VALUES(?,?,?)', (now, username, address))
        user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    encoded = user['password'] if user else ('00'*16 + ':' + '00'*32)
    valid = verify(password, encoded)
    with connection() as db:
        # Recheck after the expensive hash in case another administrator revoked the user.
        current = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if not valid or not current or not current['active'] or current['password'] != encoded:
            event(username, 'login', result='recusado', db=db)
            raise_login = True
        else:
            raise_login = False
            token = secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expires < ?', (now,))
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (token_hash(token), current['id'], now+TTL))
            db.execute('DELETE FROM attempts WHERE username=? AND address=?', (username, address))
            event(username, 'login', db=db)
    if raise_login:
        raise ValueError('Usuário ou senha inválidos.')
    return token

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def session(token):
    if not token or len(token) > 100:
        return None
    with connection() as db:
        row = db.execute('SELECT u.id,u.username,u.role FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1', (token_hash(token), time.time())).fetchone()
        return dict(row) if row else None

def logout(token, actor):
    with connection() as db:
        db.execute('DELETE FROM sessions WHERE token=?', (token_hash(token),))
        event(actor, 'logout', db=db)

def update_user(user_id, data, actor):
    encoded = password_hash(data['password']) if data.get('password') else None
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        user = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not user:
            raise ValueError('Usuário não encontrado.')
        active = data.get('active', bool(user['active']))
        role = data.get('role', user['role'])
        if type(active) is not bool or role not in ('admin', 'viewer'):
            raise ValueError('Perfil ou estado inválido.')
        if user['role'] == 'admin' and user['active'] and (not active or role != 'admin'):
            if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
                raise ValueError('Mantenha pelo menos um administrador ativo.')
        db.execute('UPDATE users SET active=?,role=?,password=? WHERE id=?', (int(active), role, encoded or user['password'], user_id))
        db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        event(actor, 'usuario.atualizado', user['username'], db=db)

def change_password(user, current, new):
    encoded = password_hash(new)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT password FROM users WHERE id=?', (user['id'],)).fetchone()
        if not row or not verify(current, row['password']):
            raise ValueError('Senha atual inválida.')
        db.execute('UPDATE users SET password=? WHERE id=?', (encoded, user['id']))
        db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
        event(user['username'], 'senha.alterada', db=db)

if __name__ == '__main__':
    import getpass
    initialize()
    username = input('Primeiro administrador (letras minúsculas): ').strip()
    password = getpass.getpass('Senha (mínimo 12 caracteres): ')
    if password != getpass.getpass('Confirme a senha: '):
        raise SystemExit('As senhas não coincidem.')
    try:
        create_user(username, password, 'admin', first=True)
    except ValueError as error:
        raise SystemExit(str(error))
    print('Administrador criado. Abra o Monitor Impacto e faça login.')
