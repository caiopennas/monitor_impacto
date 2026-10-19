import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults
import accounts
import local_app
import server

class LocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patch=patch.object(accounts,'DB',pathlib.Path(self.tmp.name)/'test.sqlite3')
        self.patch.start()
        accounts.initialize()
        accounts.create_user('localadmin','local-test-password','admin',first=True)
    def tearDown(self):
        self.patch.stop();self.tmp.cleanup()
    def request(self,path,method='GET',data=None,cookie='',host='localhost:8130',origin='http://localhost:8130'):
        env={};setup_testing_defaults(env)
        body=json.dumps(data).encode() if data is not None else b''
        env.update(REQUEST_METHOD=method,PATH_INFO=path,CONTENT_LENGTH=str(len(body)),CONTENT_TYPE='application/json',HTTP_HOST=host,HTTP_ORIGIN=origin,HTTP_COOKIE=cookie,REMOTE_ADDR='127.0.0.1')
        env['wsgi.input']=io.BytesIO(body)
        result={}
        def start(status,headers):result.update(status=int(status.split()[0]),headers=dict(headers))
        result['body']=b''.join(local_app.application(env,start))
        return result
    def test_health_and_protected_routes(self):
        self.assertEqual(self.request('/health')['status'],200)
        self.assertEqual(self.request('/api/printers')['status'],401)
        self.assertEqual(self.request('/api/camera/any')['status'],401)
        self.assertEqual(self.request('/')['status'],303)
    def test_login_edit_and_logout_through_wsgi(self):
        r=self.request('/api/login','POST',{'username':'localadmin','password':'local-test-password'})
        self.assertEqual(r['status'],200,r)
        cookie=r['headers']['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly',r['headers']['Set-Cookie'])
        self.assertEqual(self.request('/api/me',cookie=cookie)['status'],200)
        with patch.object(server,'CONFIG',pathlib.Path(self.tmp.name)/'printers.json'),patch.object(server,'PRINTERS',[]):
            r=self.request('/api/printers','POST',{'name':'Teste','model':'K1 Max','ip':'192.168.1.22'},cookie)
            self.assertEqual(r['status'],200,r)
            self.assertEqual(len(json.loads(self.request('/api/printers',cookie=cookie)['body'])['printers']),1)
        self.assertEqual(self.request('/api/logout','POST',{},cookie)['status'],200)
        self.assertEqual(self.request('/api/me',cookie=cookie)['status'],401)
    def test_host_origin_and_method_restrictions(self):
        self.assertEqual(self.request('/login',host='evil.example')['status'],403)
        self.assertEqual(self.request('/api/login','POST',{},origin='http://evil.example')['status'],403)
        self.assertEqual(self.request('/login','DELETE')['status'],405)
    def test_body_limit(self):
        self.assertEqual(self.request('/api/login','POST',{'blob':'x'*17000})['status'],413)

if __name__=='__main__':unittest.main()
