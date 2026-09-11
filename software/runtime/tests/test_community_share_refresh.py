from dataclasses import asdict
import json
import uuid

import pytest

from sage_lantern.community_service import CommunityError, CommunityService
from sage_lantern.public_mesh_protocol import result_data
from test_public_refresh import refresh


@pytest.fixture
def service(refresh, monkeypatch):
    original_call = refresh.facade.transport._call
    refresh.identifier = "mesh-public:" + refresh.original
    refresh.phase_poll = "queued"
    refresh.used = set()
    refresh.fail_after_submit = False
    refresh.listed = True
    refresh.created = "2026-09-08T01:00:00Z"

    def call(method, arguments, access_check, on_submit=None):
        assert access_check() is True
        if method == "public_list":
            return dict(items=[] if not refresh.listed else [dict(id=refresh.identifier, state="pending",
                created_at=refresh.created, question=json.loads(refresh.item.content)["question"],
                proposal=asdict(refresh.item))], more=False)
        if method == "public_inspect" and arguments["request_id"] == refresh.following:
            return dict(progress=dict(request_id=refresh.following, state=refresh.phase_poll,
                next_fragment=0 if refresh.phase_poll == "queued" else 1, fragment_count=1),
                result=None if refresh.phase_poll == "queued" else result_data("observed",
                    receipt=refresh.result["receipt"], observed_status="committed"))
        if method == "public_begin":
            if arguments["request_id"] in refresh.used:
                raise RuntimeError("duplicate")
            refresh.used.add(arguments["request_id"])
        result = original_call(method, arguments, access_check, on_submit)
        if method == "public_begin" and refresh.fail_after_submit:
            raise RuntimeError("lost response")
        return result

    monkeypatch.setattr(refresh.facade.transport, "_call", call)
    refresh.service = CommunityService(mesh_provider=refresh.facade.transport)
    refresh.body = dict(id=refresh.identifier, request_id=refresh.following, consent=True)
    refresh.call = lambda action, body: refresh.service.dispatch("/community/shares/" + action, body,
        lambda: refresh.active, session_id="new-owner-session")
    return refresh


def test_explicit_refresh_and_inspect_never_promote_gateway_or_post(service):
    assert service.call("refresh", service.body)["state"] == "queued"
    inspect = {key: value for key, value in service.body.items() if key != "consent"}
    assert service.call("inspect", inspect)["state"] == "queued"
    service.service.lock()
    service.service = CommunityService(mesh_provider=service.facade.transport)
    service.phase_poll = "consumed"
    result = service.call("inspect", inspect)
    assert result["state"] == "pending" and result["observed_status"] == "committed"
    assert result["trust_basis"] == "pinned_gateway"
    assert all(result[key] is False for key in ("acceptance_available", "consensus_verified", "cache_eligible"))
    assert [method for method, _ in service.calls].count("public_begin") == 1
    assert all(arguments["receipt"] is not None for method, arguments in service.calls if method == "public_begin")
    with pytest.raises(CommunityError):
        service.call("refresh", service.body)
    assert [method for method, _ in service.calls].count("public_begin") == 1


@pytest.mark.parametrize("fault", ["consent", "extra", "unknown", "missing", "lock", "revoke", "same_id", "receipt"])
def test_refresh_refuses_before_queue(service, fault):
    if fault == "consent":
        service.body["consent"] = False
    elif fault == "extra":
        service.body["proposal"] = asdict(service.item)
    elif fault == "unknown":
        service.phase = "unknown"
    elif fault == "missing":
        service.listed = False
    elif fault == "lock":
        service.active = False
    elif fault == "revoke":
        service.revoke = True
    elif fault == "same_id":
        service.body["request_id"] = service.original
    else:
        service.result["receipt"]["content_hash"] = "e" * 64
    with pytest.raises(CommunityError):
        service.call("refresh", service.body)
    assert all(method != "public_begin" for method, _ in service.calls)


def test_dispatch_unknown_latches_without_clearing_on_inspection(service):
    service.fail_after_submit = True
    with pytest.raises(CommunityError, match="outcome_unknown"):
        service.call("refresh", service.body)
    assert service.service._uncertain is True
    service.call("inspect", dict(id=service.identifier, request_id=service.following))
    assert service.service._uncertain is True
    with pytest.raises(CommunityError):
        service.call("refresh", {**service.body, "request_id": str(uuid.uuid4())})
    assert len(service.used) == 1


@pytest.mark.parametrize("change", [{"state": "accepted"}, {"acceptance_available": True},
    {"cache_eligible": True}, {"consensus_verified": True}, {"private_source": "forbidden"}])
def test_refresh_ui_response_contract_rejects_gateway_promotion(service, change):
    result = service.call("refresh", service.body)
    with pytest.raises(CommunityError):
        CommunityService._validate_response("/community/shares/inspect", {**result, **change})
