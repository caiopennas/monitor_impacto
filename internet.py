"""Supervise a temporary HTTPS tunnel and the authenticated notebook server."""
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import accounts

ROOT = pathlib.Path(__file__).resolve().parent
DATA = accounts.DATA
STATE = DATA / 'internet-state.json'
STOP = DATA / 'parar-internet.flag'
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

def status(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code

def main():
    DATA.mkdir(parents=True, exist_ok=True)
    executable = DATA / 'bin' / 'cloudflared.exe'
    if not executable.exists():
        raise RuntimeError('Instale cloudflared.exe oficial em '+str(executable))
    accounts.initialize()
    with accounts.connection() as db:
        if not db.execute("SELECT 1 FROM users WHERE active=1 AND role='admin'").fetchone():
            raise RuntimeError('Crie o primeiro administrador antes de publicar.')
    # Refuse to tunnel an unknown or older service already bound to the port.
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 8130)) == 0:
            raise RuntimeError('A porta 8130 está ocupada. Feche a instância anterior do monitor.')
    if STOP.exists():
        STOP.unlink()
    STATE.write_text(json.dumps({'status':'starting'}), encoding='utf-8')
    children = []
    try:
        with (DATA/'tunnel.log').open('w', encoding='utf-8') as tunnel_log, (DATA/'internet-server.log').open('w', encoding='utf-8') as server_log:
            tunnel = subprocess.Popen([str(executable), 'tunnel', '--no-autoupdate', '--protocol', 'http2', '--url', 'http://127.0.0.1:8130'], stdout=tunnel_log, stderr=subprocess.STDOUT, creationflags=HIDDEN)
            children.append(tunnel)
            origin = None
            for _ in range(60):
                if tunnel.poll() is not None:
                    raise RuntimeError('O túnel encerrou. Consulte tunnel.log.')
                text = (DATA/'tunnel.log').read_text(encoding='utf-8', errors='replace')
                match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', text)
                if match:
                    origin = match.group(0)
                    break
                time.sleep(1)
            if not origin:
                raise RuntimeError('A Cloudflare não forneceu um endereço. Consulte tunnel.log.')
            env = dict(os.environ, IMPACTO_PUBLIC_ORIGIN=origin)
            python = pathlib.Path(sys.executable).with_name('python.exe') if os.name == 'nt' else pathlib.Path(sys.executable)
            app = subprocess.Popen([str(python), str(ROOT/'server.py')], cwd=ROOT, env=env, stdout=server_log, stderr=subprocess.STDOUT, creationflags=HIDDEN)
            children.append(app)
            ready = False
            for _ in range(30):
                if app.poll() is not None:
                    raise RuntimeError('O painel não iniciou. Consulte internet-server.log.')
                try:
                    if status('http://127.0.0.1:8130/login') == 200 and status('http://127.0.0.1:8130/api/printers') == 401:
                        ready = True
                        break
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(1)
            if not ready:
                raise RuntimeError('Não foi possível confirmar a proteção do painel.')
            STATE.write_text(json.dumps({'status':'running', 'url':origin, 'supervisor_pid':os.getpid(), 'server_pid':app.pid, 'tunnel_pid':tunnel.pid}), encoding='utf-8')
            (DATA/'Endereco-internet.txt').write_text(origin+'\n\nEndereço temporário. Funciona enquanto o notebook e o acesso à internet estiverem ligados.\n', encoding='utf-8-sig')
            while not STOP.exists():
                if any(child.poll() is not None for child in children):
                    raise RuntimeError('Painel ou túnel encerrado. Inicie novamente para gerar um novo endereço.')
                time.sleep(2)
    except Exception as error:
        STATE.write_text(json.dumps({'status':'error','message':str(error)}), encoding='utf-8')
        raise
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
        if STOP.exists():
            STATE.write_text(json.dumps({'status':'stopped'}), encoding='utf-8')
            STOP.unlink()

if __name__ == '__main__':
    main()
