import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('backup', Path(__file__).parents[1] / 'lantern/backup-b-esp-flash.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def runner(calls, *, fail=None, mismatch=False):
    def run(args, **kwargs):
        calls.append(args)
        destination = Path(args[-1])
        offset = int(args[-3], 16)
        content = bytes([offset // MODULE.CHUNK + (1 if mismatch and 'second' in destination.name else 0)]) * MODULE.CHUNK
        with MODULE.new_file(destination) as output:
            output.write(content if fail != len(calls) else content[:128])
        return SimpleNamespace(returncode=1 if fail == len(calls) else 0)
    return run


def test_complete_exact_two_passes(tmp_path):
    calls = []
    result = MODULE.backup(tmp_path, Path('/python'), 'audio', observer=lambda kind: {'kind': kind}, runner=runner(calls))
    assert len(calls) == 32
    assert result['bytes'] == 16777216
    assert (tmp_path / 'factory-full-16MiB.bin').stat().st_size == 16777216
    assert (tmp_path / 'complete.json').exists()
    assert all('read-flash' in args and 'write-flash' not in args and MODULE.stable_path('audio') in args for args in calls)


@pytest.mark.parametrize('fail', [1, 5, 32])
def test_failure_preserves_partial_and_no_assembly(tmp_path, fail):
    calls = []
    with pytest.raises(RuntimeError, match='read failed'):
        MODULE.backup(tmp_path, Path('/python'), 'radio', observer=lambda kind: {}, runner=runner(calls, fail=fail))
    assert len(calls) == fail
    assert Path(calls[-1][-1]).stat().st_size == 128
    assert not (tmp_path / 'factory-full-16MiB.bin').exists()
    assert not (tmp_path / 'complete.json').exists()


def test_mismatch_stops(tmp_path):
    calls = []
    with pytest.raises(RuntimeError, match='mismatch'):
        MODULE.backup(tmp_path, Path('/python'), 'audio', observer=lambda kind: {}, runner=runner(calls, mismatch=True))
    assert len(calls) == 2


def test_existing_destination_refused(tmp_path):
    (tmp_path / '00000000-first.bin').symlink_to('/nonexistent')
    calls = []
    with pytest.raises(RuntimeError, match='destination exists'):
        MODULE.backup(tmp_path, Path('/python'), 'audio', observer=lambda kind: {}, runner=runner(calls))
    assert not calls


def test_rom_command_and_bounds():
    args = MODULE.command('/python', 'radio', 0xf00000, '/new', True)
    assert '--no-stub' in args and '--flash-size' in args
    with pytest.raises(RuntimeError, match='span'):
        MODULE.command('/python', 'radio', 0x1000000, '/new', False)


def test_device_change_before_call(tmp_path):
    observations = iter([{}, {'changed': True}])
    calls = []
    with pytest.raises(RuntimeError, match='device changed'):
        MODULE.backup(tmp_path, Path('/python'), 'audio', observer=lambda kind: next(observations), runner=runner(calls))
    assert not calls
