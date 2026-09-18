"""A read-only contact-list stall must not fail the whole radio operation.

Unit A's stock firmware stalls part-way through a contact record (about 1 read in
32 in the 09:43 capture, and several per minute on 2026-09-13 16:19-16:22). The
transport resynchronises those read-only failures instead of poisoning, but every
operation still refreshes the inventory first, so a single stall used to fail an
owner send before its queue write and to fail each dispatch before any fragment
went out. These tests pin the bounded re-read and, just as importantly, every case
where it must NOT happen.
"""

from types import SimpleNamespace

import pytest

from sage_lantern.mesh_runtime import INVENTORY_ATTEMPTS, MeshRuntime
from sage_lantern.meshcore_transport import MeshCoreError


def runtime():
    stub = SimpleNamespace(_access=lambda: True)
    stub._resynced_read_failure = lambda device: MeshRuntime._resynced_read_failure(stub, device)
    stub._readonly_read_failure = lambda device: MeshRuntime._readonly_read_failure(stub, device)
    return stub


class Device:
    """Minimal radio double: each entry in failures consumes one refused read."""

    def __init__(self, failures, **state):
        self.failures = list(failures)
        self.calls = 0
        self.poisoned = state.get('poisoned', False)
        self.discarded_bytes = state.get('discarded_bytes', 148)
        code = state.get('code', 'serial_timeout')
        self.read_failure = state.get('read_failure', dict(command='contact-list', sending=False,
            receiving=False, error=code, frames=1))

    def get_contacts(self, *, authorize):
        self.calls += 1
        if self.failures and self.failures.pop(0):
            raise MeshCoreError('serial_unavailable')
        return 'inventory'


def test_one_resynchronised_stall_is_re_read_instead_of_failing_the_operation():
    device = Device([True, False])
    assert MeshRuntime._inventory(runtime(), device, lambda: True) == 'inventory'
    assert device.calls == 2


def test_repeated_stalls_stop_at_the_bounded_attempt_count():
    device = Device([True, True, True, False])
    with pytest.raises(MeshCoreError):
        MeshRuntime._inventory(runtime(), device, lambda: True)
    assert device.calls == INVENTORY_ATTEMPTS == 3


def test_a_complete_but_unexpected_frame_is_reread_without_discarded_bytes():
    """Unit A's firmware answers the contact read this way; nothing to discard."""
    device = Device([True, False], code='unexpected_response', discarded_bytes=0)
    assert MeshRuntime._inventory(runtime(), device, lambda: True) == 'inventory'
    assert device.calls == 2


def test_a_failed_refresh_falls_back_to_the_inventory_already_read():
    """One failed refresh must not fail an owner send or a dispatch wake: the
    registry owns identity and consent, the firmware table only routes."""
    device = Device([True, True, True], code='unexpected_response', discarded_bytes=0)
    stub = runtime()
    stub._local = SimpleNamespace(inventory='last-known')
    assert MeshRuntime._inventory(stub, device, lambda: True) == 'last-known'
    assert device.calls == INVENTORY_ATTEMPTS


def test_a_refresh_that_outlives_the_job_budget_falls_back_to_the_cached_inventory(monkeypatch):
    """Each part-way stall costs the transport timeout; the second one must end
    the re-read so the dispatch wake still has room for its fragment. Unit A's
    dispatch jobs died on the job deadline with only the contact-read command
    frame written (mesh_background_failure 2026-09-18 15:48)."""
    import sage_lantern.mesh_runtime as runtime_module
    now = [0.0]
    monkeypatch.setattr(runtime_module, 'time', SimpleNamespace(monotonic=lambda: now[0]))

    def stalled_read(*, authorize):
        now[0] += 3.0
        raise MeshCoreError('serial_unavailable')

    device = SimpleNamespace(get_contacts=stalled_read, poisoned=False, discarded_bytes=148,
        read_failure=dict(command='contact-list', sending=False, receiving=False,
                          error='serial_timeout', frames=1))
    stub = runtime()
    stub._local = SimpleNamespace(inventory='last-known', background_deadline=10.0)
    assert MeshRuntime._inventory(stub, device, lambda: True) == 'last-known'
    assert now[0] == 6.0, 'the refresh stopped before the job deadline'


def test_a_first_read_with_nothing_cached_still_fails_closed():
    device = Device([True, True, True], code='unexpected_response', discarded_bytes=0)
    stub = runtime()
    stub._local = SimpleNamespace()
    with pytest.raises(MeshCoreError):
        MeshRuntime._inventory(stub, device, lambda: True)
    assert device.calls == INVENTORY_ATTEMPTS


def test_a_successful_read_becomes_the_runtime_cache():
    """The job-local inventory is cleared with every context, so the wake that
    must fall back needs the last inventory any operation read."""
    device = Device([False])
    stub = runtime()
    assert MeshRuntime._inventory(stub, device, lambda: True) == 'inventory'
    assert stub._last_inventory == 'inventory'


def test_a_read_only_stall_reuses_the_last_inventory_any_operation_read():
    device = Device([True, True, True], code='serial_timeout', discarded_bytes=0)
    stub = runtime()
    stub._last_inventory = 'last-good'
    assert MeshRuntime._inventory(stub, device, lambda: True) == 'last-good'
    assert device.calls == 1


def test_a_send_or_receive_failure_never_reuses_an_inventory():
    device = Device([True, True, True],
        read_failure=dict(command='send', sending=True, receiving=False, error='serial_timeout', frames=1))
    stub = runtime()
    stub._last_inventory = 'last-good'
    with pytest.raises(MeshCoreError):
        MeshRuntime._inventory(stub, device, lambda: True)


def test_a_poisoned_transport_never_falls_back_to_a_cached_inventory():
    device = Device([True, True, True], poisoned=True)
    stub = runtime()
    stub._local = SimpleNamespace(inventory='last-known')
    with pytest.raises(MeshCoreError):
        MeshRuntime._inventory(stub, device, lambda: True)
    assert device.calls == 1


@pytest.mark.parametrize('state', [
    dict(poisoned=True),                                        # the transport latched
    dict(code='handshake_required'),                            # no resynchronisation
    dict(discarded_bytes=0),                                    # nothing was discarded
    dict(read_failure=dict(command='send', sending=True, receiving=False, error='serial_timeout', frames=1)),
    dict(read_failure=dict(command='receive', sending=False, receiving=True, error='serial_timeout', frames=1)),
    dict(read_failure=None),                                    # no failure recorded
])
def test_only_a_resynchronised_read_failure_is_reread(state):
    device = Device([True, False], **state)
    with pytest.raises(MeshCoreError):
        MeshRuntime._inventory(runtime(), device, lambda: True)
    assert device.calls == 1
