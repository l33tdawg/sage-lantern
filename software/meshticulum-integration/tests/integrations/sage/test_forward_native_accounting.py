"""Actual held files/SQLite; native root/boot/resource inputs are modeled."""
from dataclasses import replace
import fcntl
import os
import threading

import pytest

from lantern_meshticulum.sage.forward_budget import Cost
from lantern_meshticulum.sage.forward_native_accounting import HeldParentAccounting
from tests.test_pilot_bundle import tmp_path
from tests.test_pilot_parent_snapshot import fixture as ledger_fixture, root_observation, scope
from tests.integrations.sage.test_forward_native_owner import setup


@pytest.fixture
def held(tmp_path, root_observation):
    data = ledger_fixture(tmp_path)
    native = data['natives']['unit-a']
    with setup() as endpoint:
        selected = replace(scope(data), current_host_boot_id=endpoint.intention.host_boot_id,
                           observation_expires_epoch_us=150000000)
        create = lambda: HeldParentAccounting(endpoint.hardware, endpoint.intention,
            native['state'], selected, clock_reader=lambda: endpoint.state.now,
            boot_reader=lambda: endpoint.state.boot)
        source = create()
        try:
            yield source, endpoint, native, selected, create
        finally:
            source.close()


def test_native_bytes_supply_full_scoped_caps_without_global_or_actual_claims(held):
    source, endpoint, native, selected, _ = held
    original = {p: p.read_bytes() for p in native['path'].parent.iterdir() if p.is_file()}
    observed = source.observe(endpoint.hardware, endpoint.intention)
    evidence = source.evidence()
    expected = Cost(1, 1000, 1, 32, 32).plus(Cost(**native['child']['cost']))
    assert observed.retained_debt == expected
    assert observed.actual_usage is None
    assert observed.host_boot_id == endpoint.intention.host_boot_id
    assert evidence['complete_global_debt'] is False
    assert evidence['prior_lineage_verified'] is False
    assert evidence['private_dispatch_authority'] is False
    assert evidence['physical_retirement_proved'] is False
    assert evidence['actual_plus_prepaid_sum_permitted'] is False and evidence['refunds'] == 0
    assert {p: p.read_bytes() for p in original} == original
    # The original native producer's exclusive owner lock cannot be acquired.
    fd = os.open(str(native['path']) + '.owner', os.O_RDONLY)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_sql_interpretation_is_reused_but_every_observation_rereads_all_held_bytes(held, monkeypatch):
    source, endpoint, native, selected, _ = held
    import lantern_meshticulum.sage.forward_native_accounting as module
    monkeypatch.setattr(module.parent, '_native', lambda *args: pytest.fail('immutable SQL interpretation repeated'))
    original = os.pread
    reads = []
    def reread(fd, count, offset):
        reads.append(fd)
        return original(fd, count, offset)
    monkeypatch.setattr(os, 'pread', reread)
    one = source.observe(endpoint.hardware, endpoint.intention)
    two = source.observe(endpoint.hardware, endpoint.intention)
    assert one.binding() == two.binding()
    assert all(reads.count(fd) >= 2 for _, fd, _, _ in source._reader.held)


@pytest.mark.parametrize('name', ['native.sqlite-wal', 'native.sqlite-shm', 'native.sqlite-journal',
                                  'native.sqlite.pending'])
def test_appearing_sidecar_or_pending_marker_is_refused_not_treated_as_zero(held, name):
    source, endpoint, native, _, _ = held
    path = native['path'].parent / name
    path.write_bytes(b'')
    path.chmod(0o600)
    with pytest.raises(Exception):
        source.observe(endpoint.hardware, endpoint.intention)
    assert source._uncertain
    assert source.close() is False


@pytest.mark.parametrize('name', ['native.sqlite', 'native.sqlite.owner', 'native.sqlite.approval'])
def test_changed_named_native_input_refuses_after_warm_observation(held, name):
    source, endpoint, native, _, _ = held
    source.observe(endpoint.hardware, endpoint.intention)
    path = native['path'].parent / name
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(Exception):
        source.observe(endpoint.hardware, endpoint.intention)
    assert source._uncertain


def test_replaced_database_same_bytes_is_still_refused(held):
    source, endpoint, native, _, _ = held
    path = native['path']
    raw = path.read_bytes()
    path.unlink()
    path.write_bytes(raw)
    path.chmod(0o600)
    with pytest.raises(Exception):
        source.observe(endpoint.hardware, endpoint.intention)


@pytest.mark.parametrize('change', ['scope', 'debt', 'configuration', 'boot', 'closed-resource', 'expiry'])
def test_original_scope_cost_resource_and_lifetime_cannot_change(held, change):
    source, endpoint, native, selected, _ = held
    if change == 'scope':
        source.scope = replace(selected, observation_expires_epoch_us=selected.observation_expires_epoch_us + 1)
    elif change == 'debt':
        object.__setattr__(source._debt, 'radio_attempts', 0)
    elif change == 'configuration':
        endpoint.hardware.config['dispatch_enabled'] = False
    elif change == 'boot':
        endpoint.state.boot = '00000000-0000-4000-8000-000000000002'
    elif change == 'closed-resource':
        endpoint.hardware._closed = True
    else:
        endpoint.state.now = (selected.observation_expires_epoch_us, 100000000)
    with pytest.raises(Exception):
        source.observe(endpoint.hardware, endpoint.intention)


def test_raw_dto_cost_or_foreign_intention_is_not_accepted_as_live_evidence(held):
    source, endpoint, native, _, _ = held
    with pytest.raises(Exception):
        source.observe(endpoint.hardware, replace(endpoint.intention))


def test_constructor_refuses_a_different_database_pin(tmp_path, root_observation):
    data = ledger_fixture(tmp_path)
    native = data['natives']['unit-a']
    with setup() as endpoint:
        selected = replace(scope(data), current_host_boot_id=endpoint.intention.host_boot_id,
            observation_expires_epoch_us=150000000, database_sha256='0' * 64)
        with pytest.raises(Exception):
            HeldParentAccounting(endpoint.hardware, endpoint.intention, native['state'], selected,
                clock_reader=lambda: endpoint.state.now, boot_reader=lambda: endpoint.state.boot)
        fd = os.open(str(native['path']) + '.owner', os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


def test_cleanup_after_expiry_releases_owned_descriptions_without_new_permission(held):
    source, endpoint, native, selected, _ = held
    fds = tuple(source._reader._tokens)
    endpoint.state.now = (selected.observation_expires_epoch_us, 100000000)
    assert source.close() is True
    for fd in fds:
        with pytest.raises(OSError):
            os.fstat(fd)
    with pytest.raises(Exception):
        source.observe(endpoint.hardware, endpoint.intention)


def test_foreign_thread_cannot_observe_or_close_original_descriptions(held):
    source, endpoint, native, selected, _ = held
    results = []
    def foreign():
        results.append(source.close())
    thread = threading.Thread(target=foreign)
    thread.start()
    thread.join(2)
    assert not thread.is_alive() and results == [False]
    assert source.observe(endpoint.hardware, endpoint.intention).actual_usage is None


def test_cleanup_failure_stays_failed_on_repeat_and_contains_original_descriptors(held):
    source, _, _, _, _ = held
    fds = tuple(source._reader._tokens)
    source._reader.held = list(source._reader.held)
    assert source.close() is False
    assert source.close() is False
    assert source._uncertain
    for fd in fds:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_closed_metadata_descriptor_is_not_reported_as_known_successful_cleanup(held):
    source, _, _, _, _ = held
    _, fd, _, _ = source._reader.held[0]
    os.close(fd)
    assert source.close() is False and source.close() is False


def test_reentrant_cleanup_cannot_release_descriptors_during_observation(held, monkeypatch):
    source, endpoint, _, _, _ = held
    original = os.pread
    results = []
    def observing(fd, count, offset):
        if not results:
            results.append(source.close())
        return original(fd, count, offset)
    monkeypatch.setattr(os, 'pread', observing)
    assert source.observe(endpoint.hardware, endpoint.intention).actual_usage is None
    assert results == [False] and source._closed is False
    assert source.close() is True


def test_cleanup_from_arbitrary_foreign_permit_retains_held_source(held):
    source, endpoint, _, _, _ = held
    assert source.close_from_registry(object()) is False
    assert source.observe(endpoint.hardware, endpoint.intention).actual_usage is None
    assert source.close() is True
