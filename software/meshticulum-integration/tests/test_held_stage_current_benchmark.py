"""Same-input genuine NativeCaps.current benchmark; clocks/facts/RF are models."""
import json
from pathlib import Path
import statistics
import time

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from lantern_meshticulum.bench.pilot_native_caps import _open_existing,NativeCapsError
from tests.test_fast_bundle import native,fast_data,one_part
from tests.test_pilot_bundle import data
from tests.test_public32_pilot import tmp_path,protected_source,clock,facts,BOOT

OUT=Path('/Users/l33tdawg/nodejs-projects/sage-commons/work/meshticulum-held-stage-read-candidate-20261014')


@pytest.mark.parametrize('part_count',[1,5])
def test_same_complete_current_path_with_only_stage_read_method_selected(native,part_count):
    n=native;previous=None
    for index in range(part_count):
        n['now'][0]=tuple(value+1000000 for value in n['now'][0])
        value,_,_=one_part(n,index,previous);previous=value['fast_progress_proof']
    caps=_open_existing(n['roots']['unit-c'],n['policy'].parent_id,facts('unit-c'),n['clock'])
    reader=caps._approve.reader;original=reader.read_held
    rows=[]
    try:
        for iteration in range(6):
            # ABBA alternation on the same owner, SQLite and held bytes. The
            # baseline path is the unchanged full Reader.read implementation.
            mode=('baseline','candidate','candidate','baseline')[iteration%4]
            reader.read_held=reader.read if mode=='baseline' else original
            for _ in range(8):assert caps.current() is True
            durations=[]
            for _ in range(40):
                begin=time.perf_counter_ns();assert caps.current() is True
                durations.append((time.perf_counter_ns()-begin)/1000)
            rows.append(dict(mode=mode,elapsed_us=durations))
        reader.read_held=original;assert caps.current() is True
        means={mode:statistics.median([t for row in rows if row['mode']==mode for t in row['elapsed_us']])
            for mode in ('baseline','candidate')}
        result=dict(source_only=True,native_measurement=False,clock_basis='local_perf_counter_ns',
            same_owner_same_SQLite_same_files=True,unchanged_runtime_layout='directory-source-control',
            prepared_claims=2*part_count,part_count=part_count,unit='unit-c',
            baseline_median_current_us=means['baseline'],candidate_median_current_us=means['candidate'],
            median_reduction_percent=100*(means['baseline']-means['candidate'])/means['baseline'],batches=rows)
        (OUT/('COMPLETE_CURRENT_'+str(part_count)+'_PARTS.json')).write_text(json.dumps(result,indent=2)+'\n')
    finally:
        reader.read_held=original;assert caps.close()


def test_complete_current_still_refuses_fresh_approval_revocation_with_held_stage(native):
    n=native;verified,_,_=one_part(n,0)
    caps=_open_existing(n['roots']['unit-c'],n['policy'].parent_id,facts('unit-c'),n['clock'])
    try:
        assert caps.current() is True
        approval=Path(str(caps.path)+'.approval')
        approval.write_bytes(b'0'*64+b'\n')
        assert caps.current() is False
        with pytest.raises(NativeCapsError):caps.snapshot()
    finally:assert caps.close() is True
