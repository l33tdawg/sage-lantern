"""Consent-bound delivery assertions after durable inbox admission, never reads.

Runs inside the existing serialized receive grant before captured-slot clearing.
Admission time fixes receipt timestamps across duplicate processing. Inbox and
capture evidence survives a failed or uncertain receipt write; no retry, new ID,
dispatch or historical backfill is performed here.
"""

from .mesh_inbox import IngestResult
from .message_contacts import RecipientID


def require(value):
    if value is not True:
        raise ValueError("mesh_delivery_receipt_unavailable")


class MeshDeliveryReceipts:
    def __init__(self, runtime, authority):
        self.runtime, self.authority = runtime, authority

    def after_admission(self, result):
        require(type(result) is IngestResult)
        if result.status not in ("admitted", "duplicate") or result.envelope_kind != "message":
            return
        runtime = self.runtime
        require(runtime._access())
        context = runtime._local.context
        binding = self.authority.delivery_binding(context, "queue")
        if binding is None:
            require(runtime._access())
            return
        original = runtime.inbox.historical_read(result.record_id)
        require(original.kind == "message" and original.message_id == result.remote_id
                and original.recipient == runtime.receipts.sender)
        row = runtime.store.get(result.record_id, kind="mesh_inbound", access_check=runtime._access)
        require(runtime.inbox._historical(result.record_id, row) == original)
        issued = row["payload"]["attestation"]["body"]["admitted_at"]
        peer = RecipientID(original.sender.chain_id, original.sender.public_key.hex())
        cards = [card for card in runtime.registry.list_current(access_check=runtime._access) if card.recipient == peer]
        require(len(cards) == 1)
        radio = bytes.fromhex(cards[0].radio_public_key)
        require(runtime.registry.resolve(radio, access_check=runtime._access) == cards[0])
        require(runtime._access() and self.authority.delivery_binding(context, "queue") == binding)
        progress = runtime.receipts.queue(local_id=result.record_id, kind="delivery", radio_public_key=radio,
            issued_at=issued, expires_at=issued+3600, access_check=runtime._access)
        require(runtime._access() and self.authority.delivery_binding(context, "queue") == binding
                and progress.receipt_id == runtime.receipts._slot(result.record_id, "delivery")
                and progress.state in ("queued", "serial_accepted"))
