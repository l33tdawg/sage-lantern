from dataclasses import asdict
from types import SimpleNamespace
import uuid

import pytest

from sage_lantern.community_public import PublicReceipt
from sage_lantern.community_public_mesh import CommunityPublicMeshFacade
from sage_lantern.mesh_voice_ipc import MeshVoiceFacade
from sage_lantern.message_contacts import RecipientID
from sage_lantern.public_mesh_protocol import result_data
from test_public_mesh_protocol import proposal


@pytest.fixture
def refresh(monkeypatch):
    item = proposal()
    state = SimpleNamespace(item=item, active=True, revoke=False, calls=[], submitted=[],
        original=str(uuid.uuid4()), following=str(uuid.uuid4()), phase="consumed")
    receipt = PublicReceipt(item.operation_id, item.content_hash, item.chain_id,
        str(uuid.uuid4()), "a" * 64, 1)
    state.result = result_data("receipt", receipt=asdict(receipt))
    transport = MeshVoiceFacade(connector=lambda: pytest.fail("unexpected socket open"), epoch_reader=lambda: "b" * 32)
    transport._actor = RecipientID("private", "c" * 64)

    def call(method, arguments, access_check, on_submit=None):
        if access_check() is not True:
            raise RuntimeError("revoked")
        state.calls.append((method, arguments))
        if method == "public_inspect":
            if state.revoke:
                state.active = False
            return dict(progress=dict(request_id=state.original, state=state.phase,
                next_fragment=1, fragment_count=1), result=state.result)
        assert method == "public_begin" and arguments["receipt"] is not None
        on_submit()
        return dict(request_id=state.following, state="queued", next_fragment=0, fragment_count=1)

    monkeypatch.setattr(transport, "_call", call)
    state.facade = CommunityPublicMeshFacade(transport)
    state.run = lambda: state.facade.refresh(state.item, submission_request_id=state.original,
        request_id=state.following, access_check=lambda: state.active,
        on_submit=lambda: state.submitted.append(True))
    return state


def test_refresh_uses_exact_verified_receipt_for_poll(refresh):
    assert refresh.run().state == "queued"
    assert [method for method, _ in refresh.calls] == ["public_inspect", "public_begin"]
    assert refresh.calls[-1][1]["receipt"] == refresh.result["receipt"]
    assert refresh.submitted == [True]


@pytest.mark.parametrize("fault", ["queued", "unknown", "missing", "unavailable", "operation", "hash", "chain",
                                  "same_request", "revoked", "forged_acceptance"])
def test_refresh_never_resubmits_unknown_or_unbound_proposal(refresh, fault):
    if fault in {"queued", "unknown"}:
        refresh.phase = fault
    elif fault == "missing":
        refresh.result = None
    elif fault == "unavailable":
        refresh.result = result_data("unavailable")
    elif fault == "operation":
        refresh.result["receipt"]["operation_id"] = str(uuid.uuid4())
    elif fault == "hash":
        refresh.result["receipt"]["content_hash"] = "d" * 64
    elif fault == "chain":
        refresh.result["receipt"]["intended_chain_id"] = "other"
    elif fault == "same_request":
        refresh.following = refresh.original
    elif fault == "revoked":
        refresh.revoke = True
    else:
        refresh.result["acceptance_available"] = True
    with pytest.raises((ValueError, RuntimeError)):
        refresh.run()
    assert all(method != "public_begin" for method, _ in refresh.calls)
    assert not refresh.submitted
