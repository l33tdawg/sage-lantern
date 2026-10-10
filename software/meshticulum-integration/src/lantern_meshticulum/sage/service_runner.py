# SPDX-License-Identifier: Apache-2.0
# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Finite service loop over a caller-owned authenticated flow/exclusive worker.

No device, socket, grant, identity or private backend is opened here. The native
dispatcher must supply its actual private lifecycle policy; default private
dispatch is refused. Linux hardware setup/disarm and current device-window
admission stay with the owner. Application settlement is separate from flow/RF
completion, and close never refunds airtime or closes borrowed worker/storage.
"""
from contextlib import AbstractContextManager, contextmanager

from meshticulum.contracts.identity import ServiceKind
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.service_runner import _ServiceLoop
from meshticulum.bench.wire import require


class _Held(AbstractContextManager):
    def __init__(self, lease):
        require(isinstance(lease, AbstractContextManager), 'private_policy_lease_refused')
        self.lease = lease

    def __enter__(self):
        return self.lease.__enter__()

    def __exit__(self, kind, value, traceback):
        outcome = self.lease.__exit__(kind, value, traceback)
        require(outcome is None or outcome is False, 'private_policy_lease_exit_refused')
        return False


class PrivatePolicy:
    """Trusted native policy adapter, never a permission object by construction.

    lease() must hold actual owner/consent/lock/sleep/revocation authority, and
    check() must validate that exact grant binding against current native state.
    A Python type or a callback returning True proves neither fact; production
    provider qualification is independent. No caller-controlled enable flag or
    substitute policy is created when this provider is absent or unavailable.
    """
    def __init__(self, *, grant_digest, lease, check):
        require(type(grant_digest) is bytes and len(grant_digest) == 32
                and callable(lease) and callable(check), 'invalid_private_policy_adapter')
        self.grant_digest, self._lease, self._check = grant_digest, lease, check

    @contextmanager
    def held(self, transfer):
        require(type(transfer) is ServiceTransfer and transfer.kind is ServiceKind.MESSAGE
                and transfer.grant_digest == self.grant_digest, 'private_policy_binding_refused')
        with _Held(self._lease()):
            require(self._check(self.grant_digest) is True, 'private_policy_not_current')
            yield
            require(self._check(self.grant_digest) is True, 'private_policy_not_current')


class PrivateCaptureSink:
    def __init__(self, capture, inbox, *, operation_id, sender):
        from lantern_meshticulum.sage.mesh_binary_capture import BinaryCapture
        from meshticulum.privacy.mesh_envelope import Peer
        from lantern_meshticulum.sage.mesh_inbox import MeshInbox, _uuid
        _uuid(operation_id)
        require(type(capture) is BinaryCapture and type(inbox) is MeshInbox and type(sender) is Peer
                and inbox._store is capture.store and inbox._recipient == capture.recipient,
                'invalid_private_capture_sink')
        self.capture, self.inbox, self.operation_id, self.sender = capture, inbox, operation_id, sender
        self.prepared, self.captured = False, False

    def prepare(self, transfer, policy):
        require(not self.prepared and type(transfer) is ServiceTransfer and not transfer.flow.sends
                and transfer.kind is ServiceKind.MESSAGE and type(policy) is PrivatePolicy
                and policy._lease is self.capture._lease,
                'private_capture_direction_refused')
        authority = transfer.authority
        with authority.application_lease():
            transfer._check()
            self.capture.prepare(self.operation_id, sender=self.sender,
                packet_sha256=transfer.descriptor[1], packet_bytes=transfer.descriptor[0],
                application_expires_epoch_us=authority.application_expires_epoch_us,
                application_deadline_monotonic_us=int(authority.deadlines.application_deadline_monotonic * 1000000),
                host_boot_id=authority.plan['host_boot_id'], authority_digest=transfer.grant_digest.hex(),
                permission_check=lambda: policy._check(transfer.grant_digest) is True)
            transfer._check()
        self.prepared = True

    def complete(self, transfer, policy):
        require(self.prepared and not self.captured, 'private_capture_not_prepared')
        with transfer.authority.application_lease():
            packet = transfer._received()
            self.capture.complete(self.operation_id, packet, authority_digest=transfer.grant_digest.hex(),
                permission_check=lambda: policy._check(transfer.grant_digest) is True)
            transfer._check()
        self.captured = True

    def reconcile(self, *, inbox=None):
        """Explicit original-operation recovery, including after wire expiry.

        Callers replace a fenced inbox/capture owner explicitly; this method
        does not restart one, mint another UUID or renew transport permission.
        """
        return self.capture.process_pending(inbox or self.inbox, operation_id=self.operation_id)

    def acknowledge(self):
        return self.capture.acknowledge(self.operation_id)


class ServiceRunner(_ServiceLoop):
    def __init__(self, transfer, *, payload=None, private_policy=None, private_sink=None):
        require(type(transfer) is ServiceTransfer, 'invalid_service_runner')
        private = transfer.kind is ServiceKind.MESSAGE
        if private:
            require(type(private_policy) is PrivatePolicy
                    and private_policy.grant_digest == transfer.grant_digest,
                    'private_runner_requires_native_policy')
            if not transfer.flow.sends:
                require(type(private_sink) is PrivateCaptureSink
                        and private_sink.capture._lease is private_policy._lease,
                        'private_runner_requires_capture')
        else:
            require(private_policy is None and private_sink is None, 'public_runner_private_binding_refused')
        require((transfer.flow.sends and type(payload) is bytes and private_sink is None)
                or (not transfer.flow.sends and payload is None), 'invalid_service_runner_direction')
        super().__init__(transfer, payload=payload)
        self.private_policy, self.private_sink = private_policy, private_sink

    def _prepare_receive(self):
        if self.private_sink is not None:
            # Capture acquires the native lifecycle lease itself; never nest it.
            self.private_sink.prepare(self.transfer, self.private_policy)

    def _send(self):
        if self.private_policy is not None:
            with self.private_policy.held(self.transfer):
                super()._send()
        else:
            super()._send()

    def _dispatch(self):
        if self.private_policy is not None:
            with self.private_policy.held(self.transfer):
                return super()._dispatch()
        return super()._dispatch()

    def _receive(self):
        if self.private_sink is not None:
            self.private_sink.complete(self.transfer, self.private_policy)
        else:
            super()._receive()

    def _close_application(self):
        return self.private_sink.capture.close(0) if self.private_sink is not None else True

    def settle_private(self):
        require(self.state == 'wire_complete' and self.private_sink is not None,
                'private_runner_not_captured')
        try:
            result = self.private_sink.reconcile()
            require(result is not None, 'private_capture_settlement_missing')
            self.application_result = result
            self.state = 'application_settled'
            return result
        except BaseException:
            self.state = 'failed'
            self.transfer.cancel()
            raise

