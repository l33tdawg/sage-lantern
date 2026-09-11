import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'scripts/lantern/camera-vision-defaults.py'
SPEC = importlib.util.spec_from_file_location('camera_defaults', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CAMERA = '/dev/v4l/by-id/usb-reviewed-A-video-index0'


def test_camera_provisioning_requires_guarded_vision_not_ready_marker():
    value = MODULE.policy(CAMERA)
    assert value['requested_by_default'] is True and value['readiness_verified'] is False
    assert value['camera_device']==CAMERA
    assert 'probe' in value['activation'] and 'commissioning' in value['activation']
    source=(ROOT/'scripts/lantern/install-camera-local.sh').read_text()
    assert source.index('camera-vision-defaults.py" --device "$camera_device" --inspect') < source.index('install -d -o root')
    assert source.index('camera-vision-defaults.py" --device "$camera_device" --install') < source.index('systemctl restart')
    assert '--photo-ready' not in source and '--mmproj' not in source


def test_camera_less_unit_never_claims_camera_or_vision():
    result=subprocess.run(['python3','-I','-B',str(SOURCE),'--no-camera'],check=True,capture_output=True,text=True)
    assert json.loads(result.stdout)==dict(camera_configured=False,vision_required=False,readiness_verified=False)


@pytest.mark.parametrize('device',['/dev/video0','/dev/v4l/by-id/../../video0',None,''])
def test_default_refuses_missing_or_unstable_camera(device):
    with pytest.raises(ValueError): MODULE.policy(device)


def test_default_install_is_exclusive_and_durable(tmp_path,monkeypatch):
    path=tmp_path/'policy.json'
    monkeypatch.setattr(MODULE,'PATH',path)
    raw=MODULE.canonical(MODULE.policy(CAMERA))
    monkeypatch.setattr(MODULE,'inspect',lambda device: raw)
    assert MODULE.install(CAMERA)==raw and path.read_bytes()==raw
    assert path.stat().st_mode&0o777==0o644
    with pytest.raises(FileExistsError): MODULE.install(CAMERA)
    assert path.read_bytes()==raw
