#!/usr/bin/env python3
"""Virtual-clock replay of the real voice-loop publication path, no hardware."""
import argparse
import importlib.util
from pathlib import Path
from run import digest, write


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','candidate','output'):
        p.add_argument('--'+name, type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    helper = Path(__file__).resolve().parents[3]/'tests/test_unit_a_voice_loop.py'
    write(args.output/'registration.json', {"source_sha256":digest(Path(__file__)),
        "baseline_sha256":digest(args.baseline), "candidate_sha256":digest(args.candidate),
        "harness_sha256":digest(helper), "scope":"mock process/clock, real publication logic; no model/browser/hardware",
        "case":"one incomplete multilingual token followed by upstream pause", "no_progress_before_content":True})
    harness = load(helper, 'voice_publication_harness')
    rows = []
    for mode,path in (('baseline',args.baseline),('candidate',args.candidate)):
        harness.MODULE = load(path, 'voice_publication_'+mode)
        check = harness.VoiceEventTests()
        check.setUp()
        writes = []
        check.loop.write_state.side_effect = lambda *a,**kw:writes.append({"seconds":harness.MODULE.time.monotonic(),
            "phase":a[0], "response":kw.get('response','')})
        answer = 'مرحبا، hello'
        check.result['response'] = answer
        try:
            check.execute([check.delta(answer),check.final()], idle_polls=30)
        finally:
            check.doCleanups()
        visible = [r for r in writes if r['phase']=='processing' and r['response'].strip()]
        rows.append({"mode":mode, "first_publication_after_content_ms":visible[0]['seconds']*1000,
                     "published_bytes_identical":visible[0]['response']==answer,
                     "final_bytes_identical":writes[-1]['response']==answer})
    write(args.output/'summary.json', rows)
    print((args.output/'summary.json').read_text())


if __name__ == '__main__':
    main()
