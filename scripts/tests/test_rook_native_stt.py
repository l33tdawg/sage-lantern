"""Boundary tests with a fake subprocess protocol, not native-ASR accuracy."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]/'software/experiments/resident-stt-rook'
def module(name,path):
    if name in sys.modules:return sys.modules[name]
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec)
    sys.modules[name]=value;spec.loader.exec_module(value);return value
C=module('rook_resident_stt',ROOT/'controller.py')
N=module('rook_native_stt',ROOT/'native_backend.py')
PCM=b'\1\0'*320

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.model=self.root/'model';self.model.write_bytes(b'fake model, never an ASR weight')
    def candidate(self,*,ready=True,final=None,bad_hash=False):
        final=final or {'event':'final','sequence':0,'code':0,'text':'Hello.','cancelled':False,'state_freed':True}
        path=self.root/'worker'
        code=f'''#!{sys.executable}
import json,sys
print(json.dumps(dict(event='ready',language='en',no_context={ready!r})),flush=True)
for line in sys.stdin:
    if line.strip()=='quit':
        print(json.dumps(dict(event='closed',model_freed=True)),flush=True)
        break
    print(json.dumps(dict(event='begin',sequence=0)),flush=True)
    print(json.dumps({final!r}),flush=True)
'''
        path.write_text(code);path.chmod(0o700)
        p=C.Profile('a'*64 if bad_hash else N.digest(self.model),N.digest(path),'c'*64)
        b=N.NativeBackend(path,self.model,'c'*64,cancelled_error=C.Cancelled,log_path=self.root/'stderr')
        w=C.ResidentCandidate(b,p,lambda lease:True,enabled=True)
        return w,b
    def lease(self):return C.Lease('owner','session',1,'request',time.monotonic()+5)
    def test_hash_mismatch_is_refused_before_spawn(self):
        w,b=self.candidate(bad_hash=True)
        with self.assertRaisesRegex(N.NativeFailure,'profile_mismatch'):w.transcribe(self.lease(),PCM)
        self.assertIsNone(b.process)
    def test_unready_context_is_terminated_not_reused(self):
        w,b=self.candidate(ready=False)
        with self.assertRaisesRegex(N.NativeFailure,'not_ready'):w.transcribe(self.lease(),PCM)
        self.assertIsNotNone(b.process.poll())
        with self.assertRaises(C.Refused):w.transcribe(self.lease(),PCM)
    def test_state_free_ack_is_required_before_any_final(self):
        final={'event':'final','sequence':0,'code':0,'text':'Hello.','cancelled':False,'state_freed':False}
        w,b=self.candidate(final=final)
        with self.assertRaisesRegex(N.NativeFailure,'final_mismatch'):w.transcribe(self.lease(),PCM)
        self.assertIsNotNone(b.process.poll())
        self.assertTrue(w.drained)
    def test_boolean_sequence_is_not_a_number(self):
        final={'event':'final','sequence':False,'code':0,'text':'Hello.','cancelled':False,'state_freed':True}
        w,b=self.candidate(final=final)
        with self.assertRaisesRegex(N.NativeFailure,'final_mismatch'):w.transcribe(self.lease(),PCM)
        self.assertIsNotNone(b.process.poll())
    def test_fake_valid_roundtrip_and_close(self):
        w,b=self.candidate()
        self.assertEqual(w.transcribe(self.lease(),PCM).text,'Hello.')
        self.assertTrue(w.close());self.assertEqual(b.process.poll(),0)
    def test_duplicate_and_nonfinite_protocol_fields_refused(self):
        for value in ('{"code":0,"code":1}','{"code":NaN}','{"code":Infinity}','[]'):
            with self.subTest(value=value):
                with self.assertRaises(N.NativeFailure):N.strict_json(value)

if __name__=='__main__':unittest.main()
