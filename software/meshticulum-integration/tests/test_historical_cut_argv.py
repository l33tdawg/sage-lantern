# SPDX-License-Identifier: Apache-2.0
"""Recorded H38 owner/argv pins with modeled surrounding native records."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc, pilot_ssh as ssh


CASES = json.loads((Path(__file__).parent/'fixtures/historical_cut_argv.json').read_text())['cases']


def command(case, launcher, cutoff='512'):
    owner = case['owner']
    argv = [owner.get('native_python', {}).get('unit-c', '/usr/bin/python3'), '-I', '-B', '-c',
        launcher, '/opt/meshticulum-bench/runtime-'+owner['runtime_sha256'][:16]+'/bench_node.py',
        owner['manifest_sha256'], 'ip-service-run', 'unit-c', '--run-id', case['run_id'],
        '--test-rx-cutoff', cutoff]
    return rpc.sha(b'\0'.join(a.encode() for a in argv)+b'\0')


def qualify(case, tmp_path, monkeypatch, fault=None):
    """Run the complete predicate without replacing its hashes or comparisons."""
    monkeypatch.setattr(rpc, 'STATE', tmp_path)
    run = case['run_id']; owner = deepcopy(case['owner'])
    caps = dict(radio_attempts=0, estimated_airtime_us=0, ip_frames=2048,
                tx_stream_bytes=1048576, rx_stream_bytes=1048576)
    plan = dict(format='MR1.response', session=dict(session_id=run))
    meta = dict(unit='unit-c', host_boot_id='local-control', run_id=run, campaign_id='c'*32,
        resource_sha256='a'*64, sends=False, sender='unit-b', receiver='unit-c', transport='tcp',
        format='MR1.response', application_expires_epoch_us=1, grant_digest='b'*64,
        secret_sha256='c'*64, plan_sha256='d'*64, cipher_frame_limit=2048,
        object=dict(bytes=1068, sha256='e'*64))
    receipt = dict(child_caps=dict(metadata=meta, digest='f'*64, cost=caps),
                   native_plan_sha256=rpc.sha(rpc.canonical(plan)))
    result = dict(complete=False, error='IpError:explicit_public_pilot_tcp_loss',
        test_tcp_loss=dict(injected=True, requested_rx_stream_bytes=512, known_rx_stream_bytes=512),
        byte_journal=dict(fenced=True), session_id=run, service_format='MR1.response',
        parent=dict(receipt, schema='meshticulum.native-parent-start.v1', started=True),
        native_job=dict(command_sha256=case['command_sha256']))
    row = dict(run_id=run, transport='tcp', status='scoped_rows_observed', wire=dict(retired=1),
        journal=dict(has_unknown_or_pending=True,
            owner=dict(fenced=True, rx_pending=0, rx_unknown=0, rx_settled=512),
            attempts=[dict(state='KERNEL_ACCEPTED', lane='ACK', written_bytes=74, reserved_bytes=74)]),
        actual_cost=dict(radio_attempts=0, estimated_airtime_us=0, ip_frames=1,
                         tx_stream_bytes=74, rx_stream_bytes=512),
        admitted_caps=caps, conservative_unknown_caps=caps,
        terminal_metadata=dict(command_sha256=case['command_sha256']))
    if fault == 'terminal_digest': row['terminal_metadata']['command_sha256'] = '0'*64
    elif fault == 'result_digest': result['native_job']['command_sha256'] = '0'*64
    elif fault == 'runtime': owner['runtime_sha256'] = '0'*64
    elif fault == 'manifest': owner['manifest_sha256'] = '0'*64
    elif fault == 'python': owner['native_python'] = {'unit-c': '/other/python3'}
    elif fault == 'archived_bytes': monkeypatch.setattr(ssh, 'HISTORICAL_INVOKE_V1', ssh.HISTORICAL_INVOKE_V1+'\n')
    raw = rpc.canonical(result)
    row['files'] = {'result.json': dict(sha256=rpc.sha(raw))}
    observed = dict(detail=dict(claims=[dict(child=rpc._child_ref(receipt['child_caps']),
        started=True, prepared=True, prepared_row_sha256=rpc.sha(rpc.canonical(receipt)))]))
    files = {'result.json': raw, 'provision.json': rpc.canonical(dict(parent=receipt)),
             'grant.json': rpc.canonical(plan)}
    class Reader:
        def read(self, path, limit):
            assert path.parent == tmp_path/'runs'/run
            assert len(files[path.name]) <= limit
            return files[path.name]
        def check(self): pass
    before = rpc.canonical(dict(owner=owner, row=row, observed=observed))
    value = rpc.qualify_known_cut(Reader(), 'unit-c', SimpleNamespace(parent_id=case['parent_id']),
                                  owner, observed, row, run)
    assert rpc.canonical(dict(owner=owner, row=row, observed=observed)) == before
    assert value == dict(run_id=run, classification='intentional_known_512_byte_receiver_cut',
                         fenced_preserved=True, refunds=0, full_caps_retained=True)


def test_archived_launcher_bytes_remain_pinned():
    assert rpc.sha(ssh.HISTORICAL_INVOKE_V1.encode()) == 'd0d6e2db49802f3325228fb3eb50730eb43306803056e0f9a6862f7e258dfca5'


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['parent_id'][:8])
def test_h38_recorded_commands_qualify_with_exact_archived_launcher(case, tmp_path, monkeypatch):
    assert command(case, ssh.HISTORICAL_INVOKE_V1) == case['command_sha256']
    assert command(case, ssh.INVOKE) != case['command_sha256']
    qualify(case, tmp_path, monkeypatch)


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['parent_id'][:8])
@pytest.mark.parametrize('fault', ['terminal_digest', 'result_digest', 'runtime', 'manifest', 'python', 'archived_bytes'])
def test_historical_command_comparison_still_refuses_changes(case, fault, tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match='fenced_cut_native_argv_changed'):
        qualify(case, tmp_path, monkeypatch, fault)


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['parent_id'][:8])
@pytest.mark.parametrize('launcher,cutoff', [('current', '512'), ('historical', '511')])
def test_historical_rows_do_not_accept_current_or_changed_argv(case, launcher, cutoff, tmp_path, monkeypatch):
    changed = deepcopy(case)
    changed['command_sha256'] = command(case, ssh.INVOKE if launcher == 'current' else ssh.HISTORICAL_INVOKE_V1, cutoff)
    with pytest.raises(RuntimeError, match='fenced_cut_native_argv_changed'):
        qualify(changed, tmp_path, monkeypatch)


@pytest.mark.parametrize('old_launcher', [False, True])
def test_unrecorded_parent_uses_current_launcher_even_with_old_run_id(old_launcher, tmp_path, monkeypatch):
    case = deepcopy(CASES[0]); case['parent_id'] = 'e'*32
    case['owner']['native_python'] = {'unit-c': '/usr/bin/python3'}
    case['command_sha256'] = command(case, ssh.HISTORICAL_INVOKE_V1 if old_launcher else ssh.INVOKE)
    if old_launcher:
        with pytest.raises(RuntimeError, match='fenced_cut_native_argv_changed'):
            qualify(case, tmp_path, monkeypatch)
    else:
        qualify(case, tmp_path, monkeypatch)
