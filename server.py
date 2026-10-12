"""Local printer fleet. Reads telemetry; never sends printing or heating commands."""
import concurrent.futures
import copy
import http.server
import json
import ipaddress
import os
import struct
import uuid
import urllib.parse
import pathlib
import socket
import ssl
import subprocess
import threading
import time
import urllib.request

import paho.mqtt.client as mqtt
import websocket

ROOT = pathlib.Path(__file__).resolve().parent
PORT = 8130
LOCK = threading.RLock()
SCAN_LOCK = threading.Lock()
CONFIG = ROOT / 'printers.json'
PRINTERS = json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else []
LIVE = {}
SCANNING = False
BAMBU_CLIENTS = {}
CAMERAS = {}
SECRETS = pathlib.Path(os.environ.get("LOCALAPPDATA", str(ROOT))) / "ImpactoMonitor" / "printer-secrets.json"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def allowed_hosts():
    hosts = {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}
    # Optional office deployment: the app stays on loopback; Tailscale Serve
    # forwards its private TCP listener. Never allow arbitrary Host headers.
    try:
        settings = json.loads((ROOT/'remote-access.json').read_text(encoding='utf-8-sig'))
        address = ipaddress.IPv4Address(settings.get('tailscale_ipv4', ''))
        if address in ipaddress.ip_network('100.64.0.0/10'):
            hosts.add(f'{address}:{PORT}')
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return hosts

def get(url, timeout=3):
    with OPENER.open(url, timeout=timeout) as r:
        return json.loads(r.read(500_000))

def put(id, **data):
    with LOCK:
        LIVE[id] = {**LIVE.get(id, {}), **data}

def put_for(p, **data):
    with LOCK:
        current = next((x for x in PRINTERS if x['id'] == p['id']), None)
        if current and current.get('_revision', 0) == p.get('_revision', 0):
            put(p['id'], **data)

def access_code(p):
    if SECRETS.exists():
        saved = json.loads(SECRETS.read_text(encoding='utf-8')).get(p['id'])
        if isinstance(saved, dict) and saved.get('serial') == p.get('serial'):
            return saved.get('code')
    config = pathlib.Path.home()/'AppData/Roaming/BambuStudio/BambuStudio.conf'
    if config.exists():
        d = json.JSONDecoder().raw_decode(config.read_text(encoding='utf-8-sig'))[0]
        return d.get('access_code', {}).get(p.get('serial')) or d.get('user_access_code', {}).get(p.get('serial'))

def save_config():
    temp = CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps(PRINTERS, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(CONFIG)

def save_printer(data, printer_id=None):
    if not isinstance(data, dict):
        raise ValueError('Cadastro inválido.')
    name = str(data.get('name', '')).strip()
    model = data.get('model')
    ip = str(data.get('ip', '')).strip()
    serial = str(data.get('serial', '')).strip().upper()
    code = str(data.get('access_code', '')).strip()
    if not 1 <= len(name) <= 60:
        raise ValueError('Informe um nome com até 60 caracteres.')
    if model not in ('K1 Max', 'A1 mini'):
        raise ValueError('Modelo não suportado.')
    if ip:
        try:
            addr = ipaddress.IPv4Address(ip)
            if not any(addr in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):
                raise ValueError()
        except ValueError:
            raise ValueError('Informe o endereço IPv4 da impressora na rede local.')
    if model == 'A1 mini' and ip and (not serial.isalnum() or not 6 <= len(serial) <= 64):
        raise ValueError('Informe o número de série da A1 mini.')
    if code and (len(code)>32 or not code.isascii()):
        raise ValueError('Código de acesso inválido.')
    with LOCK:
        if SCANNING:
            raise ValueError('Aguarde a busca de impressoras terminar antes de salvar.')
        old = next((p for p in PRINTERS if p['id'] == printer_id), None)
        if printer_id and not old:
            raise ValueError('Impressora não encontrada.')
        control_port = int(data.get('control_port', (old or {}).get('control_port', 80)))
        if control_port not in (80, 4408, 4409):
            raise ValueError('Interface de controle inválida.')
        if ip and any(p.get('ip') == ip and p['id'] != printer_id for p in PRINTERS):
            raise ValueError('Este IP já está cadastrado em outra impressora.')
        if serial and model == 'A1 mini' and any(p.get('serial') == serial and p['id'] != printer_id for p in PRINTERS):
            raise ValueError('Este número de série já está cadastrado.')
        p = {**(old or {}), 'id':printer_id or uuid.uuid4().hex, 'name':name, 'model':model,
             'ip':ip, 'serial':serial if model == 'A1 mini' else '',
             'control_port':control_port,
             'camera_enabled': bool(data.get('camera_enabled', True)), '_revision':(old or {}).get('_revision',0)+1}
        if not old or old.get('ip') != ip or old.get('model') != model:
            p.pop('mac', None)
        if code and model == 'A1 mini':
            SECRETS.parent.mkdir(parents=True, exist_ok=True)
            codes = json.loads(SECRETS.read_text(encoding='utf-8')) if SECRETS.exists() else {}
            codes[p['id']] = {'serial':serial, 'code':code}
            tmp = SECRETS.with_suffix('.tmp')
            tmp.write_text(json.dumps(codes), encoding='utf-8')
            tmp.replace(SECRETS)
        if old:
            PRINTERS[PRINTERS.index(old)] = p
        else:
            PRINTERS.append(p)
        save_config()
        LIVE.pop(p['id'], None)
        CAMERAS.pop(p['id'], None)
        return p['id']

def reconcile_bambu(printers):
    for p in printers:
        if p['model'] != 'A1 mini':
            if p['id'] in BAMBU_CLIENTS:
                _, client = BAMBU_CLIENTS.pop(p['id'])
                client.disconnect()
                client.loop_stop()
            continue
        existing = BAMBU_CLIENTS.get(p['id'])
        if existing and existing[0] != p.get('_revision',0):
            _, client = BAMBU_CLIENTS.pop(p['id'])
            client.disconnect()
            client.loop_stop()
        if p['id'] not in BAMBU_CLIENTS and p.get('ip'):
            bambu_connect(p)

def camera_capture(p):
    if not p.get('ip') or not p.get('camera_enabled', True):
        return
    with LOCK:
        if not LIVE.get(p['id'], {}).get('online'):
            return
    try:
        if p['model'] == 'K1 Max':
            with OPENER.open(f"http://{p['ip']}:8080/?action=snapshot", timeout=3) as r:
                data = r.read(2_000_000)
        else:
            code = access_code(p)
            if not code:
                return
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection((p['ip'],6000),timeout=4) as raw:
                with ctx.wrap_socket(raw,server_hostname=p['ip']) as sock:
                    sock.settimeout(5)
                    sock.sendall(struct.pack('<IIII32s32s',0x40,0x3000,0,0,b'bblp',code.encode('ascii')))
                    def exact(count):
                        data = bytearray()
                        while len(data)<count:
                            chunk = sock.recv(count-len(data))
                            if not chunk:
                                raise OSError('Camera disconnected')
                            data.extend(chunk)
                        return bytes(data)
                    size = struct.unpack('<I',exact(16)[:4])[0]
                    if not 4<size<2_000_000:
                        return
                    data = exact(size)
        if data[:2] == b'\xff\xd8' and data[-2:] == b'\xff\xd9':
            with LOCK:
                current = next((x for x in PRINTERS if x['id'] == p['id']), None)
                if current and current.get('_revision',0) == p.get('_revision',0):
                    CAMERAS[p['id']] = (time.time(), data)
    except Exception:
        pass

def camera_polling():
    while True:
        with LOCK:
            printers = copy.deepcopy(PRINTERS)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(camera_capture, printers))
        time.sleep(3)

def poll_k1(p):
    ip = p.get('ip')
    if not ip:
        return
    try:
        identity = get(f'http://{ip}/info')
        if identity.get('model') != 'K1 Max' or not identity.get('mac') or (p.get('mac') and identity.get('mac') != p['mac']):
            raise ValueError('Identity changed')
        if not p.get('mac'):
            with LOCK:
                current = next((x for x in PRINTERS if x['id'] == p['id']), None)
                if current and current.get('_revision',0) == p.get('_revision',0):
                    current['mac'] = identity['mac']
                    save_config()
    except Exception:
        put_for(p, online=False, state='Sem resposta', nozzle=None, bed=None, progress=None, file='', message='Confira a energia e a conexão de rede.')
        return
    try:
        info = get(f'http://{ip}:7125/printer/info')['result']
        status = get(f'http://{ip}:7125/printer/objects/query?print_stats&display_status&extruder&heater_bed')['result']['status']
        stats = status.get('print_stats', {})
        raw = stats.get('state')
        state = {'standby':'Em espera','printing':'Imprimindo','paused':'Pausada','complete':'Concluída','cancelled':'Cancelada','error':'Erro'}.get(raw, 'Estado indisponível')
        put_for(p, online=True, device_name=info.get('hostname', p['name']), state=state,
            nozzle=status.get('extruder', {}).get('temperature'), bed=status.get('heater_bed', {}).get('temperature'),
            progress=round(status.get('display_status', {}).get('progress', 0)*100),
            file=stats.get('filename', ''), message=status.get('display_status', {}).get('message') or stats.get('message') or '', updated=time.time())
    except Exception:
        try:
            ws = websocket.create_connection(f'ws://{ip}:9999', timeout=3, http_no_proxy=[ip])
            try:
                data = json.loads(ws.recv())
            finally:
                ws.close()
            # Enum verified in this printer's own Creality web controller.
            state = {0:'Parada',1:'Imprimindo',2:'Concluída',3:'Falha',4:'Cancelada',5:'Pausada'}.get(data.get('state'), 'Estado indisponível')
            put_for(p, online=True, device_name=data.get('hostname', p['name']), state=state,
                nozzle=float(data['nozzleTemp']) if 'nozzleTemp' in data else None,
                bed=float(data['bedTemp0']) if 'bedTemp0' in data else None,
                progress=data.get('printProgress'), file=data.get('printFileName', '').split('/')[-1],
                message='', updated=time.time())
        except Exception:
            put_for(p, online=True, state='Telemetria indisponível', nozzle=None, bed=None, progress=None, file='', message='O controle web está acessível.', updated=time.time())

def bambu_connect(p):
    config = pathlib.Path.home()/'AppData/Roaming/BambuStudio/BambuStudio.conf'
    try:
        serial = p.get('serial', '')
        code = access_code(p)
        if not code:
            raise ValueError('No saved access code')
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set('bblp', code)
        # Bambu LAN certificates are device-local; pinned IP/serial from discovery.
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        def connected(c, u, flags, reason, properties):
            if reason.is_failure:
                put_for(p, online=False, state='Conexão recusada')
                return
            c.subscribe(f'device/{serial}/report')
            c.publish(f'device/{serial}/request', json.dumps({'pushing': {'sequence_id':'1','command':'pushall'}}))
        def message(c, u, msg):
            try:
                data = json.loads(msg.payload).get('print', {})
                if not data:
                    return
                with LOCK:
                    old = LIVE.get(p['id'], {}).get('_raw', {})
                    data = {**old, **data}
                state = {'IDLE':'Em espera','RUNNING':'Imprimindo','PAUSE':'Pausada','FINISH':'Concluída','FAILED':'Falha','PREPARE':'Preparando'}.get(data.get('gcode_state'), 'Conectada')
                put_for(p, online=True, state=state, nozzle=data.get('nozzle_temper'), bed=data.get('bed_temper'),
                    progress=data.get('mc_percent'), file=data.get('gcode_file', ''), message='', updated=time.time(), _raw=data)
            except (ValueError, TypeError):
                pass
        def disconnected(c, u, flags, reason, properties):
            put_for(p, online=False, state='Sem conexão', nozzle=None, bed=None, progress=None, file='')
        client.on_connect = connected
        client.on_message = message
        client.on_disconnect = disconnected
        client.reconnect_delay_set(3, 30)
        client.connect_async(p['ip'], 8883, 30)
        client.loop_start()
        BAMBU_CLIENTS[p['id']] = (p.get('_revision', 0), client)
    except Exception:
        put_for(p, online=False, state='Configuração pendente', message='Abra o Bambu Studio e confira a conexão local da A1 mini.')

def scan():
    global SCANNING
    if not SCAN_LOCK.acquire(blocking=False):
        return
    SCANNING = True
    try:
        def find_bambu():
            p = next((p for p in PRINTERS if p['model'] == 'A1 mini'), None)
            if not p:
                return
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(('', 2021))
                    for addr in set(socket.gethostbyname_ex(socket.gethostname())[2]):
                        if addr.startswith('192.168.1.'):
                            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton('239.255.255.250')+socket.inet_aton(addr))
                    s.settimeout(1)
                    end = time.monotonic()+12
                    while time.monotonic()<end:
                        try:
                            body, addr = s.recvfrom(8192)
                            headers = dict(line.strip().split(':',1) for line in body.decode(errors='replace').splitlines() if ':' in line)
                            if headers.get('USN','').strip() == p['serial'] and addr[0].startswith('192.168.1.'):
                                return addr[0]
                        except socket.timeout:
                            pass
            except OSError:
                pass
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as listeners:
            bambu_future = listeners.submit(find_bambu)
            bambu_ip = bambu_future.result()
        def probe(i):
            ip = f'192.168.1.{i}'
            try:
                d = get(f'http://{ip}/info', 2)
                if d.get('model') == 'K1 Max' and d.get('mac'):
                    return {'ip':ip, 'mac':d['mac']}
            except Exception:
                pass
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
            found = [d for d in pool.map(probe, range(1, 255)) if d]
        with LOCK:
            bambu = next((p for p in PRINTERS if p['model'] == 'A1 mini'), None)
            bambu_changed = bambu and bambu_ip and bambu_ip != bambu.get('ip')
            if bambu_changed:
                bambu['ip'] = bambu_ip
                bambu['_revision'] = bambu.get('_revision', 0)+1
            for d in found:
                p = next((p for p in PRINTERS if p.get('mac') == d['mac']), None)
                if p is None:
                    p = next((p for p in PRINTERS if p['model'] == 'K1 Max' and not p.get('mac') and p.get('ip') == d['ip']), None)
                if p is None:
                    p = next((p for p in PRINTERS if p['model'] == 'K1 Max' and not p.get('mac') and not p.get('ip')), None)
                if p is not None:
                    if p.get('ip') != d['ip']:
                        p['_revision'] = p.get('_revision', 0)+1
                    p.update(d)
            temp = CONFIG.with_suffix('.tmp')
            temp.write_text(json.dumps(PRINTERS, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(CONFIG)

    finally:
        SCANNING = False
        SCAN_LOCK.release()

def polling():
    while True:
        with LOCK:
            printers = copy.deepcopy(PRINTERS)
        reconcile_bambu(printers)
        with concurrent.futures.ThreadPoolExecutor(max_workers=9) as pool:
            list(pool.map(poll_k1, [p for p in printers if p['model'] == 'K1 Max']))
        time.sleep(10)

def reorder_printers(ids):
    with LOCK:
        if SCANNING:
            raise ValueError('Aguarde a busca na rede terminar.')
        current = {p['id']: p for p in PRINTERS}
        if (not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)
                or len(ids) != len(current) or len(set(ids)) != len(ids) or set(ids) != set(current)):
            raise ValueError('A lista mudou. Atualize o painel e organize novamente.')
        ordered = [current[i] for i in ids]
        temp = CONFIG.with_suffix('.tmp')
        temp.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(CONFIG)
        PRINTERS[:] = ordered

def snapshot():
    with LOCK:
        result = []
        for p in PRINTERS:
            live = {k:v for k,v in LIVE.get(p['id'], {}).items() if not k.startswith('_')}
            d = {**p, 'online':False, 'state':'Não identificada' if not p.get('ip') else 'Conectando', **live}
            d.pop('_revision', None)
            d['camera_ready'] = bool(CAMERAS.get(p['id']) and time.time()-CAMERAS[p['id']][0]<15)
            d['camera_updated'] = CAMERAS.get(p['id'], (None,))[0]
            d.pop('mac', None)
            if d.get('updated') and time.time()-d['updated'] > 60:
                d.update(online=False, state='Dados desatualizados', nozzle=None, bed=None, progress=None, file='')
            result.append(d)
        return {'printers':result, 'scanning':SCANNING, 'time':time.time()}

class Handler(http.server.BaseHTTPRequestHandler):
    def valid_origin(self):
        return self.allowed() and self.headers.get('Origin') == 'http://' + self.headers.get('Host', '')
    def allowed(self):
        return self.headers.get('Host') in allowed_hosts()
    def reply(self, data, content_type='application/json; charset=utf-8', status=200):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if not self.allowed():
            return self.reply({'error':'Host não permitido'}, status=403)
        if self.path == '/api/printers':
            return self.reply(snapshot())
        if self.path.startswith('/api/camera/'):
            printer_id = self.path.split('?',1)[0].rsplit('/',1)[-1]
            with LOCK:
                camera = CAMERAS.get(printer_id)
            if camera and time.time()-camera[0]<15:
                return self.reply(camera[1], 'image/jpeg')
            return self.reply({'error':'Câmera indisponível'}, status=404)
        if self.path in ('/', '/index.html'):
            return self.reply((ROOT/'index.html').read_bytes(), 'text/html; charset=utf-8')
        self.reply({'error':'Não encontrado'}, status=404)
    def do_POST(self):
        if not self.valid_origin():
            return self.reply({'error':'Origem não permitida'}, status=403)
        if self.path == '/api/printers' or self.path.startswith('/api/printers/'):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 16_384:
                    raise ValueError('Cadastro inválido.')
                data = json.loads(self.rfile.read(size))
                if self.path == '/api/printers/order':
                    if not isinstance(data, dict):
                        raise ValueError('Ordem inválida.')
                    reorder_printers(data.get('ids'))
                    return self.reply({'ok':True})
                printer_id = self.path.rsplit('/',1)[-1] if self.path != '/api/printers' else None
                return self.reply({'ok':True, 'id':save_printer(data, printer_id)})
            except (ValueError, TypeError, UnicodeDecodeError) as e:
                return self.reply({'error':str(e)}, status=400)
        if self.path == '/api/discover':
            threading.Thread(target=scan, daemon=True).start()
            return self.reply({'ok':True})
        if self.path == '/api/bambu-studio':
            if self.headers.get('Host') not in (f'127.0.0.1:{PORT}', f'localhost:{PORT}'):
                return self.reply({'error':'O Bambu Studio abre no computador do escritório. Para comandar a A1 mini de fora, use Bambu Handy ou acesso remoto à área de trabalho.'}, status=409)
            exe = pathlib.Path('C:/Program Files/Bambu Studio/bambu-studio.exe')
            if not exe.exists():
                return self.reply({'error':'Bambu Studio não encontrado neste computador.'}, status=404)
            subprocess.Popen([str(exe)])
            return self.reply({'ok':True})
        self.reply({'error':'Não encontrado'}, status=404)
    def log_message(self, fmt, *args):
        pass

if __name__ == '__main__':
    import accounts
    from access import authenticated_handler
    accounts.initialize()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), authenticated_handler(Handler, ROOT))
    threading.Thread(target=camera_polling, daemon=True).start()
    threading.Thread(target=polling, daemon=True).start()
    print(f'Impacto Monitor: http://127.0.0.1:{PORT}', flush=True)
    server.serve_forever()
