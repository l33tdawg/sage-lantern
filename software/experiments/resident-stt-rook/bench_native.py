"""Opt-in pinned native-ASR lab. Requires previously frozen registration.

No capture, playback, network, services or installed-device actions.
All raw paired outputs persist before development reference scoring.
"""
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import tempfile
import threading
import time
import unicodedata
import wave
import controller
from native_backend import NativeBackend,digest

REPO=Path(__file__).resolve().parents[3]
ROOT=REPO/'work/resident-asr-bakeoff-20261002'
MODEL=REPO/'software/cache/models/ggml-base.bin'
CLI=ROOT/'build/bin/whisper-cli'
WORKER=ROOT/'build/bin/rook-resident-whisper'
FLAGS=['-t','6','--beam-size','1','--best-of','2','--no-timestamps','-np']

def words(text):return re.findall(r'[^\W_]+',unicodedata.normalize('NFKC',text).casefold())
def distance(a,b):
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        next_row=[i]
        for j,y in enumerate(b,1):next_row.append(min(next_row[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=next_row
    return row[-1]
def pcm(row):
    with wave.open(str(REPO/row['path']),'rb') as wav:return wav.readframes(wav.getnframes())
def stats(values):
    ordered=sorted(values)
    return {'p50_ms':statistics.median(ordered),'p95_ms':ordered[max(0,__import__('math').ceil(.95*len(ordered))-1)],'n':len(values)}
def baseline(row,language,index):
    with tempfile.TemporaryDirectory(prefix='rook-cli-asr-') as tmp:
        base=Path(tmp)/'transcript'
        command=[str(CLI),'-m',str(MODEL),'-f',str(REPO/row['path']),*FLAGS,'-otxt','-of',str(base)]
        if language!='en':command+=['-l',language]
        a=time.perf_counter()
        result=subprocess.run(command,capture_output=True,timeout=120)
        path=base.with_suffix('.txt')
        if result.returncode!=0 or not path.is_file():raise RuntimeError('baseline_failed')
        text=path.read_text().strip()
        elapsed=(time.perf_counter()-a)*1000
        (ROOT/'logs'/f'{index}-cli.stderr').write_bytes(result.stderr)
        try:controller.transcript(text);status='speech'
        except controller.NoSpeech:status='no_speech'
        return dict(text=text,elapsed_ms=elapsed,status=status,code=result.returncode)
def profile(reg,language):
    binding={**reg['parameter_binding'],'language':language}
    sha=hashlib.sha256(json.dumps(binding,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return controller.Profile(reg['model_sha256'],reg['worker_binary_sha256'],sha)
def native(worker,backend,row,sequence):
    request='fixture_'+hashlib.sha256(row['id'].encode()).hexdigest()[:24]
    lease=controller.Lease('lab_owner','lab_session',1,request,time.monotonic()+120,sequence)
    data=pcm(row)
    a=time.perf_counter()
    try:
        final=worker.transcribe(lease,data);text=final.text;status='speech'
    except controller.NoSpeech as exc:
        text='' if str(exc)=='silent_pcm' else backend.last_metrics['text'].strip()
        status='no_speech'
    elapsed=(time.perf_counter()-a)*1000
    metrics={'predecode_refusal':'silent_pcm'} if status=='no_speech' and not any(data) else backend.last_metrics
    return dict(text=text,elapsed_ms=elapsed,status=status,native=metrics)
def main():
    if (ROOT/'PAIRS.jsonl').exists() or (ROOT/'RESULT.json').exists():raise RuntimeError('outputs_already_exist')
    reg=json.loads((ROOT/'REGISTRATION_ENGLISH.json').read_text())
    for name,wanted in reg['source_sha256'].items():
        if digest(REPO/name)!=wanted:raise RuntimeError('source_changed_after_freeze:'+name)
    for path,wanted in ((MODEL,reg['model_sha256']),(CLI,reg['cli_binary_sha256']),(WORKER,reg['worker_binary_sha256'])):
        if digest(path)!=wanted:raise RuntimeError('binary_changed_after_freeze')
    for row in reg['fixtures']:
        if digest(REPO/row['path'])!=row['sha256']:raise RuntimeError('fixture_changed_after_freeze')
    pairs=[];loads={};cancel_check={};exit_checks={}
    with (ROOT/'PAIRS.jsonl').open('x') as output:
        for language in ('en',):
            p=profile(reg,language)
            backend=NativeBackend(WORKER,MODEL,p.baseline_parameters_sha256,cancelled_error=controller.Cancelled,
                                  language=language,log_path=ROOT/'logs'/f'{language}-worker.stderr')
            worker=controller.ResidentCandidate(backend,p,lambda lease:True,enabled=True)
            selected=reg['fixtures']
            # First decode uses the real contract's authenticated load path.
            # Cold baseline/candidate are separate from the warm paired sample.
            first=selected[0]
            cold_baseline=baseline(first,language,'cold-'+language)
            cold_resident=native(worker,backend,first,0)
            loads[language]={'native':backend.load_metrics,'cold_baseline':cold_baseline,'cold_resident':cold_resident}
            output.write(json.dumps({'phase':'cold','policy':language,**loads[language]},ensure_ascii=False)+'\n');output.flush()
            sequence=1
            try:
                for repeat in range(reg['repeats']):
                    rows=selected if repeat%2==0 else list(reversed(selected))
                    for row in rows:
                        index=f'{language}-{repeat}-{row["id"]}'
                        calls=('baseline','resident') if repeat%2==0 else ('resident','baseline')
                        result={}
                        for call in calls:
                            if call=='baseline':result[call]=baseline(row,language,index)
                            else:result[call]=native(worker,backend,row,sequence);sequence+=1
                        pair={'fixture':row['id'],'language_policy':language,'repeat':repeat,'order':list(calls),**result}
                        pairs.append(pair);output.write(json.dumps(pair,ensure_ascii=False)+'\n');output.flush()
                        print(json.dumps({'fixture':row['id'],'policy':language,'repeat':repeat,
                                          'same':result['baseline']['text']==result['resident']['text'],
                                          'baseline_ms':round(result['baseline']['elapsed_ms'],2),
                                          'resident_ms':round(result['resident']['elapsed_ms'],2)}),flush=True)
                if language=='en':
                    row=next(r for r in selected if r['id']=='jfk')
                    entered=threading.Event();backend.on_begin=entered.set;outcomes=[]
                    def decode():
                        try:native(worker,backend,row,sequence)
                        except Exception as exc:outcomes.append(type(exc).__name__)
                    thread=threading.Thread(target=decode);thread.start()
                    if not entered.wait(5):raise RuntimeError('native_cancel_no_begin')
                    time.sleep(.05)
                    a=time.perf_counter();immediate=worker.cancel();thread.join(10)
                    cancel_check={'cancel_acknowledged_drain_immediately':immediate,'thread_joined':not thread.is_alive(),
                                  'cancel_to_drain_ms':(time.perf_counter()-a)*1000,'outcomes':outcomes,
                                  'drained':worker.drained,'native_final':backend.last_metrics}
                    if thread.is_alive() or outcomes!=['Cancelled'] or not worker.drained:
                        raise RuntimeError('native_cancel_failed')
                    sequence+=1;backend.on_begin=lambda:None
                    row=next(r for r in selected if r['id']=='en_recall')
                    after=native(worker,backend,row,sequence)
                    expected=next(x for x in pairs if x['fixture']=='en_recall' and x['language_policy']=='en')['baseline']['text']
                    cancel_check['after_cancel_text']=after['text'];cancel_check['after_cancel_matches_baseline']=after['text']==expected
            finally:
                worker.close()
                exit_checks[language]={'process_exit':backend.process.poll(),'drained':worker.drained}
    # Raw output is already durable. Only now use references for development WER.
    references={r['id']:r for r in reg['fixtures']};summaries={}
    for language in ('en',):
        group=[p for p in pairs if p['language_policy']==language]
        scores={}
        for lane in ('baseline','resident'):
            numerator=denominator=0;per_fixture=[]
            for pair in group:
                ref=words(references[pair['fixture']]['reference']);hyp=words(pair[lane]['text'])
                errors=distance(ref,hyp)
                if ref:numerator+=errors;denominator+=len(ref)
                per_fixture.append({'fixture':pair['fixture'],'repeat':pair['repeat'],'errors':errors,'reference_words':len(ref),
                                    'text':pair[lane]['text'],'silence_false_speech':not ref and pair[lane]['status']=='speech'})
            scores[lane]={'aggregate_wer':numerator/denominator,'word_errors':numerator,'reference_words':denominator,
                          'latency':stats([p[lane]['elapsed_ms'] for p in group]),'per_fixture':per_fixture}
        summaries[language]={'pairs':len(group),'exact_transcript_pairs':sum(p['baseline']['text']==p['resident']['text'] for p in group),
                             'speech_status_pairs':sum(p['baseline']['status']==p['resident']['status'] for p in group),
                             'scores':scores}
    result={'scope':reg['purpose'],'registration_sha256':digest(ROOT/'REGISTRATION_ENGLISH.json'),'source_sha256':reg['source_sha256'],
            'summaries':summaries,'loads':loads,'native_cancellation':cancel_check,'unload':exit_checks,
            'actual_runtime_authorization':False,'production_wiring':False,'hardware_qualification':False,
            'independent_accuracy_qualification':False,'limitations':reg['limitations']}
    (ROOT/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'complete':True,'pairs':len(pairs),'cancel':cancel_check,'unload':exit_checks},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
