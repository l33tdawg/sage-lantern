# SPDX-License-Identifier: Apache-2.0
"""Lantern protected inbox application admission over a Meshticulum transfer."""
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.wire import require
from meshticulum.contracts.identity import ServiceKind

def deliver_private(transfer, inbox, *, operation_id, sender, peer_check):
    """Protected journal admission metadata, never delivery/read authority."""
    require(type(transfer) is ServiceTransfer, 'invalid_private_service_transfer')
    require(transfer.kind is ServiceKind.MESSAGE and transfer.format == 'LME.binary'
            and callable(peer_check),
            'private_service_binding_refused')
    from lantern_meshticulum.sage.mesh_inbox import MeshInbox
    require(type(inbox) is MeshInbox, 'private_service_binding_refused')
    with transfer.authority.application_lease():
        packet = transfer._received()
        result = inbox.ingest_binary(operation_id, packet, sender=sender,
            peer_check=lambda peer: transfer.live() and peer_check(peer) is True)
        transfer._check()
        return result

