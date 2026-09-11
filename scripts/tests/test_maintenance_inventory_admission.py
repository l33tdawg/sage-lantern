import fcntl
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'software/runtime/tests'))
from test_mesh_contact_owner import owner
from test_maintenance_owner_ledgers import attempt
from test_recover_cross_boot_audio import MODULE, SLOT, records


def test_actual_internal_entry_requires_retained_locks_and_fresh_guard(owner, tmp_path):
    _, ledger, state = owner
    row = attempt(ledger, 'accepted')
    actor = ledger.owner
    payload = dict(schema=records.CONTROL_SCHEMA, binding=actor, enabled=True,
        generation=SLOT, history_generation=None, dispatch_epoch=SLOT, clear_epoch=SLOT, suppressed=[])
    control = dict(row, kind='conversation_control', record_id=records.record_id(actor), payload=payload)
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    path = tmp_path / 'owner.lock'
    path.write_bytes(b'')
    descriptor = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backend.expected = dict(actor=actor, tree={'owner.lock': dict(metadata=MODULE.metadata(os.fstat(descriptor)))})
        backend.locks = [descriptor]
        calls = []
        def guard():
            calls.append('fresh')
            return True
        result = backend.inspect_inventory([control, row], ledger.store, ledger.identity, guard, {'receiver_slot': SLOT})
        assert result['historical_outcomes'] == 'unknown' and result['records'] == 2
        assert len(calls) == 2 and state['puts'] == 0
        with pytest.raises(ValueError):
            backend.inspect_inventory([control, row], ledger.store, ledger.identity, lambda: False, {'receiver_slot': SLOT})
        backend.locks = []
        with pytest.raises(ValueError):
            backend.inspect_inventory([control, row], ledger.store, ledger.identity, guard, {'receiver_slot': SLOT})
        backend.locks = [descriptor]
        path.write_bytes(b'changed')
        with pytest.raises(ValueError):
            backend.inspect_inventory([control, row], ledger.store, ledger.identity, guard, {'receiver_slot': SLOT})
    finally:
        os.close(descriptor)
