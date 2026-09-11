import ast
import importlib.util
import json
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / 'lantern/diagnose-fresh-basic-resume.py'
SPEC = importlib.util.spec_from_file_location('basic_diagnostic', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_report_does_not_print_exception_message_locals_or_source(capsys):
    def failure():
        sensitive_fixture = 'must-not-appear'
        raise ValueError(sensitive_fixture)
    assert MODULE.report('check', failure) is False
    raw = capsys.readouterr().out
    assert 'must-not-appear' not in raw and 'sensitive_fixture' not in raw
    value = json.loads(raw)
    assert value['error_type'] == 'ValueError' and value['mutation_authorized'] is False
    assert all(set(frame) == {'file', 'line', 'function'} for frame in value['frames'])


def test_diagnostic_never_calls_mutation_methods():
    tree = ast.parse(PATH.read_text())
    forbidden = {'start', 'release', 'install', 'unlink', 'mkdir', 'replace', 'durable', 'write_text', 'write_bytes'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden


def test_report_success_is_not_authority(capsys):
    assert MODULE.report('check', lambda: None)
    assert json.loads(capsys.readouterr().out) == dict(check='check', passed=True, mutation_authorized=False)
