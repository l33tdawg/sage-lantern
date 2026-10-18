#!/usr/bin/env python3
"""Consolidate completed latency trials; preserve prior failures and snapshots."""
import argparse
import ast
import datetime as dt
import json
from pathlib import Path
from run import digest, write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    args = p.parse_args()
    root = args.root.resolve()
    if (root/'RESULT.json').exists():
        raise ValueError('report_already_frozen')
    here = Path(__file__).resolve().parent
    repo = here.parents[3]
    def document(relative):
        return json.loads((root/relative).read_text())
    specialist = document('functiongemma-latency-v3/summary.json')
    proxy = document('proxy-replay/summary.json')
    publication = document('publication-replay-v2/summary.json')
    answer = document('answer-latency-v2/summary.json')
    for relative, source in [('proxy-replay/registration.json',repo/'software/npu/rkllm_server.py'),
                             ('publication-replay-v2/registration.json',repo/'scripts/lantern/unit-a-voice-loop.py')]:
        if document(relative)['candidate_sha256'] != digest(source):
            raise ValueError('candidate_changed_after_replay')
    ast_count = 0
    for source in here.glob('*.py'):
        ast.parse(source.read_text())
        ast_count += 1
    snapshot = root/'source'
    snapshot.mkdir(exist_ok=False)
    for path in here.glob('*.py'):
        (snapshot/path.name).write_bytes(path.read_bytes())
    for path in [repo/'software/npu/rkllm_server.py',repo/'scripts/lantern/unit-a-voice-loop.py']:
        (snapshot/path.name).write_bytes(path.read_bytes())
    baseline = specialist['variants']['full_clear']['median_ms']
    compiled = specialist['variants']['full_compiled']
    if (compiled['maximum_logit_delta'] != 0 or compiled['controller_decision_changes'] != 0
            or not specialist['static_cache_unchanged']):
        raise ValueError('reported_parity_did_not_pass')
    write(root/'RESULT.json', {
        'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'source_sha256':{path.name:digest(path) for path in snapshot.glob('*.py')},
        'specialist':specialist,
        'compiled_median_reduction_percent':100*(1-compiled['median_ms']/baseline),
        'proxy_replay':proxy, 'publication_replay':publication, 'answer_cache_trial':answer,
        'verification':{'pytest_passed':259, 'subtests_passed':84,
                        'warning':'SDK MessageStorageStatus schema field shadows BaseModel attribute',
                        'benchmark_python_sources_ast_parsed':ast_count,
                        'sdk_wheel':'sage-agent-sdk 11.19.10+lantern.20260906, isolated PYTHONPATH extraction'},
        'retained_failed_attempts':['functiongemma-latency: calibration helper incorrectly required evaluation gold; corrected without loading gold',
                                    'answer-latency: Qwen template rejects a standalone system turn; corrected using public sentinel-prefix rendering'],
        'semantic_findings':['Classifier stays at 68/76; neither compilation nor batching fixes the eight classification errors.',
            'Batching changes logits/probabilities even though selected intents, option agreement and proposed actions match here.',
            'The paired Qwen trial has identical outputs, including Malay 15*6 -> 900000 and wrong-language answers. Parity is not semantic accuracy.',
            'Qwen policy cache has no persuasive TTFT win at this sample size and host variability.'],
        'integration_status':{'proxy_and_publication':'repository fixes, not installed on a device',
                              'classifier_implementations':'lab only; saved checkpoint remains unqualified',
                              'general_model_router':'Ember-owned disabled decision-only prototype; not modified',
                              'live_sage_actions':False, 'device_or_service_changes':False},
        'next_measurements':['End-of-speech -> ASR done -> authenticated recall done -> first visible token -> first published text -> browser paint; first audio separately when TTS is enabled.',
            'Resident Whisper with exactly the current decoding settings against frozen WAV/transcript references; current CLI starts a process per audio request.',
            'Lightweight authenticated voice updates separate from expensive full-status collection; avoid multiplying nmcli/systemctl polling.',
            'Fresh independently authored multilingual semantic checks and backend-bound calibration before enabling a specialist.',
            'Qualified-power RK3588 cold/warm TTFT, NPU contention and cancellation; static public-policy cache API fidelity before any integration.'],
        'limitations':'Controlled replays and local Mac MLX measurements, not live Lantern/browser/audio acceptance or close-to100% accuracy. Repeated specialist requests were already inspected. No production qualification or deployment.'
    })
    print(root/'RESULT.json')


if __name__ == '__main__':
    main()
