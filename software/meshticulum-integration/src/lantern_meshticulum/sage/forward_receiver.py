# SPDX-License-Identifier: Apache-2.0
"""Canonical protected receiver composition over an already owned SDK client.

This opens no client, radio, identity, listener or grant and supplies no native
permission. The trusted owner provides current observations inside and after
its shared lease plus independently enrolled dedicated Noise-key purpose checks.
Runtime sage_lantern storage/inbox classes are not substituted for canonical
integration types. Original policy/format/flow admission remains caller owned.
"""
import threading
import os

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from nacl.signing import SigningKey
from meshticulum.privacy.forward_session import ForwardContext, ForwardSession, public
from lantern_meshticulum.sage.message_contacts import RecipientID
from lantern_meshticulum.sage.message_storage import MessageStorageProbe
from lantern_meshticulum.sage.workflow_store import WorkflowStore
from lantern_meshticulum.sage.forward_receive_journal import ForwardReceiveJournal, ReceiveIntention
from lantern_meshticulum.sage.mesh_binary_capture import BinaryCapture
from lantern_meshticulum.sage.mesh_inbox import MeshInbox


class ForwardReceiverError(RuntimeError):
    def __init__(self):
        super().__init__('forward_receiver_unavailable')


def _require(value):
    if not value:
        raise ForwardReceiverError()


class ForwardReceiver:
    """One original receiver, borrowed storage/client and caller-owned lifecycle.

    All callbacks are mandatory trusted-owner inputs, not native attestation by
    construction. dispatch_check binds original context/intention; key_check
    independently verifies the pinned local static's dedicated Noise purpose.
    read_check grants current protected reads/import, never fresh dispatch.
    """
    def __init__(self, client, *, context, local_initiator, intention, boot_id,
                 endpoint, signing_key, owner_lease, access_check, storage_check,
                 peer_check, dispatch_check, read_check, key_check, clock, workflow_access_check=None):
        _require(type(context) is ForwardContext and type(local_initiator) is bool
                 and type(intention) is ReceiveIntention and isinstance(signing_key, SigningKey)
                 and all(callable(c) for c in (owner_lease,access_check,storage_check,
                     peer_check,dispatch_check,read_check,key_check,clock))
                 and (workflow_access_check is None or callable(workflow_access_check)))
        local=context.initiator if local_initiator else context.responder
        remote=context.responder if local_initiator else context.initiator
        _require(bytes(signing_key.verify_key)==local.public_key
                 and client._identity.agent_id==local.public_key.hex()
                 and client._identity._signing_key is signing_key)
        self.client,self.context,self.intention,self.boot_id=client,context,intention,boot_id
        self.local,self.remote,self.local_initiator=local,remote,local_initiator
        self._lease,self._access,self._storage,self._peer=owner_lease,access_check,storage_check,peer_check
        self._dispatch,self._read,self._purpose,self._clock=dispatch_check,read_check,key_check,clock
        self._workflow_access=self._workflow_access_ref=workflow_access_check
        self._key=signing_key
        self._closed,self._uncertain,self._session_created=False,False,False
        self.session=None
        self._busy=False
        self._constructing=False
        self._owner=(os.getpid(),threading.get_ident())
        # The same owned client is retained: no parallel transport or backend.
        self.probe=MessageStorageProbe(client,actor=RecipientID(local.chain_id,local.public_key.hex()),endpoint=endpoint)
        self.store=WorkflowStore(client,storage=self.probe)
        self.journal=self._new_journal()
        self.capture=BinaryCapture(self.store,recipient=local,recipient_signing_key=signing_key,
            owner_lease=owner_lease,access_check=self._access_allowed,storage_check=self._storage_allowed,
            peer_check=self._peer_allowed,boot_id=boot_id,clock=lambda:self._time()[0]/1000000,
            monotonic_clock=lambda:self._time()[1]/1000000)
        self.inbox=MeshInbox(self.store,recipient=local,recipient_signing_key=signing_key,
            resolve_peer=lambda message:remote,access_check=self._access_allowed,
            storage_check=self._storage_allowed,clock=lambda:self._time()[0]/1000000)

    def __repr__(self):return '<ForwardReceiver>'

    def _available(self):
        _require(not self._closed and not self._uncertain and (os.getpid(),threading.get_ident())==self._owner
                 and self._workflow_access is self._workflow_access_ref)
        # Handshake operations may not touch storage; still verify that the
        # borrowed identity/endpoint/transport has not been structurally replaced.
        self.probe._binding()
        _require(self.client._identity._signing_key is self._key
                 and self.client._client.is_closed is False)

    def _checked(self,callback,*args):
        self._available();value=callback(*args);self._available()
        _require(value is True)
        return True

    def _access_allowed(self):
        self._checked(self._access)
        # Every protected dispatch/admission/clear requires current read rights,
        # not only import_pending's explicit historical permission callback.
        return self._checked(self._read,self.context,self.intention)
    def _storage_allowed(self):return self._checked(self._storage)
    def _peer_allowed(self,peer):
        _require(peer==self.remote)
        return self._checked(self._peer,peer)
    def _read_allowed(self,intention):
        _require(intention==self.intention)
        return self._checked(self._read,self.context,self.intention)
    def _dispatch_allowed(self,intention):
        _require(intention==self.intention)
        return self._checked(self._dispatch,self.context,self.intention)

    def _time(self):
        self._available();value=self._clock();self._available()
        _require(type(value) is tuple and len(value)==2 and all(type(v) is int and v>=0 for v in value))
        return value

    def _new_journal(self):
        self._available()
        return ForwardReceiveJournal(self.store,context=self.context,local_initiator=self.local_initiator,
            local_signing_key=self._key,intention=self.intention,boot_id=self.boot_id,
            owner_lease=self._lease,access_check=self._access_allowed,storage_check=self._storage_allowed,
            peer_check=self._peer_allowed,permission_check=self._dispatch_allowed,clock=self._time,
            workflow_access_check=self._workflow_access)

    def _authorize_crypto(self):
        if self._busy and not self._constructing:
            self._uncertain=True
            raise ForwardReceiverError()
        self._access_allowed();self._storage_allowed();self._peer_allowed(self.remote)
        self._dispatch_allowed(self.intention)
        expected=(self.context.initiator_noise_public_key if self.local_initiator
                  else self.context.responder_noise_public_key)
        self._checked(self._purpose,expected,self.context.digest)
        return True

    def _run(self,callback):
        entered=False
        try:
            self._available()
            if self._busy or self.session is not None and self.session._busy:
                self._uncertain=True
                raise ForwardReceiverError()
            self._busy=entered=True
            result=callback()
            self._available()
            return result
        except BaseException as error:
            if entered:self._uncertain=True
            if self.session is not None:self.session.close()
            if not isinstance(error,Exception):raise
            raise ForwardReceiverError() from None
        finally:
            if entered:self._busy=False

    def create_session(self,noise_static_key):
        """One fresh commit-mode session; mismatch/refusal occurs before claim.

        A reconstructed composition may reconcile retained source/capture, but
        cannot create another crypto incarnation under the same retired claim.
        No Noise key generation/conversion/loading or ephemeral persistence.
        """
        def create():
            _require(not self._session_created and isinstance(noise_static_key,X25519PrivateKey))
            expected=(self.context.initiator_noise_public_key if self.local_initiator
                      else self.context.responder_noise_public_key)
            _require(public(noise_static_key)==expected)
            self._constructing=True
            try:
                self._authorize_crypto()
                self._session_created=True
                self.session=ForwardSession(context=self.context,initiator=self.local_initiator,
                    signing_key=self._key,noise_static_key=noise_static_key,counters=self.journal,
                    authorize=self._authorize_crypto,clock=self._time,
                    application_deadline_monotonic_us=self.intention.application_deadline_monotonic_us,
                    receive_mode='commit')
            finally:
                self._constructing=False
            return self.session
        return self._run(create)

    def import_pending(self):
        """Current-read-only projection of the original signed journal capsule."""
        return self._run(lambda:self.capture.import_forward(self._new_journal(),
            read_permission_check=self._read_allowed))

    def settle(self):
        """Independent inner authentication/expiry/replay/consent admission."""
        return self._run(lambda:self.capture.process_pending(self.inbox,
            operation_id=self.intention.operation_id))

    def acknowledge(self):
        return self._run(lambda:self.capture.acknowledge(self.intention.operation_id))

    def close(self):
        """Fence this composition, never close borrowed client/store or clear debt."""
        self._closed=True
        if os.getpid()!=self._owner[0]:
            if self.session is not None:self.session.close()
            self.session=None
            return False  # A fork/copy cannot join the original process owner.
        if self.session is not None:self.session.close()
        journal_joined=self.journal.close()
        capture_joined=self.capture.close(0)
        crypto_joined=self.session is None or not self.session._busy
        return (journal_joined and capture_joined and crypto_joined
                and not self._busy and not self._uncertain)
