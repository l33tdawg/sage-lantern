"""Session-bound owner HTTP forwarding over the fixed root socket; no retries."""

import re
import threading
import time

from .mesh_contact_issuance import require
from .mesh_contacts import MAX_TTL_SECONDS
from .mesh_voice_ipc import connect, receive, send


ROUTES = {
    '/contacts/preview': ('owner-contact-preview', {'ttl_seconds', 'findable', 'messaging_allowed'}),
    '/contacts/confirm': ('owner-contact-confirm', {'challenge', 'plan_sha256', 'consent'}),
    '/contacts/cancel': ('owner-contact-cancel', set()),
    '/contacts/state': ('owner-contact-state', set()),
    '/contacts/reconcile/preview': ('owner-contact-reconcile-preview', {'action'}),
    '/contacts/reconcile/confirm': ('owner-contact-reconcile-confirm', {'challenge', 'plan_sha256', 'consent'}),
    '/contacts/reconcile/cancel': ('owner-contact-reconcile-cancel', set()),
}
OP = 'owner-contact-route'


def validate_payload(path, payload):
    require(path in ROUTES and type(payload) is dict and set(payload) == ROUTES[path][1] | {'token'})
    require(type(payload['token']) is str and re.fullmatch(r'[A-Za-z0-9_-]{43}', payload['token']) is not None)
    if path.endswith('/confirm'):
        require(payload['consent'] is True)
        for field in ('challenge', 'plan_sha256'):
            require(type(payload[field]) is str and re.fullmatch(r'[0-9a-f]{64}', payload[field]) is not None)
    if path == '/contacts/preview':
        require(type(payload['ttl_seconds']) is int and 1 <= payload['ttl_seconds'] <= MAX_TTL_SECONDS
            and type(payload['findable']) is bool and type(payload['messaging_allowed']) is bool)
    if path == '/contacts/reconcile/preview':
        require(payload['action'] in ('abandon', 'export', 'acknowledge'))


class OwnerContactForwarder:
    def __init__(self, broker, *, connector=connect):
        self.broker, self.connector = broker, connector
        self.mutex = threading.Lock()

    def handle(self, path, payload):
        validate_payload(path, payload)
        require(self.mutex.acquire(blocking=False))
        connection = None
        try:
            token = payload['token']
            self.broker.require_token(token)
            epoch = self.broker.access_epoch()
            require(epoch['locked'] is False)
            connection = self.connector()
            connection.settimeout(5)
            request = dict(op=ROUTES[path][0], **{key: value for key, value in payload.items() if key != 'token'})
            send(connection, dict(op=OP, epoch=epoch['epoch'], token=token, request=request))
            deadline = time.monotonic() + 45
            for _ in range(256):
                self.broker.require_token(token)
                require(time.monotonic() < deadline and self.broker.access_epoch() == epoch)
                response = receive(connection, 16384)
                if set(response) == {'guard'}:
                    require(type(response['guard']) is str and re.fullmatch(r'[0-9a-f]{32}', response['guard']) is not None)
                    self.broker.require_token(token)
                    require(self.broker.access_epoch() == epoch)
                    send(connection, dict(guard=response['guard'], allowed=True))
                    continue
                require(set(response) == {'result'} and type(response['result']) is dict)
                self.broker.require_token(token)
                require(self.broker.access_epoch() == epoch and time.monotonic() < deadline)
                return response['result']
            require(False)
        finally:
            if connection is not None:
                connection.close()
            self.mutex.release()
