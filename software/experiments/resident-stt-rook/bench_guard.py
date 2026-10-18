"""Registered inspected-English regression for the exact-zero PCM guard."""
import json
from pathlib import Path
import time
import bench_native as b
from native_backend import NativeBackend,digest

def main():
    root=b.ROOT/'silence-guard/attempt-2';root.joinpath('logs').mkdir(exist_ok=True)
    if (root/'RESULT.json').exists():raise RuntimeError('guard_outputs_exist')
    reg=json.loads((root/'REGISTRATION.json').read_text())
    for name,wanted in reg['source_sha256'].items():
        if digest(b.REPO/name)!=wanted:raise RuntimeError('guard_source_changed')
    for row in reg['fixtures']:
        if digest(b.REPO/row['path'])!=row['sha256']:raise RuntimeError('guard_fixture_changed')
    parent=json.loads((b.ROOT/'REGISTRATION_ENGLISH.json').read_text())
    if digest(b.CLI)!=parent['cli_binary_sha256']:raise RuntimeError('baseline_binary_changed')
    p=b.profile(parent,'en')
    backend=NativeBackend(b.WORKER,b.MODEL,p.baseline_parameters_sha256,cancelled_error=b.controller.Cancelled,
                          log_path=root/'logs/native.stderr')
    worker=b.controller.ResidentCandidate(backend,p,lambda lease:True,enabled=True)
    b.ROOT=root
    results=[]
    try:
        silence=next(r for r in reg['fixtures'] if r['id']=='silence')
        silent=b.native(worker,backend,silence,0)
        zero_started_model=backend.process is not None
        if silent['status']!='no_speech' or silent['text']!='' or zero_started_model:
            raise RuntimeError('zero_pcm_guard_failed')
        results.append({'fixture':'silence','baseline':b.baseline(silence,'en','silence'),'resident':silent,
                        'zero_started_model':zero_started_model})
        for sequence,row in enumerate((r for r in reg['fixtures'] if r['id']!='silence'),1):
            old=b.baseline(row,'en',str(sequence));new=b.native(worker,backend,row,sequence)
            results.append({'fixture':row['id'],'scale':row['scale'],'baseline':old,'resident':new,
                            'same':old['text']==new['text']})
            print(json.dumps({'fixture':row['id'],'same':old['text']==new['text']}),flush=True)
        # The warm-context path must also refuse silence and remain reusable.
        silent_warm=b.native(worker,backend,silence,11)
        row=next(r for r in reg['fixtures'] if r['id']=='en_recall-1.0')
        after=b.native(worker,backend,row,12)
        expected=next(r for r in results if r['fixture']=='en_recall-1.0')['baseline']['text']
        if silent_warm['text'] or silent_warm['status']!='no_speech' or after['text']!=expected:
            raise RuntimeError('warm_silence_reuse_failed')
    finally:worker.close()
    result={'scope':reg['purpose'],'registration_sha256':digest(root/'REGISTRATION.json'),
            'runner_sha256':digest(Path(__file__)),'source_sha256':reg['source_sha256'],
            'results':results,'nonzero_pairs':10,'nonzero_exact_pairs':sum(r.get('same') is True for r in results),
            'cold_silence_guard':silent,'warm_silence_guard':silent_warm,'after_silence_matches_baseline':after['text']==expected,
            'unload':{'process_exit':backend.process.poll(),'drained':worker.drained},
            'actual_native_calls':11,'actual_cli_calls':11,'independent_accuracy_qualification':False,
            'noise_or_general_silence_detection':False}
    (root/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'complete':True,'nonzero_exact_pairs':result['nonzero_exact_pairs'],
                      'silence_guard_ms':silent['elapsed_ms'],'unload':result['unload']}),flush=True)
if __name__=='__main__':main()
