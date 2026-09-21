"""Owner-cancel of lapsed outbox rows on a Lantern appliance.

Root on the unit. Reads one JSON line on stdin:

    {"pin": "...", "reason": "why the owner is ending these", "apply": false}
    {"pin": "...", "reason": "...", "apply": true, "binding": "owner-cancel-..."}

WHY THIS EXISTS (owner decision 2026-09-21). A row whose approval window lapses
while nobody can act on it has no way out at all: ``mesh_daemon`` refuses to
construct a provider while a ``dispatching`` row is out of window, and the
runtime's own retry driver only works inside the window. Unit A has three such
rows and answers every community route with 503/community_unavailable. This tool
performs the one transition the owner is allowed to make - ``state:
"cancelled"`` through ``MeshOutbox.cancel`` - and prints the evidence.

WHAT IT REFUSES. A row still inside its window (the runtime may still dispatch
it), a row that already carries peer evidence (``delivered``/``read`` is finished
evidence and not the owner's to discard), and a row that is already cancelled.
Nothing is deleted: the original approval, ciphertext wire and text stay in the
row, and a genuine peer receipt arriving later still promotes it, because the
peer's signed word outranks a local cancel.

WHAT IT CANNOT DO. Un-send a fragment that already reached the wire. A cancelled
row may therefore have been delivered, and the printed ``next_fragment`` and
``attempts`` are the evidence of how far it got.

Run it twice: once with ``"apply": false`` to list what is eligible (no service
stop, no writes), then with ``"apply": true`` in the window. The service is
stopped for the applying run so the operator is the only writer, and is left
stopped; the reviewed sequence starts it again afterwards.

    printf '<sudo-password>\n{"pin":"...","reason":"...","apply":false}\n' | \
        sudo -S -p '' /opt/sage-lantern/venv/bin/python /root/cancel-outbox-rows.py
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request


CONTROL = 'http://127.0.0.1:3196'
BASE = {'Origin': 'http://127.0.0.1:3100', 'X-Lantern-Auth': 'control',
        'Content-Type': 'application/json'}
SERVICE = 'sage-lantern-mesh-runtime.service'
SCHEMA = 'sage.lantern.outbox-rows-cancellation.v1'
ELIGIBLE = ('queued', 'dispatching', 'awaiting_receipt')
DEFAULT_BINDING = 'owner-cancel-on-unit'


def eligible(body, now):
    """Whether the owner may end this row, and why not when they may not.

    Pure decision function: the caller supplies the verified row body and the
    wall clock, so the rules can be read (and tested) without a unit.
    """
    state = body['state']
    if state in ('delivered', 'read'):
        return False, 'row_carries_peer_evidence'
    if state == 'cancelled':
        return False, 'row_already_cancelled'
    if state not in ELIGIBLE:
        return False, 'row_state_not_cancellable'
    approved = body['approved']
    if approved['issued_at'] <= now < approved['expires_at']:
        return False, 'window_still_open'
    return True, None


def call(path, body=None, timeout=20):
    request = urllib.request.Request(CONTROL + path, headers=BASE,
        data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode('utf-8', 'replace')
    except Exception as error:
        return 0, json.dumps({'transport_error': type(error).__name__})


def owner_session(pin, evidence):
    """Hold a live owner session: the same fence the runtime's own guards use."""
    from sage_lantern.device_access import access_epoch
    status, raw = call('/unlock', {'pin': pin})
    if status != 200:
        raise SystemExit(json.dumps(dict(evidence, error='unlock_failed', status=status)))
    status, raw = call('/activate', {'token': json.loads(raw)['token']})
    evidence['activate'] = status
    if status != 200 or json.loads(raw).get('authorized') is not True:
        raise SystemExit(json.dumps(dict(evidence, error='activate_failed')))
    deadline = time.monotonic() + 30
    while access_epoch() is None and time.monotonic() < deadline:
        time.sleep(0.5)
    guard = lambda: access_epoch() is not None
    if guard() is not True:
        raise SystemExit(json.dumps(dict(evidence, error='no_owner_session')))
    return guard


def cancel_rows(store, outbox, guard, *, now, binding, only, apply):
    """End every lapsed, pre-terminal row the owner is allowed to end."""
    rows = []
    for record in store.scan(limit=50, max_records=4096, access_check=guard):
        if record['kind'] != 'mesh_outbound':
            continue
        identifier = record['record_id']
        if only is not None and identifier != only:
            continue
        try:
            body = outbox._verified(record)
        except Exception:
            continue
        if body.get('schema') != 'sage.lantern.mesh-outbound.v1':
            continue
        allowed, refused = eligible(body, now)
        entry = {'message_id': identifier, 'revision_before': record['revision'],
                 'state_before': body['state'], 'attempts': body['attempts'],
                 'next_fragment': body['next_fragment'],
                 'seconds_since_window_closed': max(0, now - body['approved']['expires_at'])}
        if not allowed:
            entry['refused'] = refused
            rows.append(entry)
            continue
        if not apply:
            entry['outcome'] = 'would_cancel'
            rows.append(entry)
            continue
        try:
            progress = outbox.cancel(identifier, expected_revision=record['revision'],
                                     decision_binding=binding, access_check=guard)
            after = {'state': progress.state, 'revision': progress.revision}
            entry['outcome'] = 'confirmed'
        except Exception:
            # A lost acknowledgement is not a lost write: read the row back and
            # let the durable record say which of the two happened. Nothing is
            # retried here - the same decision, submitted again, is the
            # operator's call.
            try:
                saved = outbox._verified(store.get(identifier, kind='mesh_outbound', access_check=guard))
                after = {'state': saved['state'], 'revision': None}
                entry['outcome'] = ('confirmed_after_reread'
                                    if saved['state'] == 'cancelled' and saved['retry_binding'] == binding
                                    else 'unknown')
            except Exception:
                after = {'state': 'unreadable', 'revision': None}
                entry['outcome'] = 'unknown'
        entry.update(state_after=after['state'], revision_after=after['revision'],
                     binding=binding)
        rows.append(entry)
    return rows


def main():
    if os.geteuid() != 0:
        raise SystemExit('root on the appliance required')
    payload = json.loads(sys.stdin.readline())
    pin = payload['pin']
    apply = payload.get('apply', False)
    binding = payload.get('binding', DEFAULT_BINDING)
    only = payload.get('message_id')
    if type(apply) is not bool or re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', binding) is None:
        raise SystemExit('apply must be a boolean and binding a bounded token')
    evidence = {'schema': SCHEMA, 'apply': apply,
                'reason': str(payload.get('reason', ''))[:200],
                'started': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'rows': []}

    sys.path.insert(0, '/opt/sage-lantern/venv/lib/python3.11/site-packages')
    from sage_lantern.message_contacts import RecipientID
    from sage_lantern.message_storage import MessageStorageProbe
    from sage_lantern.mesh_daemon import load_config, load_identity
    from sage_lantern.mesh_envelope import Peer
    from sage_lantern.mesh_outbox import MeshOutbox
    from sage_lantern.workflow_store import WorkflowStore
    from sage_sdk import SageClient

    guard = owner_session(pin, evidence)
    pin = None
    if apply:
        # The operator is the only writer: the service is stopped and every
        # guard below refuses without a held session.
        evidence['stop'] = subprocess.run(['systemctl', 'stop', SERVICE],
                                          capture_output=True, text=True).returncode
        deadline = time.monotonic() + 30
        while guard() is not True and time.monotonic() < deadline:
            time.sleep(0.5)
        if guard() is not True:
            raise SystemExit(json.dumps(dict(evidence, error='no_owner_session_after_stop')))

    config = load_config()
    identity, descriptor = load_identity(config)
    try:
        with SageClient(config['endpoint'], identity, timeout=5, trust_env=False) as client:
            probe = MessageStorageProbe(client, actor=RecipientID(config['chain_id'], config['agent_id']),
                                        endpoint=config['endpoint'])
            store = WorkflowStore(client, storage=probe)
            outbox = MeshOutbox(store, identity,
                                sender=Peer(config['chain_id'], bytes.fromhex(config['agent_id'])),
                                guard=lambda action: True, verify_contact=lambda action: None)
            evidence['rows'] = cancel_rows(store, outbox, guard, now=int(time.time()),
                                           binding=binding, only=only, apply=apply)
    finally:
        os.close(descriptor)
    evidence['restart_required'] = bool(apply and any(
        row.get('state_after') == 'cancelled' for row in evidence['rows']))
    evidence['finished'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    print(json.dumps(evidence, indent=1, sort_keys=True))


if __name__ == '__main__':
    main()
