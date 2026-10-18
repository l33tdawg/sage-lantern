"""Synthetic development audio only. No capture, downloads or playback.

Freeze WAVs, authored reference text and study registration before model runs.
Speech is macOS system TTS. It cannot qualify natural accents/noisy capture.
"""
from array import array
import hashlib
import json
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import wave

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
ROOT=REPO/'work/resident-asr-bakeoff-20261002'
ITEMS=[
 ('en_status','Samantha','en','Is the battery low?'),
 ('en_recall','Samantha','en','Find the note about the water filter.'),
 ('en_negative','Samantha','en','Do not save this message.'),
 ('en_number','Samantha','en','Fifteen times six is ninety.'),
 ('en_correction','Samantha','en','Turn left. No, turn right.'),
 ('en_accent','Rishi','en','My name is Farid. I live in Kuala Lumpur.'),
 ('ms_recall','Amira','ms','Cari nota tentang penapis air.'),
 ('ms_status','Amira','ms','Berapa baki bateri sekarang?'),
 ('ms_negative','Amira','ms','Jangan simpan mesej ini. Saya cuma mahu membacanya.'),
]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write_pcm(path,pcm):
    with wave.open(str(path),'wb') as wav:
        wav.setparams((2,2,16000,0,'NONE','not compressed'));wav.writeframes(pcm)
def stereo(path):
    with wave.open(str(path),'rb') as wav:
        channels,width,rate,frames=wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),wav.getnframes()
        if channels not in (1,2) or width!=2 or rate!=16000 or not 0<frames<=16000*30:
            raise RuntimeError('fixture_format')
        raw=wav.readframes(frames)
        if len(raw)!=frames*channels*2:raise RuntimeError('truncated_fixture')
    if channels==2:return raw
    samples=array('h');samples.frombytes(raw)
    if sys.byteorder=='big':samples.byteswap()
    result=array('h',(x for s in samples for x in (s,s)))
    if sys.byteorder=='big':result.byteswap()
    return result.tobytes()
def main():
    registration=ROOT/'REGISTRATION.json'
    if registration.exists():raise RuntimeError('registration_already_frozen')
    fixture=ROOT/'fixtures';fixture.mkdir(exist_ok=True)
    rows=[]
    for name,voice,language,text in ITEMS:
        source=fixture/(name+'-tts.wav')
        subprocess.run(['/usr/bin/say','-v',voice,'-o',str(source),'--file-format=WAVE',
                        '--data-format=LEI16@16000',text],check=True,timeout=30)
        path=fixture/(name+'.wav');write_pcm(path,stereo(source))
        rows.append(dict(id=name,language=language,reference=text,voice=voice,path=str(path.relative_to(REPO)),kind='synthetic_tts'))
    # Same synthetic Malay waveform with deterministic sample noise, not new speech.
    source=fixture/'ms_recall.wav'
    with wave.open(str(source),'rb') as wav:raw=wav.readframes(wav.getnframes())
    samples=array('h');samples.frombytes(raw)
    rms=(sum(x*x for x in samples)/len(samples))**0.5
    rng=random.Random(20261002)
    sigma=rms/(10**(18/20))
    noisy=array('h',(max(-32768,min(32767,round(x+rng.gauss(0,sigma)))) for x in samples))
    path=fixture/'ms_noise.wav';write_pcm(path,noisy.tobytes())
    rows.append(dict(id='ms_noise',language='ms',reference=ITEMS[6][3],voice='Amira',path=str(path.relative_to(REPO)),kind='synthetic_tts_noise18db'))
    path=fixture/'silence.wav';write_pcm(path,b'\0'*(16000*4*2))
    rows.append(dict(id='silence',language='none',reference='',path=str(path.relative_to(REPO)),kind='zero_pcm'))
    path=fixture/'jfk.wav'
    write_pcm(path,stereo(ROOT/'upstream/whisper.cpp-1.8.6/samples/jfk.wav'))
    rows.append(dict(id='jfk',language='en',reference='And so my fellow Americans ask not what your country can do for you ask what you can do for your country',path=str(path.relative_to(REPO)),kind='bundled_upstream_sample'))
    for row in rows:
        path=REPO/row['path'];row['sha256']=sha(path)
        with wave.open(str(path),'rb') as wav:row['seconds']=wav.getnframes()/16000
    sources={str(p.relative_to(REPO)):sha(p) for p in HERE.iterdir() if p.suffix in ('.py','.cpp','.cmake')}
    sources['software/runtime/src/sage_lantern/adapters.py']=sha(REPO/'software/runtime/src/sage_lantern/adapters.py')
    sources['scripts/lantern/unit-a-voice-loop.py']=sha(REPO/'scripts/lantern/unit-a-voice-loop.py')
    for name in ('native_worker.cpp','native_backend.py','controller.py','build_lab.py','freeze_fixtures.py','lab-target.cmake','bench_native.py'):
        shutil.copyfile(HERE/name,ROOT/'source'/name)
    binding={'pinned_cli_sha256':sha(ROOT/'upstream/whisper.cpp-1.8.6/examples/cli/cli.cpp'),
             'generated_parameter_block_sha256':sha(ROOT/'build/rook-generated/cli_parameters.inc'),
             'header_sha256':sha(ROOT/'upstream/whisper.cpp-1.8.6/include/whisper.h'),
             'implementation_sha256':sha(ROOT/'upstream/whisper.cpp-1.8.6/src/whisper.cpp'),
             'flags':['-t','6','--beam-size','1','--best-of','2','--no-timestamps','-np']}
    data={'purpose':'same-model CPU subprocess/resident development comparison; no device or independent accuracy qualification',
          'fixtures':rows,'repeats':3,'pair_orders':['baseline,resident','resident,baseline','baseline,resident'],
          'variants':{'en':'all fixtures; exact current CLI language default en',
                      'auto':'separate development language fork on the four Malay fixtures, baseline and resident both -l auto'},
          'wer_normalization':'Unicode NFKC then casefold then Unicode alphanumeric words; no number equivalence or stemming; silence excluded from WER denominator',
          'pass_gate':'exact stripped raw transcript equality for each registered pair; no regression against the matched baseline; native cancel/reset/unload checks separate',
          'source_sha256':sources,'parameter_binding':binding,
          'model_sha256':sha(REPO/'software/cache/models/ggml-base.bin'),
          'archive_sha256':sha(REPO/'software/cache/sources/whisper.cpp-v1.8.6.tar.gz'),
          'cli_binary_sha256':sha(ROOT/'build/bin/whisper-cli'),
          'worker_binary_sha256':sha(ROOT/'build/bin/rook-resident-whisper'),
          'machine':{'platform':platform.platform(),'machine':platform.machine()},
          'limitations':['Single-author synthetic development text and TTS; not fresh independent qualification',
                         'No human Malay/accent/noisy-microphone corpus',
                         'Host CPU uses platform-specific kernels/Accelerate; not RK3588 firmware/binary attestation',
                         'Engine cold start is not cold filesystem cache',
                         'No runtime wiring, devices, services, microphone or audio playback'],
          'pre_registration_attempts':['Initial lock walker expected artifacts key and failed before source extraction/model runs; corrected to verified path/hash walk',
                                       'Sandboxed Amira file-only synthesis returned zero-frame WAV with extension error; no ASR model output inspected']}
    registration.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'frozen':len(rows),'registration':str(registration)},indent=2))
if __name__=='__main__':main()
