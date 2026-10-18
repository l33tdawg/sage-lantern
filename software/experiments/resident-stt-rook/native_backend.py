"""Opt-in lab subprocess backend for the pinned resident Whisper driver.

No production imports/wiring, microphone, network, persistent audio or restart.
The composition root supplies artifact hashes and the cancellation exception.
Native state-free acknowledgements are checked; they are not OS attestation.
"""
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import time
import wave


class NativeFailure(RuntimeError):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def strict_json(line):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise NativeFailure('duplicate_worker_field')
            result[key]=value
        return result
    def invalid(_):raise NativeFailure('nonfinite_worker_field')
    result=json.loads(line,object_pairs_hook=pairs,parse_constant=invalid)
    if type(result) is not dict:raise NativeFailure('worker_object_required')
    return result


class NativeBackend:
    def __init__(self, worker, model, parameter_binding, *, cancelled_error,
                 language='en', log_path, cancel_grace=2.0):
        if language not in ('en','auto','ms'):raise NativeFailure('unsupported_language')
        self.worker,self.model=Path(worker).resolve(),Path(model).resolve()
        self.parameter_binding=parameter_binding
        self.cancelled_error=cancelled_error
        self.language,self.log_path,self.cancel_grace=language,Path(log_path),cancel_grace
        self.process=None
        self._stderr=None
        self._buffer=b''
        self._sequence=0
        self._settled=True
        self.last_metrics={}
        self.load_metrics={}
        self.on_begin=lambda:None

    def _kill(self):
        if self.process is not None:
            if self.process.poll() is None:
                os.killpg(self.process.pid,signal.SIGKILL)
            self.process.wait(timeout=5)
        self._settled=True

    def _read(self, abort, timeout=120.0):
        started=time.monotonic()
        cancelled_at=None
        while True:
            if abort() and cancelled_at is None:
                cancelled_at=time.monotonic()
                if self.process.poll() is None:os.kill(self.process.pid,signal.SIGUSR1)
            now=time.monotonic()
            if (cancelled_at is not None and now-cancelled_at>=self.cancel_grace) or now-started>=timeout:
                self._kill()
                raise NativeFailure('worker_timeout_terminated')
            if b'\n' in self._buffer:
                line,self._buffer=self._buffer.split(b'\n',1)
                if len(line)>65536:raise NativeFailure('worker_line_limit')
                return strict_json(line.decode('utf-8'))
            if len(self._buffer)>65536:raise NativeFailure('worker_line_limit')
            ready,_,_=select.select([self.process.stdout],[],[],0.01)
            if ready:
                part=os.read(self.process.stdout.fileno(),8192)
                if not part:raise NativeFailure('worker_eof')
                self._buffer+=part

    def load(self, profile, abort):
        started=time.perf_counter()
        if self.process is not None:raise NativeFailure('no_silent_reload')
        if ((profile.threads,profile.beam_size,profile.best_of)!=(6,1,2)
            or digest(self.worker)!=profile.backend_sha256 or digest(self.model)!=profile.model_sha256
            or self.parameter_binding!=profile.baseline_parameters_sha256):
            raise NativeFailure('worker_profile_mismatch')
        if abort():raise self.cancelled_error('cancelled_before_native_load')
        self._stderr=self.log_path.open('xb')
        self.process=subprocess.Popen([str(self.worker),'-m',str(self.model),'-t','6',
            '--beam-size','1','--best-of','2','--no-timestamps','-np','-l',self.language],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self._stderr,
            bufsize=0,start_new_session=True)
        self._settled=False
        ready=self._read(abort,timeout=30.0)
        if (ready.get('event')!='ready' or ready.get('language')!=self.language
            or ready.get('no_context') is not True):raise NativeFailure('worker_not_ready')
        self.load_metrics={**ready,'load_wall_ms':(time.perf_counter()-started)*1000}
        self._settled=True
        if abort():raise self.cancelled_error('cancelled_native_load')

    def transcribe(self, pcm, profile, abort):
        if self.process is None or self.process.poll() is not None or not self._settled:
            raise NativeFailure('worker_not_idle')
        self._settled=False
        with tempfile.TemporaryDirectory(prefix='rook-asr-') as tmp:
            path=Path(tmp)/'input.wav'
            with wave.open(str(path),'wb') as wav:
                wav.setparams((2,2,16000,0,'NONE','not compressed'))
                wav.writeframes(pcm)
            self.process.stdin.write((str(path)+'\n').encode())
            begin=self._read(abort)
            if begin.get('event')!='begin' or type(begin.get('sequence')) is not int or begin['sequence']!=self._sequence:
                raise NativeFailure('worker_begin_mismatch')
            self.on_begin()
            final=self._read(abort)
            if (final.get('event')!='final' or type(final.get('sequence')) is not int
                or final['sequence']!=self._sequence or final.get('state_freed') is not True
                or type(final.get('cancelled')) is not bool or type(final.get('code')) is not int
                or type(final.get('text')) is not str):raise NativeFailure('worker_final_mismatch')
            self._sequence+=1
            self._settled=True
            self.last_metrics=final
            if final['cancelled'] or abort():raise self.cancelled_error('cancelled_native_decode')
            if final['code']!=0:raise NativeFailure('worker_decode_failure')
            return final['text']

    def reset_utterance(self):
        if not self._settled:
            # Protocol errors cannot leave an unknown live worker behind.
            self._kill()

    def close(self):
        try:
            if self.process is not None and self.process.poll() is None:
                if not self._settled:self._kill()
                else:
                    self.process.stdin.write(b'quit\n')
                    closed=self._read(lambda:False,timeout=5.0)
                    if closed.get('event')!='closed' or closed.get('model_freed') is not True:
                        raise NativeFailure('worker_unload_missing')
                    if self.process.wait(timeout=5)!=0:raise NativeFailure('worker_close_failure')
        finally:
            self._kill()
            if self.process is not None:
                self.process.stdin.close();self.process.stdout.close()
            if self._stderr is not None:self._stderr.close()
