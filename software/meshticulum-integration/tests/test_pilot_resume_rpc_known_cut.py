# SPDX-License-Identifier: Apache-2.0
"""Genuine IpJournal writer and existing typed parser; no native/RF evidence."""
from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest
from meshticulum.contracts.identity import PeerID
from meshticulum.ip.authority import grant_from_plan
from meshticulum.ip.journal import IpJournal
from meshticulum.ip.wire import IpLimits
from lantern_meshticulum.bench import pilot_resume_rpc as rpc, pilot_debt_snapshot as debt

BOOT='11111111-1111-4111-8111-111111111111'


def written_journal(path,attack):
    peers=[asdict(PeerID('known-cut-local-control',c*64)) for c in 'bc']
    plan=dict(schema='meshticulum.ip-service-run.v1',host_boot_id=BOOT,local_peer=peers[1],
        session=dict(initiator=peers[0],responder=peers[1],session_id='a'*32,generation='b'*32),
        format='MR1.response',object=dict(bytes=1068,sha256='c'*64),route_binding_digest='d'*64,
        issued_epoch_us=100000000,expires_epoch_us=180000000,application_expires_epoch_us=180000000,
        application_deadline_monotonic_us=880000000,initiator_address=['127.0.0.1',35001],
        responder_address=['127.0.0.1',35002],limits=asdict(IpLimits()))
    grant=grant_from_plan(plan,b'x'*32,local_peer=PeerID(**peers[1]),host_boot_id=BOOT)
    journal=IpJournal(path,grant,create=True)
    packet=b'a'*74;token=journal.reserve(packet,'ACK')
    journal.reserve_receive(512)
    if attack not in ('pending_rx','unknown_rx'):journal.settle_receive(511 if attack=='wrong_cut' else 512)
    if attack=='unknown_tx': journal.progress(token,packet,10);journal.settle(token,packet,'UNKNOWN')
    elif attack!='pending_tx':journal.progress(token,packet,74);journal.settle(token,packet,'KERNEL_ACCEPTED')
    if attack!='not_fenced':journal.fence()
    journal.close()
    if attack=='unknown_rx':
        recovered=IpJournal(path,grant);recovered.close()  # Actual pending -> unknown recovery writer.
    return grant


@pytest.mark.parametrize('attack',[None,'unknown_tx','pending_tx','pending_rx','unknown_rx','wrong_cut','not_fenced'])
def test_real_fence_and_typed_read_distinguish_known_bytes_from_uncertainty(attack):
    base=Path(tempfile.mkdtemp(prefix='known-cut-controls-',dir=Path(__file__).parent));base.chmod(0o700)
    path=base/'bytes.sqlite'
    try:
        grant=written_journal(path,attack);before=path.read_bytes()
        class HeldImage:
            @contextmanager
            def database(self,name,owner=False):
                assert name=='bytes.sqlite' and owner is True
                raw=bytearray(path.read_bytes());raw[18]=raw[19]=1
                db=sqlite3.connect(':memory:');db.deserialize(bytes(raw))
                db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
                try:yield db
                finally:db.close()
        run=SimpleNamespace(transport='tcp',attempt_binding_sha256=grant.digest.hex())
        typed=debt._journal(HeldImage(),run,dict(context_sha256=grant.digest.hex(),caps=dict(
            radio_attempts=0,estimated_airtime_us=0,ip_frames=grant.limits.max_frames,
            tx_stream_bytes=grant.limits.max_tx_stream_bytes,rx_stream_bytes=grant.limits.max_rx_stream_bytes)))
        original=json.dumps(typed,sort_keys=True)
        if attack is None:
            assert typed['has_unknown_or_pending'] is True and typed['owner']['fenced'] is True
            assert typed['owner']['rx_settled']==512 and typed['owner']['rx_unknown']==typed['owner']['rx_pending']==0
            assert rpc.qualify_cut_journal(typed) is None
            assert typed['actual_cost']==dict(radio_attempts=0,estimated_airtime_us=0,ip_frames=1,
                                              tx_stream_bytes=74,rx_stream_bytes=512)
        else:
            with pytest.raises(RuntimeError,match='fenced_cut_has_unsettled_bytes'):rpc.qualify_cut_journal(typed)
        assert json.dumps(typed,sort_keys=True)==original and path.read_bytes()==before
    finally:shutil.rmtree(base)
