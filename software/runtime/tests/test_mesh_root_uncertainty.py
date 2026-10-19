import copy
import json
import os
import uuid
import pytest

from sage_lantern.community_service import CommunityMeshProvider
from sage_lantern.mesh_receipt_sender import MeshReceiptSender
from sage_lantern.workflow_store import WorkflowOutcomeUnknown, WorkflowStore, WorkflowUnavailable
from sage_lantern.mesh_inbox import ASSEMBLY_SCHEMA, INBOX_SCHEMA, REJECTION_SCHEMA
from test_mesh_daemon import runtime
from test_mesh_runtime import factory
from test_mesh_background import incoming, valid_sender_radio
from test_mesh_render_ipc import rendered, begin, acknowledge
from test_storage_supervisor import setup as supervisor_setup
from sage_lantern.storage_supervisor import RootStorageSupervisor, StorageSupervisorError


@pytest.mark.parametrize("stage", ["assembly", "admission", "complete", "rejection"])
@pytest.mark.parametrize("failure", ["before", "after", "definite_before_write"])
def test_inbox_write_unknown_cannot_resume_root(runtime, supervisor_setup, monkeypatch, stage, failure):
    state, supervisor, resource, config, opened = runtime
    config.update(messaging_enabled=True, dispatch_enabled=True, delivery_receipts_enabled=True)
    # The root now admits its first worker through restart_receiving too.
    # Accelerate that actual path so all fragments reach the injected write
    # within the bounded join, instead of testing an unconsumed input queue.
    restart = CommunityMeshProvider.restart_receiving
    monkeypatch.setattr(CommunityMeshProvider, "restart_receiving",
        lambda provider, **kwargs: restart(provider, **kwargs, interval=0.2))
    original = WorkflowStore.put
    failed = []

    def put(store, identifier, kind, revision, payload, **kwargs):
        target = (payload.get("schema"), payload.get("state"))
        selected = dict(assembly=(ASSEMBLY_SCHEMA, "collecting"), admission=(INBOX_SCHEMA, None),
            complete=(ASSEMBLY_SCHEMA, "complete"), rejection=(REJECTION_SCHEMA, None))[stage]
        if kind != "mesh_inbound" or target != selected or failed:
            return original(store, identifier, kind, revision, payload, **kwargs)
        failed.append(True)
        if failure == "after":
            original(store, identifier, kind, revision, payload, **kwargs)
        if failure == "definite_before_write":
            raise WorkflowUnavailable()
        raise WorkflowOutcomeUnknown()

    monkeypatch.setattr(WorkflowStore, "put", put)
    resource.invoke("ready", {}, lambda: True)
    live = resource._provider._runtime
    if stage == "rejection":
        live.inbox._resolve = lambda message: None
    local_id = incoming(state)
    live._poller.join(15)
    assert failed and not live._poller.is_alive()
    assert state.rows[config["receiver_slot"]]["payload"]["state"] == "captured"
    assert not state.transmits
    if stage == "admission" and failure == "after":
        assert local_id in state.rows
    if failure == "definite_before_write":
        assert not live.inbox._uncertain and live.admission_safe()
        assert live.pause_receiving() and resource.active()
        assert resource.invoke("ready", {}, lambda: True)
        return
    before = copy.deepcopy(state.rows)
    assert live.inbox._uncertain and not live.admission_safe() and not resource.active()
    assert not live.pause_receiving()
    with pytest.raises(RuntimeError):
        resource.invoke("ready", {}, lambda: True)
    supervisor.phase = "draining"
    assert resource.close(3) is False and not state.released
    assert state.rows == before and not state.transmits
    durable, backend, lifecycle = supervisor_setup
    durable.start()
    durable.own_resource(resource)
    with pytest.raises(StorageSupervisorError):
        durable.drain(str(uuid.uuid4()))
    evidence = (durable.directory / "fence.json").read_bytes()
    assert json.loads(evidence)["phase"] == "recovery_required"
    assert "stop_vault" not in backend.events
    durable.close()
    with pytest.raises(StorageSupervisorError):
        RootStorageSupervisor(durable.directory, backend=backend, snapshot=lambda: lifecycle["snapshot"],
            access_epoch=lambda: lifecycle["epoch"], owner_uid=os.geteuid())
    assert (durable.directory / "fence.json").read_bytes() == evidence
    assert state.rows == before and not state.transmits


@pytest.mark.parametrize("failure", ["before", "after", "definite"])
def test_ready_cannot_resume_unknown_receipt(runtime, monkeypatch, failure):
    state, supervisor, resource, config, opened = runtime
    config.update(messaging_enabled=True, dispatch_enabled=True, delivery_receipts_enabled=True)
    restart = CommunityMeshProvider.restart_receiving
    monkeypatch.setattr(CommunityMeshProvider, "restart_receiving",
        lambda provider, **kwargs: restart(provider, **kwargs, interval=0.2))
    original = MeshReceiptSender._put
    failed = []

    def put(sender, identifier, body, revision, access_check):
        if failed:
            return original(sender, identifier, body, revision, access_check)
        failed.append(True)
        if failure == "after":
            original(sender, identifier, body, revision, access_check)
        if failure == "definite":
            raise PermissionError()
        raise WorkflowOutcomeUnknown()

    monkeypatch.setattr(MeshReceiptSender, "_put", put)
    incoming(state)
    resource.invoke("ready", {}, lambda: True)
    live = resource._provider._runtime
    live._poller.join(15)
    assert not live._poller.is_alive() and failed
    assert state.rows[config["receiver_slot"]]["payload"]["state"] == "captured"
    assert not state.transmits
    if failure == "definite":
        assert live.admission_safe() and live.pause_receiving() and resource.active()
        return
    before = copy.deepcopy(state.rows)
    assert live.receipts._uncertain and not live.admission_safe()
    assert not live.pause_receiving() and not resource.active()
    for _ in range(2):
        with pytest.raises(RuntimeError):
            resource.invoke("ready", {}, lambda: True)
    assert state.rows == before and not state.transmits


def test_captured_commit_unknown_refuses_root_ready(runtime, monkeypatch):
    state, supervisor, resource, config, opened = runtime
    config.update(messaging_enabled=True, dispatch_enabled=True, delivery_receipts_enabled=True)
    original = WorkflowStore.put
    failed = []

    def put(store, identifier, kind, revision, payload, **kwargs):
        result = original(store, identifier, kind, revision, payload, **kwargs)
        if kind == "mesh_inbound" and payload.get("state") == "captured" and not failed:
            failed.append(True)
            raise WorkflowOutcomeUnknown()
        return result

    monkeypatch.setattr(WorkflowStore, "put", put)
    resource.invoke("ready", {}, lambda: True)
    incoming(state)
    live = resource._provider._runtime
    live._poller.join(15)
    assert not live._poller.is_alive() and failed and live.receiver._uncertain
    before = copy.deepcopy(state.rows)
    assert not live.pause_receiving() and not resource.active()
    with pytest.raises(RuntimeError):
        resource.invoke("ready", {}, lambda: True)
    assert state.rows == before and not state.transmits


def test_read_receipt_unknown_also_refuses_root_ready(rendered, monkeypatch):
    state, supervisor, resource, facade, item = rendered
    challenge = begin(rendered)
    original = MeshReceiptSender._put

    def put(sender, *args, **kwargs):
        original(sender, *args, **kwargs)
        raise WorkflowOutcomeUnknown()

    monkeypatch.setattr(MeshReceiptSender, "_put", put)
    with pytest.raises(RuntimeError):
        acknowledge(rendered, challenge)
    live = resource._provider._runtime
    before = copy.deepcopy(state.rows)
    assert live.receipts._uncertain and not resource.active()
    assert not live.pause_receiving()
    with pytest.raises(RuntimeError):
        resource.invoke("ready", {}, lambda: True)
    assert state.rows == before and not state.transmits
