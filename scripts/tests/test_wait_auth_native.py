import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('wait_auth_native',
    Path(__file__).resolve().parents[1] / 'lantern/wait-auth-native.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_transient_native_startup_failure_is_retried_before_admission():
    elapsed = [0]
    calls = []
    def probe():
        calls.append(elapsed[0])
        if len(calls) == 1:
            raise ConnectionRefusedError
        return len(calls) == 3
    module.wait_ready(probe, timeout=5, clock=lambda: elapsed[0],
                      sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    assert calls == [0, 1, 2]


def test_unconfirmed_or_truthy_status_never_admits_auth():
    elapsed = [0]
    with pytest.raises(TimeoutError):
        module.wait_ready(lambda: 1, timeout=3, clock=lambda: elapsed[0],
                          sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    assert elapsed[0] == 3
