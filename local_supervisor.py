"""Restart the local worker after an exit or repeated failed health probes."""
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    root = Path(__file__).resolve().parent
    settings = json.loads(Path(os.environ['IMPACTO_LOCAL_SETTINGS']).read_text(encoding='utf-8-sig'))
    url = 'http://127.0.0.1:'+str(settings['port'])+'/health'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        child = subprocess.Popen([sys.executable, '-u', str(root/'local_app.py')], cwd=root)
        logging.info('Aplicativo iniciado PID %s', child.pid)
        try:
            failures = 0
            time.sleep(5)
            while child.poll() is None:
                try:
                    with opener.open(url, timeout=5) as response:
                        healthy = response.status == 200 and json.load(response).get('service') == 'impacto-local'
                    failures = 0 if healthy else failures+1
                except Exception:
                    failures += 1
                if failures >= 3:
                    logging.error('Aplicativo sem resposta; reiniciando PID %s', child.pid)
                    break
                time.sleep(10)
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
        logging.warning('Aplicativo encerrado; reinício em 5 segundos')
        time.sleep(5)

if __name__ == '__main__':
    main()
