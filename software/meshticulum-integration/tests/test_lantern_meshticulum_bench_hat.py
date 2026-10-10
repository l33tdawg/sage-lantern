import hashlib, importlib.util, json
from pathlib import Path
import pytest
from meshticulum.bench.authority import RunAuthority
from tests.test_meshticulum_bench_runtime import Clock, BOOT
def overlay_tool():
    file = Path(__file__).resolve().parents[3] / 'firmware/meshticulum-bench/hat_overlay.py'
    spec = importlib.util.spec_from_file_location('mtbench_overlay', file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_overlay_rollback_preserves_later_unrelated_boot_edits():
    module = overlay_tool()
    before = b'console=both\nuser_overlays=camera\nfdtfile=original\n'
    after = module.edit_environment(before, True)
    later = after.replace(b'console=both', b'console=serial') + b'usbquirks=new\n'
    undone = module.edit_environment(later, False)
    assert undone == before.replace(b'console=both', b'console=serial') + b'usbquirks=new\n'
    assert module.edit_environment(undone, False) == undone


@pytest.mark.parametrize('raw', [b'user_overlays=a\nuser_overlays=b\n',
                                 b'user_overlays=a;reboot\n', b'user_overlays=a a\n'])
def test_ambiguous_boot_overlay_settings_are_refused(raw):
    with pytest.raises(RuntimeError):
        overlay_tool().edit_environment(raw, True)


def test_activation_refuses_stale_plan_before_any_state_directory(tmp_path, monkeypatch):
    module = overlay_tool()
    monkeypatch.setattr(module, 'STATE', tmp_path/'never-created')
    monkeypatch.setattr(module, 'create_plan', lambda overlay: {'boot_id': 'new-boot'})
    with pytest.raises(RuntimeError, match='plan_stale'):
        module.apply({'boot_id': 'old-boot'}, tmp_path/'not-opened.dtbo')
    assert not (tmp_path/'never-created').exists()
