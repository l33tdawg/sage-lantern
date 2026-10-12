"""Real RPC approval and NativeCaps/guard, with explicitly modeled host facts.

This is a source regression test, not physical qualification. It uses the
existing local integration fixture for protected files, SQLite and seals; no
radio, serial channel, service, firmware or remote device is opened.
"""
import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from lantern_meshticulum.bench.owned_native_snapshot import collect_owned_native
from lantern_meshticulum.bench.pilot_parent_snapshot import Scope
from lantern_meshticulum.bench.resume_parent_guard import ResumeParentGuard
from tests.test_pilot_resume_rpc_integration import local_scope
from tests.test_pilot_parent_snapshot import root_observation


def test_owned_checkpoint_snapshot_uses_real_current_approval_contract(
        root_observation, local_scope, monkeypatch):
    state, runtime, payload, _, _ = local_scope
    rpc.enrolled_action('enroll', 'unit-a', payload, runtime)
    original_enter = ResumeParentGuard.__enter__
    snapshots = []

    def collect_during_actual_guard(guard):
        original_enter(guard)
        # The closure comes from enrolled_action itself, rather than a test
        # observer that would accidentally approve arbitrary phase strings.
        caps = guard.caps
        with pytest.raises(RuntimeError, match='own_approval_binding_changed'):
            caps._approve(rpc.canonical(dict(
                enrollment=caps._enrollment, phase='owned-snapshot')))
        before = caps.snapshot()
        path = state / 'parents' / caps.policy.parent_id / 'native.sqlite'
        scope = Scope(
            'meshticulum.public-parent-snapshot-scope.v1', 'native', 0,
            caps.policy.parent_id, caps.policy.digest,
            rpc.sha(path.read_bytes()), 'unit-a', caps.unit.host_boot_id,
            rpc.clock()[0] + 55000000,
        )
        actual = collect_owned_native(
            state, scope, caps=caps, clock=caps._clock,
            boot_reader=rpc.boot,
        ).to_dict()
        assert actual['status'] == 'scoped_rows_observed', actual.get('reason')
        assert actual['scoped_rows_complete'] is True
        assert actual['pending_unknown'] is False
        assert actual['database_writes'] is False
        assert caps.snapshot() == before
        assert caps.current() and guard.check()
        snapshots.append(actual)
        return guard

    monkeypatch.setattr(ResumeParentGuard, '__enter__', collect_during_actual_guard)
    result = rpc.enrolled_action('guard', 'unit-a', payload, runtime)
    assert len(snapshots) == 1
    assert result['snapshot']['claims'] == result['snapshot']['prepared'] == 0

