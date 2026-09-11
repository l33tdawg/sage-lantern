from __future__ import annotations

import uuid
import threading
import secrets
import time
from functools import wraps

from .workflow_store import WorkflowNotFound, WorkflowStore, WorkflowUnavailable


KIND = "correction_attempt"
SCHEMA = "sage.lantern.correction-attempt.v1"
STATES = {"reserved", "admitting", "accepted", "completed", "failed"}


def serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


def identifier(value):
    if type(value) is not str:
        raise WorkflowUnavailable()
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise WorkflowUnavailable() from None
    if parsed.int == 0 or str(parsed) != value:
        raise WorkflowUnavailable()
    return value


class CorrectionAttempts:
    def __init__(self, workflow):
        if not isinstance(workflow, WorkflowStore):
            raise WorkflowUnavailable()
        self.workflow = workflow
        self.lock = threading.RLock()
        self.challenges = {}
        actor = workflow.storage.actor
        self.actor = dict(chain_id=actor.chain_id, agent_id=actor.agent_id)

    def _key(self, source_id):
        identifier(source_id)
        actor = self.workflow.storage.actor
        if self.actor != dict(chain_id=actor.chain_id, agent_id=actor.agent_id):
            raise WorkflowUnavailable()
        return str(uuid.uuid5(uuid.NAMESPACE_URL, SCHEMA + ":" + actor.chain_id + ":" + actor.agent_id + ":" + source_id))

    def _read(self, source_id, check):
        record = self.workflow.get(self._key(source_id), kind=KIND, access_check=check)
        value = record["payload"]
        if (set(value) not in ({"schema", "actor", "source_id", "source_kind", "attempt_id", "session_id", "state"},
                              {"schema", "actor", "source_id", "source_kind", "attempt_id", "session_id", "state", "previous", "authorization"})
                or value["schema"] != SCHEMA or value["actor"] != self.actor
                or value["source_id"] != source_id or value["source_kind"] not in ("current", "history")
                or type(value["state"]) is not str or value["state"] not in STATES):
            raise WorkflowUnavailable()
        identifier(value["attempt_id"])
        if "previous" in value and value['previous'] is not None:
            identifier(value['previous'])
        if 'authorization' in value:
            authority = value['authorization']
            if (type(authority) is not dict or set(authority) != {'expected_revision', 'acknowledge_unknown', 'new_execution'}
                    or type(authority['expected_revision']) is not int or authority['expected_revision'] < 0
                    or authority['acknowledge_unknown'] is not True or authority['new_execution'] is not True):
                raise WorkflowUnavailable()
        if value["session_id"] is not None:
            identifier(value["session_id"])
        if (value["state"] in {"reserved", "admitting"}) != (value["session_id"] is None):
            raise WorkflowUnavailable()
        return record

    @serialized
    def reconcile(self, source_id, *, access_check, approval_context=None):
        try:
            record = self._read(source_id, access_check)
        except WorkflowNotFound:
            record = None
        if access_check() is not True:
            raise WorkflowUnavailable()
        if record is None:
            result = {"status": "unrecorded", "retry_allowed": False}
        else:
            value = record['payload']
            result = dict(status=value['state'], attempt_id=value['attempt_id'],
                          session_id=value['session_id'], revision=record['revision'], retry_allowed=False)
        if approval_context is not None:
            if type(approval_context) is not str or len(approval_context) != 64:
                raise WorkflowUnavailable()
            now = time.monotonic()
            self.challenges = {key: value for key, value in self.challenges.items() if value['expires'] > now}
            if len(self.challenges) >= 256:
                raise WorkflowUnavailable()
            epoch = self.workflow.storage.snapshot().epoch
            if access_check() is not True:
                raise WorkflowUnavailable()
            challenge = secrets.token_urlsafe(32)
            self.challenges[challenge] = dict(source_id=source_id, revision=0 if record is None else record['revision'],
                context=approval_context, epoch=epoch, expires=now + 120, record=record)
            result.update(challenge=challenge, expected_revision=0 if record is None else record['revision'],
                risk_acknowledgement_required=True, continuity='current_snapshot_only',
                warning='A new execution may repeat work; previous execution is not disproven.',
                expires_in_seconds=120)
        return result

    @serialized
    def submit(self, *, source_id, source_kind, attempt_id, access_check, admit,
               new_execution=None, approval_context=None):
        identifier(attempt_id)
        if source_kind not in ("current", "history") or not callable(admit):
            raise WorkflowUnavailable()
        key = self._key(source_id)
        try:
            current = self._read(source_id, access_check)
        except WorkflowNotFound:
            current = None
        if current is not None and new_execution is None:
            return self.reconcile(source_id, access_check=access_check)
        if current is None and new_execution is None:
            raise WorkflowUnavailable()
        expected = 0 if current is None else current['revision']
        previous = None
        if new_execution is not None:
            if (type(new_execution) is not dict or set(new_execution) !=
                    {'challenge', 'expected_revision', 'consent', 'acknowledge_unknown'}
                    or type(new_execution['challenge']) is not str
                    or type(new_execution['expected_revision']) is not int
                    or new_execution['expected_revision'] != expected
                    or new_execution['consent'] is not True
                    or type(new_execution['acknowledge_unknown']) is not bool):
                raise WorkflowUnavailable()
            grant = self.challenges.pop(new_execution['challenge'], None)
            if (grant is None or grant['expires'] <= time.monotonic() or grant['source_id'] != source_id
                    or grant['revision'] != expected or grant['context'] != approval_context
                    or grant['record'] != current or access_check() is not True
                    or self.workflow.storage.snapshot().epoch != grant['epoch']
                    or new_execution['acknowledge_unknown'] is not True):
                raise WorkflowUnavailable()
        def checked():
            return (access_check() is True and grant['expires'] > time.monotonic()
                    and self.workflow.storage.snapshot().epoch == grant['epoch'])
        if current is not None:
            if attempt_id == current['payload']['attempt_id']:
                raise WorkflowUnavailable()
            archive_key = lambda value: str(uuid.uuid5(uuid.UUID(key), value))
            try:
                self.workflow.get(archive_key(attempt_id), kind=KIND, access_check=checked)
            except WorkflowNotFound:
                pass
            else:
                raise WorkflowUnavailable()
            previous = archive_key(current['payload']['attempt_id'])
            archive = dict(schema='sage.lantern.correction-history.v1', record=current)
            try:
                retained = self.workflow.get(previous, kind=KIND, access_check=checked)
            except WorkflowNotFound:
                self.workflow.put(previous, KIND, 0, archive, access_check=checked)
            else:
                if retained['payload'] != archive:
                    raise WorkflowUnavailable()
        payload = dict(schema=SCHEMA, actor=dict(self.actor), source_id=source_id,
                       source_kind=source_kind, attempt_id=attempt_id, session_id=None, state="reserved")
        if new_execution is not None:
            payload['previous'] = previous
            payload['authorization'] = dict(expected_revision=expected, acknowledge_unknown=True, new_execution=True)
        reserved = self.workflow.put(key, KIND, expected, payload, access_check=checked)
        admitting = self.workflow.put(key, KIND, reserved["revision"],
                                      dict(payload, state="admitting"), access_check=checked)
        if checked() is not True:
            raise WorkflowUnavailable()
        session_id = identifier(admit())
        self.workflow.put(key, KIND, admitting["revision"],
                          dict(payload, state="accepted", session_id=session_id), access_check=checked)
        return self.reconcile(source_id, access_check=access_check)

    @serialized
    def finish(self, *, source_id, attempt_id, session_id, succeeded, access_check):
        identifier(attempt_id)
        identifier(session_id)
        if type(succeeded) is not bool:
            raise WorkflowUnavailable()
        record = self._read(source_id, access_check)
        value = record["payload"]
        if (value["state"] != "accepted" or value["attempt_id"] != attempt_id
                or value["session_id"] != session_id):
            raise WorkflowUnavailable()
        self.workflow.put(self._key(source_id), KIND, record["revision"],
                          dict(value, state="completed" if succeeded else "failed"), access_check=access_check)
        return self.reconcile(source_id, access_check=access_check)
