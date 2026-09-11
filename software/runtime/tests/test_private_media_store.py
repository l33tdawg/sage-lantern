"""Real storage probe/SDK MockTransport; injected media-method contract doubles.

Media SDK implementation is owned separately. These test the adapter boundary,
not media HTTP signature encoding; no network, keys on disk or file writes.
"""

import hashlib
import json
from types import SimpleNamespace

import httpx
from nacl.signing import SigningKey
import pytest
from sage_sdk.auth import AgentIdentity
from sage_sdk.client import SageClient

from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.private_media_store import ProtectedMediaNotFound, ProtectedMediaOutcomeUnknown, ProtectedMediaStore, ProtectedMediaUnavailable


IDENTIFIER = "00000000-0000-4000-8000-000000000001"
JPEG = (b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x00\xff\xd9")
DIGEST = hashlib.sha256(JPEG).hexdigest()


@pytest.fixture
def rig():
    identity = AgentIdentity(SigningKey(bytes(range(32))))
    actor = RecipientID("home", identity.agent_id)
    fixture = SimpleNamespace(rows={}, media_calls=[], live=True, change=None, error=None,
                              metadata_change={}, get_bytes=None, status=200)
    fixture.observation = dict(schema="sage.message-storage.v1", instance_id="b" * 64,
        agent_id=actor.agent_id, encryption_expected=True, vault_active=True, vault_generation="1",
        stable=True, canonical_send_idempotency=True, encrypted_send_admission=True)

    def handler(request):
        assert request.url.path == "/v1/messages/storage"
        assert request.headers["X-Agent-ID"] == actor.agent_id and request.headers["X-Signature"]
        return httpx.Response(fixture.status, json=fixture.observation)

    endpoint = "http://127.0.0.1:19100"
    client = SageClient(endpoint, identity, timeout=2, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=endpoint, timeout=2, trust_env=False, follow_redirects=False,
                                  transport=httpx.MockTransport(handler))

    def put(*, object_id, jpeg):
        fixture.media_calls.append(("put", object_id))
        if object_id in fixture.rows and fixture.rows[object_id] != jpeg:
            raise RuntimeError("synthetic conflict")
        fixture.rows[object_id] = jpeg
        if fixture.change:
            fixture.change()
        if fixture.error:
            raise fixture.error
        return {**dict(schema="sage.private-media.v1", agent_id=actor.agent_id, object_id=object_id,
            revision=1, length=len(jpeg), digest=hashlib.sha256(jpeg).hexdigest()), **fixture.metadata_change}

    def get(*, object_id):
        fixture.media_calls.append(("get", object_id))
        if fixture.change:
            fixture.change()
        if fixture.error:
            raise fixture.error
        return fixture.get_bytes if fixture.get_bytes is not None else fixture.rows[object_id]

    client.private_media_put = put
    client.private_media_get = get
    probe = MessageStorageProbe(client, actor=actor, endpoint=endpoint)
    fixture.client, fixture.probe = client, probe
    fixture.store = ProtectedMediaStore(client, storage=probe)
    fixture.guard = lambda: fixture.live
    yield fixture
    client._client.close()


def test_roundtrip_unchanged_and_explicit_idempotent_put(rig):
    assert rig.store.put(IDENTIFIER, JPEG, access_check=rig.guard) == DIGEST
    assert rig.store.put(IDENTIFIER, JPEG, access_check=rig.guard) == DIGEST
    assert rig.store.get(IDENTIFIER, access_check=rig.guard, expected_digest=DIGEST) == JPEG
    assert len(rig.rows) == 1
    assert not hasattr(rig.store, "delete")


@pytest.mark.parametrize("identifier", ["bad", IDENTIFIER.upper().replace("4000", "A000"),
    "00000000-0000-0000-0000-000000000000", None, IDENTIFIER + "\n"])
def test_bad_uuid_denied_before_submission(rig, identifier):
    with pytest.raises(ProtectedMediaUnavailable) as error:
        rig.store.put(identifier, JPEG, access_check=rig.guard)
    assert type(error.value) is ProtectedMediaUnavailable and not rig.media_calls


@pytest.mark.parametrize("image", [b"", b"not jpeg", b"\xff\xd8\xff\xd9", bytearray(JPEG),
    JPEG[:-2], b"\xff\xd8" + b"x" * (2 * 1024 * 1024) + b"\xff\xd9"])
def test_bad_jpeg_denied_before_submission(rig, image):
    with pytest.raises(ProtectedMediaUnavailable):
        rig.store.put(IDENTIFIER, image, access_check=rig.guard)
    assert not rig.media_calls


@pytest.mark.parametrize("field,value", [("agent_id", "a" * 64), ("object_id", "bad"),
    ("schema", "other"), ("revision", True), ("revision", 2), ("length", True),
    ("length", len(JPEG) + 1), ("digest", "b" * 64), ("extra", True)])
def test_bad_metadata_is_unknown_not_retry(rig, field, value):
    rig.metadata_change[field] = value
    with pytest.raises(ProtectedMediaOutcomeUnknown):
        rig.store.put(IDENTIFIER, JPEG, access_check=rig.guard)
    assert rig.media_calls == [("put", IDENTIFIER)]


@pytest.mark.parametrize("operation", ["put", "get"])
@pytest.mark.parametrize("change", ["instance_id", "vault_generation", "vault_active", "live"])
def test_epoch_or_lock_change_never_returns_success(rig, operation, change):
    rig.rows[IDENTIFIER] = JPEG
    if change == "live":
        rig.change = lambda: setattr(rig, "live", False)
    else:
        value = "c" * 64 if change == "instance_id" else "2" if change == "vault_generation" else False
        rig.change = lambda: rig.observation.update({change: value})
    expected = ProtectedMediaOutcomeUnknown if operation == "put" else ProtectedMediaUnavailable
    with pytest.raises(expected):
        if operation == "put":
            rig.store.put(IDENTIFIER, JPEG, access_check=rig.guard)
        else:
            rig.store.get(IDENTIFIER, access_check=rig.guard)


@pytest.mark.parametrize("gate", ["live", "vault_active", "encryption_expected", "stable", "agent_id"])
def test_unavailable_before_admission(rig, gate):
    if gate == "live":
        rig.live = False
    else:
        rig.observation[gate] = "a" * 64 if gate == "agent_id" else False
    with pytest.raises(ProtectedMediaUnavailable) as error:
        rig.store.put(IDENTIFIER, JPEG, access_check=rig.guard)
    assert type(error.value) is ProtectedMediaUnavailable and not rig.media_calls


def test_timeout_after_put_never_retries(rig):
    rig.error = httpx.ReadTimeout("SECRET")
    with pytest.raises(ProtectedMediaOutcomeUnknown) as error:
        rig.store.put(IDENTIFIER, JPEG, access_check=rig.guard)
    assert "SECRET" not in str(error.value) and len(rig.media_calls) == 1
    rig.error = None
    assert rig.store.get(IDENTIFIER, access_check=rig.guard, expected_digest=DIGEST) == JPEG


def test_conflicting_put_never_overwrites(rig):
    rig.store.put(IDENTIFIER, JPEG, access_check=rig.guard)
    with pytest.raises(ProtectedMediaOutcomeUnknown):
        rig.store.put(IDENTIFIER, JPEG[:-2] + b"x\xff\xd9", access_check=rig.guard)
    assert rig.rows[IDENTIFIER] == JPEG


@pytest.mark.parametrize("image", [b"bad", JPEG[:-2], bytearray(JPEG)])
def test_corrupt_get_denied(rig, image):
    rig.get_bytes = image
    with pytest.raises(ProtectedMediaUnavailable):
        rig.store.get(IDENTIFIER, access_check=rig.guard)


def test_wrong_history_digest_denied(rig):
    rig.get_bytes = JPEG
    with pytest.raises(ProtectedMediaUnavailable):
        rig.store.get(IDENTIFIER, access_check=rig.guard, expected_digest="a" * 64)


@pytest.mark.parametrize("status", [404, 503])
def test_probe_route_failure_denies_before_media_call(rig, status):
    rig.status = status
    with pytest.raises(ProtectedMediaUnavailable):
        rig.store.get(IDENTIFIER, access_check=rig.guard)
    assert not rig.media_calls


def test_any_media_404_unavailable_not_confirmed_absence(rig):
    request = httpx.Request("GET", "http://127.0.0.1:19100/v1/private-media/" + IDENTIFIER)
    rig.error = httpx.HTTPStatusError("missing route or object", request=request,
                                     response=httpx.Response(404, request=request))
    with pytest.raises(ProtectedMediaUnavailable) as error:
        rig.store.get(IDENTIFIER, access_check=rig.guard)
    assert str(error.value) == "protected_media_unavailable"


def test_exact_probe_client_binding(rig):
    with pytest.raises(ProtectedMediaUnavailable):
        ProtectedMediaStore(object(), storage=rig.probe)
    rig.probe.endpoint = "http://127.0.0.1:19101"
    with pytest.raises(ProtectedMediaUnavailable):
        rig.store.put(IDENTIFIER, JPEG, access_check=rig.guard)
    assert not rig.media_calls


@pytest.fixture
def http_rig():
    identity = AgentIdentity(SigningKey(bytes(range(32))))
    actor = RecipientID("home", identity.agent_id)
    fixture = SimpleNamespace(rows={}, headers={}, status=200, mutate=None, calls=[])
    observation = dict(schema="sage.message-storage.v1", instance_id="b" * 64,
        agent_id=actor.agent_id, encryption_expected=True, vault_active=True, vault_generation="1",
        stable=True, canonical_send_idempotency=True, encrypted_send_admission=True)
    def handler(request):
        assert request.headers["X-Agent-ID"] == actor.agent_id and request.headers["X-Signature"]
        if request.url.path == "/v1/messages/storage":
            return httpx.Response(200, json=observation)
        assert request.url.path == "/v1/private-media/" + IDENTIFIER
        fixture.calls.append(request.method)
        headers = {"X-SAGE-Private-Media-Schema": "sage.private-media.v1", "Cache-Control": "no-store",
                   "X-Content-Type-Options": "nosniff"}
        if fixture.status != 200:
            if fixture.mutate:
                fixture.mutate(observation)
            return httpx.Response(fixture.status, headers={**headers, **fixture.headers},
                                  stream=httpx.ByteStream(b""))
        if request.method == "PUT":
            assert request.content == JPEG and request.headers["content-type"] == "image/jpeg"
            fixture.rows[IDENTIFIER] = request.content
            body = json.dumps(dict(schema="sage.private-media.v1", agent_id=actor.agent_id,
                object_id=IDENTIFIER, revision=1, length=len(JPEG), digest=DIGEST)).encode()
            headers["Content-Type"] = "application/json"
        else:
            body = fixture.rows[IDENTIFIER]
            headers.update({"Content-Type": "image/jpeg", "X-SAGE-Agent-ID": actor.agent_id,
                            "X-SAGE-Media-ID": IDENTIFIER, "X-SAGE-Media-SHA256": DIGEST})
        headers["Content-Length"] = str(len(body))
        if fixture.mutate:
            fixture.mutate(observation)
        return httpx.Response(200, headers={**headers, **fixture.headers}, stream=httpx.ByteStream(body))
    endpoint = "http://127.0.0.1:19100"
    client = SageClient(endpoint, identity, timeout=2, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=endpoint, timeout=2, trust_env=False, follow_redirects=False,
                                  transport=httpx.MockTransport(handler))
    fixture.store = ProtectedMediaStore(client, storage=MessageStorageProbe(client, actor=actor, endpoint=endpoint))
    yield fixture
    client._client.close()


def test_real_sdk_raw_http_roundtrip(http_rig):
    assert http_rig.store.put(IDENTIFIER, JPEG, access_check=lambda: True) == DIGEST
    assert http_rig.store.get(IDENTIFIER, access_check=lambda: True, expected_digest=DIGEST) == JPEG
    assert http_rig.calls == ["PUT", "GET"]


def test_real_sdk_epoch_change_after_write_unknown(http_rig):
    http_rig.mutate = lambda observation: observation.update(vault_generation="2")
    with pytest.raises(ProtectedMediaOutcomeUnknown):
        http_rig.store.put(IDENTIFIER, JPEG, access_check=lambda: True)
    assert http_rig.calls == ["PUT"]


def test_real_sdk_schema_marked_missing_only(http_rig):
    http_rig.status = 404
    with pytest.raises(ProtectedMediaNotFound):
        http_rig.store.get(IDENTIFIER, access_check=lambda: True)
    http_rig.headers["X-SAGE-Private-Media-Schema"] = ""
    with pytest.raises(ProtectedMediaUnavailable) as error:
        http_rig.store.get(IDENTIFIER, access_check=lambda: True)
    assert type(error.value) is ProtectedMediaUnavailable


@pytest.mark.parametrize("change", [dict(vault_generation="2"), dict(vault_active=False), dict(agent_id="a" * 64)])
def test_confirmed_missing_still_requires_post_probe(http_rig, change):
    http_rig.status = 404
    http_rig.mutate = lambda observation: observation.update(change)
    with pytest.raises(ProtectedMediaUnavailable) as error:
        http_rig.store.get(IDENTIFIER, access_check=lambda: True)
    assert type(error.value) is ProtectedMediaUnavailable


@pytest.mark.parametrize("header,value", [("X-SAGE-Agent-ID", "a" * 64), ("X-SAGE-Media-ID", "bad"),
    ("X-SAGE-Media-SHA256", "a" * 64), ("Content-Length", "1"),
    ("X-SAGE-Private-Media-Schema", "other"), ("Cache-Control", "public"),
    ("Content-Type", "text/plain"), ("X-Content-Type-Options", "")])
def test_real_sdk_get_rejects_corrupt_binding_headers(http_rig, header, value):
    http_rig.rows[IDENTIFIER] = JPEG
    http_rig.headers[header] = value
    with pytest.raises(ProtectedMediaUnavailable):
        http_rig.store.get(IDENTIFIER, access_check=lambda: True)
