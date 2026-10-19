"""Local LAN deployment: Waitress handles HTTP; reuse audited application routes."""
import email.parser
import io
import json
import logging
import os
import socket
import threading
import time
from http import HTTPStatus
from pathlib import Path

import accounts
import server
from access import authenticated_handler

SETTINGS = Path(os.environ.get('IMPACTO_LOCAL_SETTINGS', str(accounts.DATA/'local-settings.json')))
settings = json.loads(SETTINGS.read_text(encoding='utf-8-sig')) if SETTINGS.exists() else {}
PORT = int(settings.get('port', 8130))
server.PORT = PORT
hosts = {f'localhost:{PORT}', f'127.0.0.1:{PORT}', f'{socket.gethostname().lower()}:{PORT}'}
hosts.update(f'{str(h).lower()}:{PORT}' for h in settings.get('hosts', []))

class LocalHandler(authenticated_handler(server.Handler, server.ROOT)):
    def allowed(self):
        return self.headers.get('Host', '').lower() in hosts

    def do_GET(self):
        if self.path == '/health' and self.allowed():
            return self.reply({'service':'impacto-local','ok':True})
        return super().do_GET()

    def do_POST(self):
        if self.path == '/api/bambu-studio':
            if not self.valid_origin():
                return self.reply({'error':'Origem não permitida.'}, status=403)
            if not self.identity():
                return self.reply({'error':'Faça login.'}, status=401)
            return self.reply({'error':'O serviço não abre programas na área de trabalho. Abra o Bambu Studio manualmente no seu computador.'}, status=409)
        return super().do_POST()

def application(environ, start_response):
    """Adapt WSGI to the route methods only, without parsing raw HTTP or sockets."""
    try:
        method = environ['REQUEST_METHOD']
        if method not in ('GET', 'POST'):
            start_response('405 Method Not Allowed', [('Content-Length','0'), ('Allow','GET, POST')])
            return [b'']
        headers = email.parser.Parser().parsestr('')
        for key, value in environ.items():
            if key.startswith('HTTP_'):
                headers[key[5:].replace('_','-')] = value
        length = int(environ.get('CONTENT_LENGTH') or 0)
        if length > 16_384 or length < 0:
            start_response('413 Content Too Large', [('Content-Length','0')])
            return [b'']
        headers['Content-Length'] = str(length)
        headers['Content-Type'] = environ.get('CONTENT_TYPE', '')
        handler = LocalHandler.__new__(LocalHandler)
        handler.headers = headers
        handler.path = environ.get('PATH_INFO', '/') + ('?'+environ['QUERY_STRING'] if environ.get('QUERY_STRING') else '')
        handler.command = method
        handler.request_version = 'HTTP/1.1'
        handler.requestline = method+' '+handler.path+' HTTP/1.1'
        handler.client_address = (environ.get('REMOTE_ADDR',''), 0)
        handler.rfile = io.BytesIO(environ['wsgi.input'].read(length))
        handler.wfile = io.BytesIO()
        handler.close_connection = True
        getattr(handler, 'do_'+method)()
        head, body = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
        status_line, raw_headers = head.split(b'\r\n', 1)
        code = int(status_line.split()[1])
        response_headers = email.parser.Parser().parsestr(raw_headers.decode('latin-1'))
        start_response(f'{code} {HTTPStatus(code).phrase}', list(response_headers.items()))
        return [body]
    except Exception:
        logging.exception('Falha ao atender requisição')
        body = b'{"error":"Falha interna. Consulte os logs do servico."}'
        start_response('500 Internal Server Error', [('Content-Type','application/json'),('Content-Length',str(len(body))),('Cache-Control','no-store')])
        return [body]

def resilient_worker(function):
    while True:
        try:
            function()
        except Exception:
            logging.exception('Coleta interrompida; tentando novamente')
        time.sleep(5)

if __name__ == '__main__':
    from waitress import serve
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    accounts.initialize()
    for function in (server.polling, server.camera_polling):
        threading.Thread(target=resilient_worker, args=(function,), daemon=True).start()
    logging.info('Monitor Impacto Local escutando na porta %s', PORT)
    serve(application, host='0.0.0.0', port=PORT, threads=12, connection_limit=100,
          channel_timeout=30, max_request_header_size=16_384, max_request_body_size=16_384,
          clear_untrusted_proxy_headers=True, expose_tracebacks=False)
