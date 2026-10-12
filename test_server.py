import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import server

class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.patches = [patch.object(server, 'CONFIG', base/'printers.json'),
                        patch.object(server, 'SECRETS', base/'secrets.json'),
                        patch.object(server, 'PRINTERS', []), patch.object(server, 'LIVE', {}),
                        patch.object(server, 'CAMERAS', {}), patch.object(server, 'SCANNING', False)]
        for p in self.patches:
            p.start()
    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()
    def test_create_edit_persist_and_no_duplicate(self):
        pid = server.save_printer({'name':'Impacto 1','model':'K1 Max','ip':'192.168.1.20'})
        with self.assertRaisesRegex(ValueError, 'já está cadastrado'):
            server.save_printer({'name':'Duplicate','model':'K1 Max','ip':'192.168.1.20'})
        before = dict(server.PRINTERS[0])
        server.save_printer({'name':'Impacto 2','model':'K1 Max','ip':'192.168.1.21','camera_enabled':False},pid)
        server.put_for(before, online=True, state='Old callback')
        self.assertNotIn(pid,server.LIVE)
        saved = json.loads(server.CONFIG.read_text(encoding='utf-8'))
        self.assertEqual(len(saved),1)
        self.assertEqual(saved[0]['name'],'Impacto 2')
        self.assertEqual(saved[0]['ip'],'192.168.1.21')
        self.assertFalse(saved[0]['camera_enabled'])
    def test_secrets_not_in_registry_or_snapshot(self):
        server.save_printer({'name':'Mini','model':'A1 mini','ip':'192.168.1.22','serial':'0300TEST1234','access_code':'TEST1234'})
        self.assertNotIn('TEST1234',server.CONFIG.read_text().replace('0300TEST1234','serial'))
        self.assertNotIn('access_code',json.dumps(server.snapshot()))
        self.assertEqual(list(json.loads(server.SECRETS.read_text()).values()),[{'serial':'0300TEST1234','code':'TEST1234'}])
    def test_invalid_addresses_and_models(self):
        for ip in ('127.0.0.1','8.8.8.8','169.254.169.254','192.168.1.2/path','bad'):
            with self.assertRaises(ValueError):
                server.save_printer({'name':'Test','model':'K1 Max','ip':ip})
        with self.assertRaises(ValueError):
            server.save_printer({'name':'Test','model':'Other'})
        self.assertEqual(server.PRINTERS,[])
    def test_remote_host_requires_explicit_private_configuration(self):
        base = pathlib.Path(self.tmp.name)
        with patch.object(server, 'ROOT', base):
            self.assertEqual(server.allowed_hosts(), {'localhost:8130', '127.0.0.1:8130'})
            for value in ({'tailscale_ipv4':'8.8.8.8'}, [], {'tailscale_ipv4':'100.128.1.2'}):
                (base/'remote-access.json').write_text(json.dumps(value), encoding='utf-8')
                self.assertEqual(len(server.allowed_hosts()), 2)
            (base/'remote-access.json').write_text(json.dumps({'tailscale_ipv4':'100.80.1.2'}), encoding='utf-8-sig')
            self.assertIn('100.80.1.2:8130', server.allowed_hosts())

    def test_scan_locks_configuration_changes(self):
        server.SCANNING = True
        with self.assertRaisesRegex(ValueError,'busca'):
            server.save_printer({'name':'Test','model':'K1 Max'})

if __name__ == '__main__':
    unittest.main()
