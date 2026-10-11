# SPDX-License-Identifier: Apache-2.0
"""Owned NativeCaps reads remain usable during WAL and scoped debt checks."""
from dataclasses import replace
import hashlib

from test_pilot_debt_snapshot import fixture as debt_fixture
from test_pilot_native_caps import Observers, enroll, policy, prepared
from test_pilot_parent_snapshot import root_observation
from lantern_meshticulum.bench import pilot_parent_snapshot as snapshot


def test_checkpoint_projection_uses_live_owner_and_reads_legacy_scope(
        tmp_path, root_observation):
    from lantern_meshticulum.bench import pilot_debt_snapshot as debt
    from lantern_meshticulum.bench.owned_native_snapshot import (
        collect_owned_native, match_checkpoint_projection,
    )

    state, _, _, legacy = debt_fixture(tmp_path, transport='lora')
    parent = policy()
    observed = Observers(parent)
    parents = state / 'parents'
    parents.mkdir(mode=0o700)
    directory = parents / parent.parent_id
    directory.mkdir(mode=0o700)
    path = directory / 'native.sqlite'
    caps = enroll(path, parent, observed)
    try:
        prepared(caps, parent)
        assert caps.current()
        expiry = observed.epoch + 55000000
        legacy = replace(legacy, observation_expires_epoch_us=expiry)
        scope = snapshot.Scope(
            'meshticulum.public-parent-snapshot-scope.v1', 'native', 0,
            parent.parent_id, parent.digest, hashlib.sha256(path.read_bytes()).hexdigest(),
            observed.unit, observed.boot, expiry,
        )
        actual = collect_owned_native(
            state, scope, caps=caps, clock=caps._clock,
            boot_reader=lambda: observed.boot, legacy_scope=legacy,
        ).to_dict()
        assert actual['status'] == 'scoped_rows_observed', (actual.get('reason'), actual.get('legacy_runs'))
        assert actual['legacy_scoped_rows_complete'] is True
        assert len(actual['legacy_runs']) == 1
        # The independent debt fixture intentionally has no matching native
        # allocation; the snapshot must preserve that unlinked status.
        assert actual['legacy_relation']['scoped_linkage_complete'] is False
        assert match_checkpoint_projection(
            actual, actual, scope=scope, handoff_epoch_us=expiry + 1,
            handoff_monotonic_us=observed.mono + 55000001,
        )
        assert caps.current() and not caps._closed
    finally:
        assert caps.close()
