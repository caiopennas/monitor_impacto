import http.client
import json
import pathlib
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import accounts
import access
import server

class AccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dbpatch = patch.object(accounts, 'DB', pathlib.Path(cls.tmp.name)/'accounts.sqlite3')
        cls.dbpatch.start()
        accounts.initialize()
        cls.admin = accounts.create_user('admin', 'admin-test-password', 'admin', first=True)
        cls.viewer = accounts.create_user('viewer', 'viewer-test-password', 'viewer')
        cls.admin_token = accounts.login('admin', 'admin-test-password', 'initial-admin')
        cls.viewer_token = accounts.login('viewer', 'viewer-test-password', 'initial-viewer')
        cls.httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), access.authenticated_handler(server.Handler, server.ROOT))
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        cls.dbpatch.stop()
        cls.tmp.cleanup()

    def request(self, method, path, data=None, token=None, origin='http://127.0.0.1:8130', host='127.0.0.1:8130'):
        client = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=10)
        headers = {'Host':host, 'Origin':origin, 'Content-Type':'application/json'}
        if token:
            headers['Cookie'] = 'impacto_session='+token
        client.request(method, path, json.dumps(data) if data is not None else None, headers)
        response = client.getresponse()
        result = (response.status, dict(response.getheaders()), response.read())
        client.close()
        return result

    def test_anonymous_cannot_read_dashboard_telemetry_camera_or_users(self):
        for path in ('/api/printers', '/api/camera/bambu-mini', '/api/users', '/api/audit', '/api/me'):
            self.assertEqual(self.request('GET', path)[0], 401, path)
        self.assertEqual(self.request('GET', '/')[0], 303)
        self.assertEqual(self.request('GET', '/login')[0], 200)

    def test_viewer_permissions_enforced_at_server(self):
        self.assertEqual(self.request('GET', '/api/printers', token=self.viewer_token)[0], 200)
        for path in ('/admin', '/api/users', '/api/audit'):
            self.assertEqual(self.request('GET', path, token=self.viewer_token)[0], 403)
        for path in ('/api/users', '/api/printers', '/api/discover', '/api/bambu-studio'):
            self.assertEqual(self.request('POST', path, {}, self.viewer_token)[0], 403, path)

    def test_origin_host_and_cookie(self):
        self.assertEqual(self.request('POST', '/api/login', {}, origin='https://evil.example')[0], 403)
        self.assertEqual(self.request('GET', '/login', host='evil.example')[0], 403)
        status, headers, _ = self.request('POST', '/api/login', {'username':'viewer', 'password':'viewer-test-password'})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        with patch.object(access, 'PUBLIC_ORIGIN', 'https://monitor.example.com'):
            status, headers, _ = self.request('POST', '/api/login', {'username':'viewer','password':'viewer-test-password'}, origin='https://monitor.example.com', host='monitor.example.com')
            self.assertEqual(status, 200)
            self.assertIn('; Secure', headers['Set-Cookie'])
            with patch.object(server, 'save_printer', return_value='test-printer'):
                status, _, _ = self.request('POST', '/api/printers', {'name':'test'}, self.admin_token, origin='https://monitor.example.com', host='monitor.example.com')
                self.assertEqual(status, 200)

    def test_admin_can_manage_users_and_audit_without_password_leak(self):
        status, _, body = self.request('POST', '/api/users', {'username':'new-user','password':'test-password-1234','role':'viewer'}, self.admin_token)
        self.assertEqual(status, 200, body)
        status, _, body = self.request('GET', '/api/users', token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertNotIn(b'password', body)
        status, _, body = self.request('GET', '/api/audit?actor=admin', token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertTrue(all(r['actor']=='admin' for r in json.loads(body)))
        self.assertNotIn(b'test-password-1234', body)

    def test_disable_and_password_change_revoke_sessions(self):
        uid=accounts.create_user('revoked', 'initial-password-123', 'viewer')
        token=accounts.login('revoked', 'initial-password-123', 'revocation-test')
        accounts.update_user(uid, {'active':False}, 'admin')
        self.assertIsNone(accounts.session(token))
        accounts.update_user(uid, {'active':True}, 'admin')
        token=accounts.login('revoked', 'initial-password-123', 'revocation-test')
        user=accounts.session(token)
        accounts.change_password(user, 'initial-password-123', 'new-password-1234')
        self.assertIsNone(accounts.session(token))
        with self.assertRaises(ValueError): accounts.login('revoked', 'initial-password-123', 'revocation-test')
        self.assertIsNotNone(accounts.login('revoked', 'new-password-1234', 'revocation-test'))

    def test_last_admin_and_bootstrap_protected(self):
        with self.assertRaises(ValueError): accounts.update_user(self.admin, {'active':False}, 'admin')
        with self.assertRaises(ValueError): accounts.update_user(self.admin, {'role':'viewer'}, 'admin')
        with self.assertRaises(ValueError): accounts.create_user('second', 'test-password-123', 'admin', first=True)

    def test_expiry_logout_and_rate_limit(self):
        token=accounts.login('viewer', 'viewer-test-password', 'expiry-test')
        with patch.object(accounts.time, 'time', return_value=time.time()+accounts.TTL+1):
            self.assertIsNone(accounts.session(token))
        accounts.logout(token, 'viewer')
        self.assertIsNone(accounts.session(token))
        for _ in range(10):
            with self.assertRaises(ValueError): accounts.login('not-a-user', 'bad-password-123', 'rate-test')
        with self.assertRaisesRegex(ValueError, 'Muitas tentativas'):
            accounts.login('not-a-user', 'bad-password-123', 'rate-test')

    def test_invalid_body_and_audit_of_actual_result(self):
        self.assertEqual(self.request('POST', '/api/users', [], self.admin_token)[0], 400)
        with patch.object(server, 'save_printer', side_effect=ValueError('inválido')):
            self.assertEqual(self.request('POST', '/api/printers', {}, self.admin_token)[0], 400)
        with accounts.connection() as db:
            row=db.execute("SELECT result FROM audit WHERE action='impressora.salvar' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(row['result'], 'recusado')

if __name__ == '__main__':
    unittest.main()
