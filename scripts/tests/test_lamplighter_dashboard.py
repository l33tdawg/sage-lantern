import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lantern'))
from lamplighter_dashboard import Dashboard
import lamplighter_dashboard_agent as agent


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=Path(self.temp.name)/'token';p.write_text('test-secret')
        self.d=Dashboard({'token_file':str(p),'hosts':[]})
        self.server=self.d.server('127.0.0.1',0);self.host='127.0.0.1:'+str(self.server.server_port)
        self.d.config['hosts']=[self.host]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
    def request(self,path,method='GET',body=None,headers=None):
        c=http.client.HTTPConnection(self.host,timeout=5)
        c.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers or {})
        r=c.getresponse();raw=r.read();c.close();return r.status,json.loads(raw)
    def test_auth_and_origin_required_before_mutation(self):
        payload={'unit':'unit-b','action':'start'}
        with patch.object(self.d,'command') as command:
            self.assertEqual(self.request('/api/jobs','POST',payload)[0],403)
            headers={'Authorization':'Bearer test-secret','Content-Type':'application/json','Origin':'http://evil.example'}
            self.assertEqual(self.request('/api/jobs','POST',payload,headers)[0],403)
            command.assert_not_called()
    def test_valid_operation_routes_exact_unit(self):
        payload={'unit':'unit-b','action':'pause','id':'example'}
        headers={'Authorization':'Bearer test-secret','Content-Type':'application/json','Origin':'http://'+self.host}
        with patch.object(self.d,'command',return_value={'pause_requested':True}) as command:
            self.assertEqual(self.request('/api/jobs','POST',payload,headers)[0],200)
            command.assert_called_once_with('unit-b',{'action':'pause','id':'example'})
    def test_state_needs_token_and_untrusted_host_rejected(self):
        self.assertEqual(self.request('/api/state')[0],401)
        self.assertEqual(self.request('/api/state',headers={'Authorization':'Bearer test-secret'})[0],200)
        self.assertEqual(self.request('/api/state',headers={'Authorization':'Bearer test-secret','Host':'evil.example'})[0],403)
    def test_successful_job_with_null_error_is_accepted(self):
        import subprocess
        self.d.config['python'] = '/usr/bin/python3'
        response = subprocess.CompletedProcess([], 0, '{"state":"running","error":null}', '')
        with patch('lamplighter_dashboard.subprocess.run', return_value=response):
            self.assertEqual(self.d.command('unit-a', {'action':'start'})['state'], 'running')
    def test_kiosk_home_accepts_unit_query_before_authentication(self):
        c=http.client.HTTPConnection(self.host,timeout=5)
        c.request('GET','/?display=unit-a');r=c.getresponse()
        self.assertEqual(r.status,200)
        self.assertIn(b'SAGE / LAMPLIGHTER',r.read());c.close()
    def test_non_object_body_rejected(self):
        headers={'Authorization':'Bearer test-secret','Content-Type':'application/json','Origin':'http://'+self.host}
        self.assertEqual(self.request('/api/jobs','POST',[],headers)[0],400)
    def test_agent_lock_rejects_concurrent_workers(self):
        import os
        with patch.object(agent,'STATE',Path(self.temp.name)):
            fd=agent.lease()
            try:
                with self.assertRaisesRegex(ValueError,'already running'):agent.lease()
            finally:os.close(fd)
    def test_publication_mode_is_public_without_widening_private_default(self):
        import stat
        import lamplighter as ll
        p=Path(self.temp.name)/'object'
        ll.atomic(p,b'private')
        self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
        ll.atomic(p,b'public',mode=0o644)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o644)
    def test_job_path_rejects_traversal(self):
        with self.assertRaises(ValueError):agent.job_path('../config')

if __name__=='__main__':unittest.main()
