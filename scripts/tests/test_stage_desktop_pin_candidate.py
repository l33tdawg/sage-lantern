"""Review snapshot integrity and absent activation, without device writes."""
import json
from pathlib import Path
import pytest
from test_desktop_native import load_script, ROOT


def inputs(tmp_path):
    value = json.loads((ROOT / 'work/c-desktop-native-guard-20261001/live-readback-complete.json').read_text())
    value.update(kiosk_uid=1000, kiosk_gid=1000)
    live = tmp_path / 'metadata.json'; live.write_text(json.dumps(value))
    return ROOT / 'work/c-desktop-pin-preflight-20261001/installed-source.json', \
        ROOT / 'work/c-desktop-pin-candidate-20261001/startup-source.json', live


def test_matched_bundle_is_hash_verified_and_has_no_activation_file(tmp_path):
    module = load_script('desktop_pin_stager_test', 'stage-desktop-pin-candidate.py')
    directory = tmp_path / 'review'
    value = module.stage(*inputs(tmp_path), directory)
    assert module.verify(directory) == len(value['files'])
    assert value['deployable'] is value['installed_on_c'] is value['desktop_pin_accepted'] is False
    targets = {item['target'] for item in value['files']}
    assert '/etc/sage-lantern/desktop-pin-activation.json' not in targets
    launch = json.loads((directory / 'payload/rootfs/etc/sage-lantern/desktop-launch-policy.json').read_text())
    assert set(launch['files']) == targets - {'/etc/sage-lantern/desktop-launch-policy.json'}
    assert (directory / 'payload/rootfs/etc/sage-lantern/session-profile').read_text() == 'wayland-mutter-native-pin\n'
    with pytest.raises(FileExistsError):
        module.stage(*inputs(tmp_path), directory)


@pytest.mark.parametrize('change', ['mutate', 'extra', 'missing', 'symlink'])
def test_candidate_payload_drift_or_extra_files_refuse(tmp_path, change):
    module = load_script('desktop_pin_stager_tamper_test', 'stage-desktop-pin-candidate.py')
    directory = tmp_path / 'review'
    value = module.stage(*inputs(tmp_path), directory)
    target = directory / value['files'][0]['bundle_path']
    if change == 'mutate':
        target.write_bytes(b'changed')
    elif change == 'extra':
        (directory / 'payload/extra').write_bytes(b'unknown')
    elif change == 'missing':
        target.unlink()
    else:
        original = target.read_bytes(); target.unlink()
        substitute = tmp_path / 'substitute'; substitute.write_bytes(original)
        target.symlink_to(substitute)
    with pytest.raises((ValueError, FileNotFoundError)):
        module.verify(directory)


def test_missing_kiosk_nss_observation_refuses_before_output(tmp_path):
    module = load_script('desktop_pin_stager_missing_test', 'stage-desktop-pin-candidate.py')
    preflight, startup, live = inputs(tmp_path)
    value = json.loads(live.read_text()); value.pop('kiosk_gid'); live.write_text(json.dumps(value))
    output = tmp_path / 'review'
    with pytest.raises(ValueError, match='nss'):
        module.stage(preflight, startup, live, output)
    assert not output.exists()
