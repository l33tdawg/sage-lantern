import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SPEC = importlib.util.spec_from_file_location('boot_phase', Path(__file__).parents[1] / 'lantern/provision-fresh-appliance.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def arguments(**changes):
    values = dict(unit_id='unit-a', phase='boot-theme', app_options=[], allow_native_build=False,
        install_os_packages=False, execute=False, apply_boot_theme=False,
        boot_theme_manifest=None, boot_theme_plan=None)
    values.update(changes)
    return SimpleNamespace(**values)


def test_readonly_command_and_release_verification(monkeypatch):
    verify = Mock(return_value=Path('/release/payload'))
    monkeypatch.setattr(MODULE, 'verify_release', verify)
    run = Mock()
    result = MODULE.execute(arguments(), run=run)
    verify.assert_called_once()
    run.assert_not_called()
    assert result['commands'] == [['/usr/bin/python3', '-I', '-B',
        '/release/payload/scripts/lantern/install-boot-theme.py']]
    assert result['ready'] is False


def test_exact_apply_command():
    result = MODULE.commands(arguments(execute=True, apply_boot_theme=True,
        boot_theme_manifest='a' * 64, boot_theme_plan='b' * 64), Path('/release/payload'))
    assert result[0][-5:] == ['--apply', '--expected-manifest', 'a' * 64, '--expected-plan', 'b' * 64]


@pytest.mark.parametrize('changes', [dict(apply_boot_theme=True), dict(boot_theme_plan='b' * 64),
    dict(install_os_packages=True), dict(app_options=['--foo']), dict(startup_expected='file'),
    dict(allow_native_build=True), dict(execute=True, apply_boot_theme=True,
        boot_theme_manifest='wrong', boot_theme_plan='b' * 64)])
def test_mixed_or_unbound_options_rejected(changes):
    with pytest.raises(ValueError):
        MODULE.commands(arguments(**changes), Path('/release/payload'))


def test_release_failure_never_runs_child(monkeypatch):
    def fail(args):
        raise ValueError('release mismatch')
    monkeypatch.setattr(MODULE, 'verify_release', fail)
    run = Mock()
    with pytest.raises(ValueError):
        MODULE.execute(arguments(execute=True), run=run)
    run.assert_not_called()
