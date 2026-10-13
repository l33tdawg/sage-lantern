# SPDX-License-Identifier: Apache-2.0
"""One fresh storage status for two exactly bound views of the same SDK client.

This holds original bindings and refusal state, never a status, epoch result,
permission or signed-record observation. Native supervisors and callers' SDK
before/after epoch brackets remain independent. This is not bearer authority.
"""
import os
import threading
from types import MethodType

from sage_sdk.client import SageClient
from .message_contacts import RecipientID
from .message_storage import MessageStorageProbe, MessageStorageSnapshot
from .workflow_store import WorkflowStore


class BoundStoragePairError(RuntimeError):
    def __init__(self): super().__init__('bound_storage_pair_unavailable')


def require(value):
    if not value: raise BoundStoragePairError()


class BoundStoragePair:
    """Immutable native/integration bindings; each observe performs fresh IO."""
    __slots__ = ('__matches', '__observe')

    def __setattr__(self, name, value): raise BoundStoragePairError()
    def __delattr__(self, name): raise BoundStoragePairError()
    def __repr__(self): return '<BoundStoragePair>'

    def __init__(self, native_probe, store):
        from sage_lantern.message_storage import MessageStorageProbe as NativeProbe
        from sage_lantern.message_contacts import RecipientID as NativeRecipient
        require(type(native_probe) is NativeProbe and type(store) is WorkflowStore
                and type(store.storage) is MessageStorageProbe)
        probe = store.storage; client = store.client
        require(type(client) is SageClient and native_probe.client is probe.client is client
                and type(native_probe.actor) is NativeRecipient and type(probe.actor) is RecipientID)
        owner = os.getpid(), threading.get_ident()
        transport, identity = client._client, client._identity
        inner_transport, signer = transport._transport, identity._signing_key
        native_actor, actor = native_probe.actor, probe.actor
        facts = actor.chain_id, actor.agent_id, probe.endpoint
        require(facts == (native_actor.chain_id, native_actor.agent_id, native_probe.endpoint)
                and type(facts[0]) is str and type(facts[1]) is str and type(facts[2]) is str
                and store._actor is actor and store._endpoint == facts[2])
        targets = ((native_probe, '_binding'), (native_probe, 'snapshot'),
                   (probe, '_binding'), (probe, 'snapshot'), (client, 'message_storage_status'))
        methods = []
        for instance, name in targets:
            method = getattr(instance, name)
            require(type(method) is MethodType and method.__self__ is instance
                    and name not in instance.__dict__)
            methods.append(method)
        methods = tuple(methods)
        pair_methods = type(self).matches, type(self).observe
        snapshot_type = MessageStorageSnapshot
        snapshot_methods = snapshot_type.__init__, snapshot_type.epoch
        observing = False; fenced = False

        def pins():
            require(not fenced and owner == (os.getpid(), threading.get_ident())
                    and type(self) is BoundStoragePair
                    and (type(self).matches, type(self).observe) == pair_methods
                    and getattr(self, '_BoundStoragePair__matches', None) is matches
                    and getattr(self, '_BoundStoragePair__observe', None) is observe
                    and type(native_probe) is NativeProbe and type(store) is WorkflowStore
                    and type(probe) is MessageStorageProbe and type(client) is SageClient
                    and store.storage is probe and store.client is native_probe.client is probe.client is client
                    and native_probe.actor is native_actor and probe.actor is actor
                    and type(native_actor) is NativeRecipient and type(actor) is RecipientID
                    and all(type(value) is str for value in (actor.chain_id, actor.agent_id, probe.endpoint,
                        native_actor.chain_id, native_actor.agent_id, native_probe.endpoint, store._endpoint))
                    and (actor.chain_id, actor.agent_id, probe.endpoint) == facts
                    and (native_actor.chain_id, native_actor.agent_id, native_probe.endpoint) == facts
                    and store._actor is actor and store._endpoint == facts[2]
                    and client._client is native_probe._transport is probe._transport is transport
                    and transport._transport is inner_transport
                    and client._identity is native_probe._identity is probe._identity is identity
                    and identity._signing_key is signer
                    and MessageStorageSnapshot is snapshot_type
                    and (snapshot_type.__init__, snapshot_type.epoch) == snapshot_methods)
            for (instance, name), original in zip(targets, methods):
                current = getattr(instance, name)
                require(type(current) is MethodType and current.__self__ is instance
                        and current.__func__ is original.__func__ and name not in instance.__dict__)
        def check():
            pins(); methods[0](); pins(); methods[2](); pins()

        def matches(current_native_probe, current_store):
            nonlocal fenced
            try:
                # Availability compares structural identity only. Live storage
                # observations retain both probes' binding callbacks around IO.
                pins()
                require(current_native_probe is native_probe and current_store is store)
                return True
            except BaseException as error:
                fenced = True
                if not isinstance(error, Exception): raise
                raise BoundStoragePairError() from None

        def observe(expected_epoch):
            nonlocal observing, fenced
            if observing:
                fenced = True; raise BoundStoragePairError()
            observing = True
            try:
                check()
                require(type(expected_epoch) is str and 0 < len(expected_epoch) <= 128)
                snapshot = methods[3]()
                check()
                require(type(snapshot) is snapshot_type and snapshot.epoch == expected_epoch)
                return snapshot.epoch
            except BaseException as error:
                fenced = True
                if not isinstance(error, Exception): raise
                raise BoundStoragePairError() from None
            finally: observing = False

        object.__setattr__(self, '_BoundStoragePair__matches', matches)
        object.__setattr__(self, '_BoundStoragePair__observe', observe)
        check()

    def matches(self, native_probe, store):
        return self.__matches(native_probe, store)

    def observe(self, expected_epoch):
        """Return this fresh call's epoch, compared with its admitted original."""
        return self.__observe(expected_epoch)
