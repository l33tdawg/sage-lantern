"""The masked cause of a dispatch failure must reach the journal.

Unit A journalled `MeshOutboxError ... mesh_outbox.py:91:call` at 17:34:34 on
2026-09-13 - the innermost frame was the mask itself, so the entry could not tell
a refused admission from a stalled contact read. These tests pin the recovery of
the chain, not the masking policy: the error a caller sees is unchanged.
"""

import json
import syslog

from sage_lantern.mesh_outbox import MeshOutboxError
from sage_lantern.mesh_runtime import MeshRuntimeError, _log_operation_failure


class MeshCoreError(RuntimeError):
    """Stands in for the transport's coded error; only the code is reported."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def capture(monkeypatch):
    logged = []
    monkeypatch.setattr(syslog, 'syslog',
                        lambda priority, message: logged.append((priority, json.loads(message))))
    return logged


def test_masked_chain_names_every_layer_and_the_origin(monkeypatch):
    logged = capture(monkeypatch)
    try:
        try:
            try:
                raise MeshCoreError('serial_incomplete')
            except Exception:
                raise MeshOutboxError() from None      # mesh_outbox.dispatch_one
        except Exception:
            raise MeshOutboxError() from None          # mesh_outbox._admitted
    except Exception:
        try:
            raise MeshRuntimeError() from None         # MeshRuntime._run
        except MeshRuntimeError as error:
            _log_operation_failure(error, 'background_dispatch',
                                   provably_pre_wire=False, wire_frames=1)

    assert len(logged) == 1
    priority, payload = logged[0]
    assert priority == syslog.LOG_ERR
    assert payload['event'] == 'mesh_operation_failed'
    assert payload['exception'] == 'MeshRuntimeError'
    assert payload['operation'] == 'background_dispatch'
    assert payload['pre_wire'] is False and payload['wire_frames'] == 1
    assert [cause['exception'] for cause in payload['causes']] == [
        'MeshOutboxError', 'MeshOutboxError', 'MeshCoreError']
    assert payload['causes'][-1]['code'] == 'serial_incomplete'
    assert payload['causes'][-1]['frames'][-1]['function'] == \
        'test_masked_chain_names_every_layer_and_the_origin'
    assert payload['causes'][-1]['frames'][-1]['file'] == 'test_mesh_runtime_diagnostics.py'


def test_an_unmasked_failure_reports_no_causes(monkeypatch):
    logged = capture(monkeypatch)
    try:
        raise ValueError('private diagnostic')
    except ValueError as error:
        _log_operation_failure(error, 'receive', provably_pre_wire=True, wire_frames=0)

    priority, payload = logged[0]
    assert priority == syslog.LOG_ERR
    assert 'causes' not in payload
    assert set(payload) == {'event', 'exception', 'operation', 'pre_wire', 'wire_frames', 'frames'}
    assert 'private diagnostic' not in json.dumps(payload)


def test_a_non_string_code_is_not_reported(monkeypatch):
    logged = capture(monkeypatch)
    try:
        raise MeshCoreError(('serial_incomplete',))
    except MeshCoreError as error:
        _log_operation_failure(error, 'receive')

    assert 'code' not in logged[0][1]


def test_a_cause_cycle_is_bounded(monkeypatch):
    logged = capture(monkeypatch)
    first, second = ValueError('first'), ValueError('second')
    first.__context__ = second
    second.__context__ = first
    _log_operation_failure(first, 'receive')

    assert [cause['exception'] for cause in logged[0][1]['causes']] == ['ValueError']


def test_a_base_exception_is_never_reported_as_a_cause(monkeypatch):
    logged = capture(monkeypatch)
    error = ValueError('masked')
    error.__context__ = KeyboardInterrupt()
    _log_operation_failure(error, 'receive')

    assert 'causes' not in logged[0][1]
