"""Owner-directed English-only study revision, before any ASR outputs.

Preserves the previous multilingual registration and all unused WAVs.
Adds two synthetic English voice variations and seeded English sample noise.
"""
from array import array
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import wave
from freeze_fixtures import stereo,write_pcm,sha,ROOT,REPO,HERE

def main():
    target=ROOT/'REGISTRATION_ENGLISH.json'
    if target.exists() or (ROOT/'PAIRS.jsonl').exists():raise RuntimeError('english_study_already_frozen_or_started')
    old=json.loads((ROOT/'REGISTRATION.json').read_text())
    rows=[r for r in old['fixtures'] if r['language'] in ('en','none')]
    for row in rows:
        if sha(REPO/row['path'])!=row['sha256']:raise RuntimeError('old_fixture_changed')
    for name,voice,text in [('en_uk','Daniel','Read the note about our meeting tomorrow.'),
                            ('en_za','Tessa','Do not save it. Read the message instead.')]:
        source=ROOT/'fixtures'/(name+'-tts.wav')
        subprocess.run(['/usr/bin/say','-v',voice,'-o',str(source),'--file-format=WAVE',
                        '--data-format=LEI16@16000',text],check=True,timeout=30)
        path=ROOT/'fixtures'/(name+'.wav');write_pcm(path,stereo(source))
        rows.append(dict(id=name,language='en',reference=text,voice=voice,path=str(path.relative_to(REPO)),kind='synthetic_tts'))
    ref=next(r for r in rows if r['id']=='en_recall')
    with wave.open(str(REPO/ref['path']),'rb') as wav:raw=wav.readframes(wav.getnframes())
    samples=array('h');samples.frombytes(raw)
    rms=(sum(x*x for x in samples)/len(samples))**0.5
    rng=random.Random(20261002);sigma=rms/(10**(18/20))
    noisy=array('h',(max(-32768,min(32767,round(x+rng.gauss(0,sigma)))) for x in samples))
    path=ROOT/'fixtures/en_noise.wav';write_pcm(path,noisy.tobytes())
    rows.append(dict(id='en_noise',language='en',reference=ref['reference'],voice='Samantha',path=str(path.relative_to(REPO)),kind='synthetic_tts_noise18db'))
    for row in rows:
        path=REPO/row['path'];row['sha256']=sha(path)
        with wave.open(str(path),'rb') as wav:row['seconds']=wav.getnframes()/16000
    source={str(p.relative_to(REPO)):sha(p) for p in HERE.iterdir() if p.suffix in ('.py','.cpp','.cmake')}
    for path in old['source_sha256']:
        if path.startswith('software/runtime/') or path=='scripts/lantern/unit-a-voice-loop.py':source[path]=sha(REPO/path)
    snap=ROOT/'source-english';snap.mkdir()
    for p in HERE.iterdir():
        if p.suffix in ('.py','.cpp','.cmake'):shutil.copyfile(p,snap/p.name)
    reg={**old,'purpose':'owner-directed English-only CPU subprocess/resident development comparison; no device or independent accuracy qualification',
         'supersedes_registration_sha256':sha(ROOT/'REGISTRATION.json'),
         'revision_reason':'Direct owner steering to prioritize English for an international audience, received before any ASR model runs',
         'variants':{'en':'all selected English/silence fixtures; current language default en; no multilingual model runs'},
         'cold_phase':'first selected fixture, one matched subprocess and one resident decode through the normal lifecycle contract; not included in warm paired aggregate',
         'cancel_phase':'after paired runs, cancel during JFK after native begin +50ms; require actual state-free/drain then en_recall parity and model unload',
         'fixtures':rows,'unused_multilingual_fixtures_retained':True,'source_sha256':source}
    target.write_text(json.dumps(reg,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'frozen_english_fixtures':len(rows),'registration':str(target),'paired_calls':len(rows)*3*2},indent=2))
if __name__=='__main__':main()
