"""Authentication boundary for every dashboard, telemetry and camera request."""
import http.cookies
import json
import os
import urllib.parse
import accounts

PUBLIC_ORIGIN = os.environ.get('IMPACTO_PUBLIC_ORIGIN', '').rstrip('/')
if PUBLIC_ORIGIN:
    parsed = urllib.parse.urlsplit(PUBLIC_ORIGIN)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('IMPACTO_PUBLIC_ORIGIN deve ser uma origem HTTPS, sem caminho.')

def authenticated_handler(base, root):
    class AuthHandler(base):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def allowed(self):
            return super().allowed() or bool(PUBLIC_ORIGIN and self.headers.get('Host') == urllib.parse.urlsplit(PUBLIC_ORIGIN).netloc)

        def expected_origin(self):
            host = self.headers.get('Host', '')
            if PUBLIC_ORIGIN and host == urllib.parse.urlsplit(PUBLIC_ORIGIN).netloc:
                return PUBLIC_ORIGIN
            return 'http://' + host

        def valid_origin(self):
            return self.allowed() and self.headers.get('Origin') == self.expected_origin()

        def cookie(self, token='', clear=False):
            value = 'impacto_session=' + token + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=' + ('0' if clear else str(accounts.TTL))
            if self.expected_origin().startswith('https:'):
                value += '; Secure'
            self.pending_cookie = value

        def end_headers(self):
            if getattr(self, 'pending_cookie', None):
                self.send_header('Set-Cookie', self.pending_cookie)
                self.pending_cookie = None
            self.send_header('Referrer-Policy', 'same-origin')
            super().end_headers()

        def identity(self):
            cookies = http.cookies.SimpleCookie()
            try:
                cookies.load(self.headers.get('Cookie', ''))
                token = cookies['impacto_session'].value if 'impacto_session' in cookies else ''
            except http.cookies.CookieError:
                token = ''
            self.token = token
            return accounts.session(token)

        def body(self):
            size = int(self.headers.get('Content-Length', '0'))
            if self.headers.get('Transfer-Encoding') or not 0 < size <= 16_384:
                raise ValueError('Requisição inválida.')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('Requisição inválida.')
            return data

        def do_GET(self):
            if not self.allowed():
                return self.reply({'error':'Host não permitido'}, status=403)
            if self.path in ('/login', '/account.js', '/account.css'):
                name, mime = {'/login':('login.html','text/html'), '/account.js':('account.js','text/javascript'), '/account.css':('account.css','text/css')}[self.path]
                return self.reply((root/name).read_bytes(), mime+'; charset=utf-8')
            user = self.identity()
            if not user:
                if self.path in ('/', '/index.html', '/admin'):
                    self.send_response(303)
                    self.send_header('Location', '/login')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', '0')
                    return self.end_headers()
                return self.reply({'error':'Faça login para continuar.'}, status=401)
            if self.path == '/api/me':
                return self.reply(user)
            if self.path in ('/admin', '/api/users') or self.path.startswith('/api/audit'):
                if user['role'] != 'admin':
                    return self.reply({'error':'Acesso exclusivo do administrador.'}, status=403)
                if self.path == '/admin':
                    return self.reply((root/'admin.html').read_bytes(), 'text/html; charset=utf-8')
                with accounts.connection() as db:
                    if self.path == '/api/users':
                        rows = db.execute('SELECT id,username,role,active FROM users ORDER BY username').fetchall()
                    else:
                        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                        actor = query.get('actor', [''])[0][:60]
                        try:
                            before = int(query.get('before', ['9223372036854775807'])[0])
                        except ValueError:
                            return self.reply({'error':'Página inválida.'}, status=400)
                        before = max(0, min(before, 9223372036854775807))
                        rows = db.execute('SELECT * FROM audit WHERE id<? AND (?=\'\' OR actor=?) ORDER BY id DESC LIMIT 100', (before, actor, actor)).fetchall()
                return self.reply([dict(row) for row in rows])
            if self.path in ('/', '/index.html'):
                accounts.event(user['username'], 'painel.aberto')
            return super().do_GET()

        def do_POST(self):
            if not self.valid_origin():
                return self.reply({'error':'Origem não permitida.'}, status=403)
            try:
                if self.path == '/api/login':
                    data = self.body()
                    token = accounts.login(data.get('username', ''), data.get('password', ''), self.client_address[0])
                    self.cookie(token)
                    return self.reply({'ok':True})
                user = self.identity()
                if not user:
                    return self.reply({'error':'Faça login para continuar.'}, status=401)
                actor = user['username']
                if self.path == '/api/logout':
                    accounts.logout(self.token, actor)
                    self.cookie(clear=True)
                    return self.reply({'ok':True})
                if self.path == '/api/password':
                    data = self.body()
                    accounts.change_password(user, data.get('current'), data.get('password'))
                    self.cookie(clear=True)
                    return self.reply({'ok':True})
                if self.path == '/api/activity':
                    data = self.body()
                    if data.get('action') != 'camera.ampliada':
                        raise ValueError('Atividade inválida.')
                    # Supplemental UI event: explicitly labelled, not proof of a printer command.
                    accounts.event(actor, 'interface.camera.ampliada', str(data.get('target', ''))[:100])
                    return self.reply({'ok':True})
                if user['role'] != 'admin':
                    accounts.event(actor, 'acesso.recusado', self.path, 'recusado')
                    return self.reply({'error':'Acesso exclusivo do administrador.'}, status=403)
                if self.path == '/api/users':
                    data = self.body()
                    accounts.create_user(data.get('username'), data.get('password'), data.get('role'), actor)
                    return self.reply({'ok':True})
                if self.path.startswith('/api/users/'):
                    user_id = int(self.path.rsplit('/', 1)[-1])
                    accounts.update_user(user_id, self.body(), actor)
                    return self.reply({'ok':True})
                # Existing backend validates printer fields; record its actual HTTP result.
                self.audit_actor = actor
                self.audit_action = ('impressora.salvar' if self.path.startswith('/api/printers') else 'rede.buscar' if self.path == '/api/discover' else 'bambu.abrir' if self.path == '/api/bambu-studio' else None)
                return super().do_POST()
            except (ValueError, TypeError, UnicodeDecodeError) as error:
                return self.reply({'error':str(error)}, status=400)

        def reply(self, data, content_type='application/json; charset=utf-8', status=200):
            action = getattr(self, 'audit_action', None)
            if action:
                target = data.get('id', self.path) if isinstance(data, dict) else self.path
                accounts.event(self.audit_actor, action, target, 'ok' if status < 400 else 'recusado')
                self.audit_action = None
            return super().reply(data, content_type, status)
    return AuthHandler
