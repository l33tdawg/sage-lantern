import importlib.util
from pathlib import Path
import sys
import threading
import unittest

SOURCE = Path(__file__).resolve().parents[2]/'software/experiments/resident-stt-rook/controller.py'
SPEC = importlib.util.spec_from_file_location('rook_resident_stt', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
PCM = b'\1\0'*320


class Backend:
    def __init__(self):
        self.loads = self.resets = self.closes = self.runs = 0
        self.text = 'Apa khabar?'
        self.hook = lambda abort:None
    def load(self, profile, abort):
        self.loads += 1
    def transcribe(self, pcm, profile, abort):
        self.runs += 1
        self.hook(abort)
        return self.text
    def reset_utterance(self):
        self.resets += 1
    def close(self):
        self.closes += 1


class Tests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.now = 1.0
        self.valid = True
        self.profile = MODULE.Profile('a'*64, 'b'*64, 'c'*64)
        self.lease = MODULE.Lease('owner','session',7,'request',10.0)
        self.worker = MODULE.ResidentCandidate(self.backend,self.profile,
            lambda lease:self.valid,enabled=True,clock=lambda:self.now)

    def test_disabled_constructor_is_inert(self):
        w = MODULE.ResidentCandidate(self.backend,self.profile,lambda lease:True)
        with self.assertRaises(MODULE.Refused):w.transcribe(self.lease,PCM)
        self.assertEqual(self.backend.loads,0)

    def test_same_model_reused_and_request_state_reset(self):
        first = self.worker.transcribe(self.lease,PCM)
        other = MODULE.Lease('other','new',8,'next',10.0)
        second = self.worker.transcribe(other,PCM)
        self.assertEqual((first.text,second.text),('Apa khabar?','Apa khabar?'))
        self.assertEqual(second.lease,other)
        self.assertEqual((self.backend.loads,self.backend.runs,self.backend.resets),(1,2,2))
        self.assertTrue(self.worker.close())
        self.assertEqual(self.backend.closes,1)

    def test_invalid_scope_denied_before_load(self):
        self.valid=False
        with self.assertRaises(MODULE.Refused):self.worker.transcribe(self.lease,PCM)
        self.assertEqual(self.backend.loads,0)

    def test_revocation_during_decode_discards_late_result(self):
        def revoke(abort):
            self.valid=False
            self.assertTrue(abort())
        self.backend.hook=revoke
        with self.assertRaises(MODULE.Cancelled):self.worker.transcribe(self.lease,PCM)
        self.assertTrue(self.worker.drained)
        self.assertEqual(self.backend.resets,1)

    def test_deadline_during_decode_discards_late_result(self):
        self.backend.hook=lambda abort:setattr(self,'now',10.0)
        with self.assertRaises(MODULE.Cancelled):self.worker.transcribe(self.lease,PCM)

    def test_cancel_is_not_drain_and_second_request_never_queues(self):
        entered,release=threading.Event(),threading.Event()
        outcomes=[]
        def block(abort):
            entered.set()
            self.assertTrue(release.wait(2))
            self.assertTrue(abort())
        self.backend.hook=block
        def run():
            try:self.worker.transcribe(self.lease,PCM)
            except Exception as exc:outcomes.append(type(exc))
        thread=threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.worker.cancel())
            self.assertFalse(self.worker.drained)
            with self.assertRaises(MODULE.Busy):self.worker.transcribe(self.lease,PCM)
        finally:
            release.set();thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes,[MODULE.Cancelled])
        self.assertTrue(self.worker.drained)

    def test_backend_failure_is_not_silently_retried(self):
        def fail(abort):raise OSError('fixture')
        self.backend.hook=fail
        with self.assertRaises(OSError):self.worker.transcribe(self.lease,PCM)
        with self.assertRaises(MODULE.Refused):self.worker.transcribe(self.lease,PCM)
        self.assertEqual((self.backend.loads,self.backend.closes),(1,1))

    def test_reset_failure_never_acknowledges_drain_or_returns_final(self):
        self.backend.reset_utterance=lambda:(_ for _ in ()).throw(OSError())
        with self.assertRaisesRegex(MODULE.Refused,'reset_failed'):self.worker.transcribe(self.lease,PCM)
        self.assertFalse(self.worker.drained)

    def test_cancel_during_reset_cannot_release_the_pending_final(self):
        self.backend.reset_utterance=lambda:self.worker.cancel()
        with self.assertRaises(MODULE.Cancelled):self.worker.transcribe(self.lease,PCM)
        self.assertTrue(self.worker.drained)

    def test_silence_keeps_model_warm_and_next_sequence_can_run(self):
        self.backend.text='[silence]'
        with self.assertRaises(MODULE.NoSpeech):self.worker.transcribe(self.lease,PCM)
        self.backend.text='Hello'
        next_lease=MODULE.Lease('owner','session',7,'next',10.0,sequence=1)
        self.assertEqual(self.worker.transcribe(next_lease,PCM).text,'Hello')
        self.assertEqual(self.backend.loads,1)

    def test_duplicate_final_sequence_is_refused(self):
        self.worker.transcribe(self.lease,PCM)
        with self.assertRaises(MODULE.Refused):self.worker.transcribe(self.lease,PCM)
        self.assertEqual(self.backend.runs,1)

    def test_partial_load_failure_closes_backend_before_acknowledging_drain(self):
        self.backend.load=lambda *args:(_ for _ in ()).throw(OSError())
        with self.assertRaises(OSError):self.worker.transcribe(self.lease,PCM)
        self.assertEqual(self.backend.closes,1)
        self.assertTrue(self.worker.drained)

    def test_close_failure_never_acknowledges_drain(self):
        self.worker.transcribe(self.lease,PCM)
        self.backend.close=lambda:(_ for _ in ()).throw(OSError())
        with self.assertRaisesRegex(MODULE.Refused,'close_failed'):self.worker.close()
        self.assertFalse(self.worker.drained)
        self.assertFalse(self.worker.close())

    def test_close_during_active_decode_waits_for_return_and_unload(self):
        entered,release=threading.Event(),threading.Event()
        outcomes=[]
        def block(abort):
            entered.set()
            self.assertTrue(release.wait(2))
            self.assertTrue(abort())
        self.backend.hook=block
        def run():
            try:self.worker.transcribe(self.lease,PCM)
            except Exception as exc:outcomes.append(type(exc))
        thread=threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.worker.close())
            self.assertFalse(self.worker.drained)
            self.assertEqual(self.backend.closes,0)
        finally:
            release.set();thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes,[MODULE.Cancelled])
        self.assertEqual((self.backend.resets,self.backend.closes),(1,1))
        self.assertTrue(self.worker.drained)
        with self.assertRaises(MODULE.Refused):self.worker.transcribe(self.lease,PCM)

    def test_close_during_reset_unloads_and_discards_pending_final(self):
        self.backend.reset_utterance=lambda:self.worker.close()
        with self.assertRaises(MODULE.Cancelled):self.worker.transcribe(self.lease,PCM)
        self.assertEqual(self.backend.closes,1)
        self.assertTrue(self.worker.drained)

    def test_cancelled_partial_load_is_unloaded_and_not_retried(self):
        self.backend.load=lambda *args:(_ for _ in ()).throw(MODULE.Cancelled())
        with self.assertRaises(MODULE.Cancelled):self.worker.transcribe(self.lease,PCM)
        self.assertEqual(self.backend.closes,1)
        self.assertTrue(self.worker.drained)
        with self.assertRaises(MODULE.Refused):self.worker.transcribe(self.lease,PCM)

    def test_empty_annotations_and_malformed_text_refused(self):
        for text in ('','[silence]','(music)','♪♫','bad\0text','x'*4097,True):
            with self.subTest(text=str(text)[:15]):
                with self.assertRaises(MODULE.Refused):MODULE.transcript(text)

    def test_strict_types_and_deadline_bounds(self):
        for lease in (MODULE.Lease('o','s',True,'r',10),MODULE.Lease('o','s',1,'r',float('nan')),
                      MODULE.Lease('o','s',1,'r',122),MODULE.Lease('o','s',1,'r',True)):
            with self.assertRaises(MODULE.Refused):self.worker.transcribe(lease,PCM)
        with self.assertRaises(MODULE.Refused):MODULE.Profile('a'*64,'b'*64,'c'*64,beam_size=True).validate()

    def test_pcm_is_bounded_without_touching_backend(self):
        for pcm in (b'',b'123',bytearray(PCM)):
            with self.assertRaises(MODULE.Refused):self.worker.transcribe(self.lease,pcm)
        self.assertEqual(self.backend.loads,0)

    def test_exact_silence_refused_without_model_load_or_decode(self):
        with self.assertRaisesRegex(MODULE.NoSpeech,'silent_pcm'):
            self.worker.transcribe(self.lease,b'\0'*640)
        self.assertEqual((self.backend.loads,self.backend.runs),(0,0))
        self.assertTrue(self.worker.drained)

    def test_silence_cannot_bypass_authorization(self):
        self.valid=False
        with self.assertRaises(MODULE.Refused) as outcome:self.worker.transcribe(self.lease,b'\0'*640)
        self.assertNotIsInstance(outcome.exception,MODULE.NoSpeech)

    def test_single_nonzero_sample_is_not_trimmed_as_quiet_speech(self):
        value=b'\0'*636+b'\1\0\0\0'
        self.assertEqual(self.worker.transcribe(self.lease,value).text,'Apa khabar?')
        self.assertEqual(self.backend.runs,1)


if __name__ == '__main__':unittest.main()
