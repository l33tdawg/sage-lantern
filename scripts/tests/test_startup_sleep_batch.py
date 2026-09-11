import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'software/runtime/src'))
from sage_lantern.suspend_system import SLEEP_UNITS, SuspendSystem

SPEC = importlib.util.spec_from_file_location('sleep_batch_recovery', ROOT /
    'scripts/lantern/recover-cross-boot-audio.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def system_for(rows, batch_fault=None):
    system = object.__new__(SuspendSystem)
    calls = []
    def command(arguments, deadline):
        assert deadline == 10
        calls.append(arguments)
        if '-p' not in arguments:
            return '\n'.join(f'{key}={value}' for key, value in rows[arguments[-1]].items() if key != 'Id')
        assert arguments[:3] == ['show', '--no-pager', '--all']
        assert arguments[-len(SLEEP_UNITS):] == list(SLEEP_UNITS)
        blocks = ['\n'.join(f'{key}={value}' for key, value in row.items()) for row in reversed(list(rows.values()))]
        if batch_fault == 'missing':
            blocks.pop()
        elif batch_fault == 'duplicate':
            blocks.append(blocks[0])
        elif batch_fault == 'foreign':
            blocks[0] = blocks[0].replace('Id=', 'Id=foreign-')
        elif batch_fault == 'duplicate_field':
            blocks[0] += '\nJob=0'
        elif batch_fault == 'missing_field':
            blocks[0] = '\n'.join(line for line in blocks[0].splitlines() if not line.startswith('Job='))
        return '\n\n'.join(blocks) + '\n'
    system._command = command
    return system, calls


def observations():
    return {unit: dict(Id=unit, LoadState='loaded', ActiveState='inactive', Job='') for unit in SLEEP_UNITS}


@pytest.mark.parametrize('unit', SLEEP_UNITS)
@pytest.mark.parametrize('field,value', [('ActiveState', 'active'), ('ActiveState', 'deactivating'),
    ('ActiveState', 'unknown'), ('Job', '1'), ('Job', 'bad'), ('LoadState', 'not-found'),
    ('LoadState', 'error'), ('Job', '0')])
def test_batch_preserves_each_original_gate(unit, field, value):
    rows = observations()
    rows[unit][field] = value
    system, calls = system_for(rows)
    try:
        system._sleep_idle(10)
        accepted = True
    except Exception:
        accepted = False
    if accepted:
        MODULE.sleep_idle_batch(system, 10)
    else:
        with pytest.raises(ValueError):
            MODULE.sleep_idle_batch(system, 10)


def test_measured_show_calls_nine_to_one_without_reusing_observations():
    system, calls = system_for(observations())
    system._sleep_idle(10)
    assert len(calls) == 9
    calls.clear()
    MODULE.sleep_idle_batch(system, 10)
    assert len(calls) == 1
    MODULE.sleep_idle_batch(system, 10)
    assert len(calls) == 2


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'foreign', 'duplicate_field', 'missing_field'])
def test_batch_requires_complete_unambiguous_unit_identity(fault):
    system, calls = system_for(observations(), fault)
    with pytest.raises(ValueError):
        MODULE.sleep_idle_batch(system, 10)


def test_next_check_observes_new_sleep_job():
    rows = observations()
    system, calls = system_for(rows)
    MODULE.sleep_idle_batch(system, 10)
    rows['suspend.target']['Job'] = '123'
    with pytest.raises(ValueError):
        MODULE.sleep_idle_batch(system, 10)
    assert len(calls) == 2
