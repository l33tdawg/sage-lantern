from __future__ import annotations

import base64
import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fleet_authority.app import create_app, stamp
from fleet_authority.config import Settings
from fleet_authority.crypto import canonical_json, fingerprint
from fleet_authority.observations import verify_observation

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
TOKEN = "a" * 48


@pytest.fixture
def authority(tmp_path: Path):
    app = create_app(Settings(admin_token=SecretStr(TOKEN), data_dir=tmp_path, enrollment_ttl_seconds=900))
    app.state.clock = lambda: NOW
    with TestClient(app) as client:
        yield client, app


def admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-Fleet-Operator-Id": "operator:test@example.org"}


def identity():
    management = ed25519.Ed25519PrivateKey.generate()
    raw = management.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    tls_key = ec.generate_private_key(ec.SECP256R1())
    csr = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bootstrap")])).sign(tls_key, hashes.SHA256())
    return management, base64.b64encode(raw).decode(), fingerprint(raw), tls_key, csr.public_bytes(serialization.Encoding.PEM).decode()


def create(client: TestClient, device_id="unit-a", **extra):
    return client.post("/v1/admin/enrollments", headers=admin(), json={"device_id": device_id, **extra})


def claim(client: TestClient, created: dict, ident):
    management, public, fp, _, csr = ident
    result = client.post(f"/v1/enrollments/{created['enrollment_id']}/claim", json={"setup_secret":created["setup_secret"],"management_public_key":public,"management_fingerprint":fp,"tls_csr_pem":csr})
    return result


def enroll(client: TestClient, device_id="unit-a", ident=None):
    ident = ident or identity(); created = create(client,device_id).json(); claimed = claim(client,created,ident).json()
    approved=client.post(f"/v1/admin/enrollments/{created['enrollment_id']}/approve",headers=admin(),json={"comparison_code":claimed["comparison_code"]})
    assert approved.status_code == 200
    management, _, fp, _, csr_pem = ident
    csr=x509.load_pem_x509_csr(csr_pem.encode())
    proof={"schema":"sage.fleet.enrollment.proof.v1","enrollment_id":created["enrollment_id"],"device_id":device_id,"management_fingerprint":fp,"tls_csr_fingerprint":fingerprint(csr.public_bytes(serialization.Encoding.DER)),"challenge":claimed["proof_challenge"]}
    signature=base64.b64encode(management.sign(canonical_json(proof))).decode()
    completed=client.post(f"/v1/enrollments/{created['enrollment_id']}/complete",json={"proof_signature":signature})
    assert completed.status_code == 200, completed.text
    return ident, created, claimed, completed.json()


def observation(management, device_id, fp, sequence=1, state=None):
    state = state or {"health":"ok","neighbors":2,"online":True}
    state_hash=hashlib.sha256(canonical_json(state)).hexdigest()
    value={"schema":"sage.lantern.management.observation.v1","device_id":device_id,"sequence":sequence,"observed_at":stamp(NOW-timedelta(seconds=5)),"freshness_expires_at":stamp(NOW+timedelta(minutes=5)),"clock_confidence":"ntp-synchronized","source_fingerprint":fp,"state":state,"state_hash":state_hash}
    return {"observation":value,"signature":base64.b64encode(management.sign(canonical_json(value))).decode()}


def cert_headers(completed):
    return {"X-Fleet-Client-Cert-Verified":"SUCCESS","X-Fleet-Client-Cert-Fingerprint":completed["certificate_fingerprint"]}


def test_health_and_admin_auth(authority):
    client,_=authority
    assert client.get("/healthz").json()=={"status":"ok","service":"sage-fleet-authority"}
    assert client.post("/v1/admin/enrollments",json={"device_id":"unit-a"}).status_code==401
    assert client.post("/v1/admin/enrollments",headers={"Authorization":"Bearer wrong"},json={"device_id":"unit-a"}).status_code==401


def test_complete_positive_path_certificate_once_and_no_secret_listing(authority):
    client,app=authority; ident,created,claimed,completed=enroll(client)
    cert=x509.load_pem_x509_certificate(completed["client_certificate_pem"].encode())
    assert cert.issuer == app.state.keys.ca_cert.subject
    key_usage=cert.extensions.get_extension_for_class(x509.KeyUsage).value
    extended_usage=cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert key_usage.digital_signature is True
    assert key_usage.key_encipherment is False
    assert x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH in extended_usage
    assert completed["command_authority"] == claimed["command_authority"]
    assert completed["command_authority"]["fingerprint"] != ident[2]
    listed=client.get("/v1/admin/enrollments",headers=admin()).json()["enrollments"][0]
    assert listed["enrollment_id"] == created["enrollment_id"]
    assert "setup_secret" not in listed and "tls_csr_pem" not in listed and listed["status"]=="issued"
    again=client.post(f"/v1/enrollments/{created['enrollment_id']}/complete",json={"proof_signature":"A"*88})
    assert again.status_code==409
    with app.state.db.connect() as db:
        row=db.execute("SELECT secret_hash,tls_csr_pem,proof_challenge FROM enrollments").fetchone()
        assert tuple(row)==("",None,None)


def test_claim_is_single_use_even_with_same_secret(authority):
    client,_=authority; created=create(client).json(); ident=identity()
    assert claim(client,created,ident).status_code==200
    assert claim(client,created,ident).status_code==409


def test_wrong_secret_code_approval_and_proof_fail_closed(authority):
    client,_=authority; created=create(client).json(); ident=identity(); bad={**created,"setup_secret":"x"*43}
    assert claim(client,bad,ident).status_code==401
    claimed=claim(client,created,ident).json(); endpoint=f"/v1/admin/enrollments/{created['enrollment_id']}/approve"
    assert client.post(endpoint,headers=admin(),json={"comparison_code":"000000" if claimed["comparison_code"]!="000000" else "999999"}).status_code==403
    assert client.post(endpoint,headers=admin(),json={"comparison_code":claimed["comparison_code"]}).status_code==200
    assert client.post(f"/v1/enrollments/{created['enrollment_id']}/complete",json={"proof_signature":base64.b64encode(b"x"*64).decode()}).status_code==403


def test_expired_enrollment_rejected(authority):
    client,app=authority; created=create(client,expires_in_seconds=60).json(); app.state.clock=lambda: NOW+timedelta(seconds=61)
    assert claim(client,created,identity()).status_code==410


def test_duplicate_management_and_tls_identity_rejected(authority):
    client,_=authority; shared=identity(); enroll(client,"unit-a",shared)
    created=create(client,"unit-b").json()
    assert claim(client,created,shared).status_code==409


def test_management_key_cannot_be_reused_as_tls_key(authority):
    client,_=authority; created=create(client).json(); management=ed25519.Ed25519PrivateKey.generate()
    raw=management.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    csr=x509.CertificateSigningRequestBuilder().subject_name(x509.Name([])).sign(management,algorithm=None)
    response=client.post(f"/v1/enrollments/{created['enrollment_id']}/claim",json={"setup_secret":created["setup_secret"],"management_public_key":base64.b64encode(raw).decode(),"management_fingerprint":fingerprint(raw),"tls_csr_pem":csr.public_bytes(serialization.Encoding.PEM).decode()})
    assert response.status_code==422 and "separate" in response.text


def test_observation_requires_verified_cert_then_accepts_and_lists(authority):
    client,_=authority; ident,_,_,completed=enroll(client); management,_,fp,_,_=ident; body=observation(management,"unit-a",fp)
    assert client.post("/v1/device/observations",json=body).status_code==401
    accepted=client.post("/v1/device/observations",headers=cert_headers(completed),json=body)
    assert accepted.status_code==202 and accepted.json()["accepted"] is True
    rows=client.get("/v1/admin/observations?device_id=unit-a",headers=admin()).json()["observations"]
    assert rows[0]["state"]==body["observation"]["state"] and "signature" not in rows[0]


def test_observation_replay_tamper_wrong_cert_and_staleness_rejected(authority):
    client,_=authority; ident,_,_,completed=enroll(client); management,_,fp,_,_=ident; headers=cert_headers(completed); body=observation(management,"unit-a",fp)
    first=client.post("/v1/device/observations",headers=headers,json=body)
    retry=client.post("/v1/device/observations",headers=headers,json=body)
    assert first.status_code==retry.status_code==202 and first.json()==retry.json()
    tampered=observation(management,"unit-a",fp,sequence=2); tampered["observation"]["state"]["health"]="bad"
    assert client.post("/v1/device/observations",headers=headers,json=tampered).status_code==422
    wrong={**headers,"X-Fleet-Client-Cert-Fingerprint":"sha256:"+"0"*64}
    assert client.post("/v1/device/observations",headers=wrong,json=observation(management,"unit-a",fp,sequence=2)).status_code==403
    stale=observation(management,"unit-a",fp,sequence=2); stale["observation"]["freshness_expires_at"]=stamp(NOW-timedelta(seconds=1)); stale["signature"]=base64.b64encode(management.sign(canonical_json(stale["observation"]))).decode()
    assert client.post("/v1/device/observations",headers=headers,json=stale).status_code==422


def test_audit_is_sanitized(authority):
    client,_=authority; ident,created,_,_=enroll(client); serialized=str(client.get("/v1/admin/audit-events",headers=admin()).json())
    assert created["setup_secret"] not in serialized and ident[1] not in serialized and "BEGIN CERTIFICATE REQUEST" not in serialized
    assert "operator:test@example.org" in serialized


def test_atomic_sequence_check_allows_only_one_concurrent_insert(authority):
    client,_=authority; ident,_,_,completed=enroll(client); management,_,fp,_,_=ident
    body=observation(management,"unit-a",fp,sequence=9); headers=cert_headers(completed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda _: client.post("/v1/device/observations",headers=headers,json=body).status_code,range(2)))
    assert statuses==[202,202]
    with authority[1].state.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM observations WHERE device_id='unit-a' AND sequence=9").fetchone()[0]==1


def test_valid_but_different_envelope_cannot_reuse_sequence(authority):
    client,_=authority; ident,_,_,completed=enroll(client); management,_,fp,_,_=ident; headers=cert_headers(completed)
    first=observation(management,"unit-a",fp,sequence=4,state={"health":"ok"})
    different=observation(management,"unit-a",fp,sequence=4,state={"health":"degraded"})
    assert client.post("/v1/device/observations",headers=headers,json=first).status_code==202
    response=client.post("/v1/device/observations",headers=headers,json=different)
    assert response.status_code==409 and "different envelope" in response.text


def test_exact_retry_still_returns_original_ack_after_freshness_expires(authority):
    client,app=authority; ident,_,_,completed=enroll(client); management,_,fp,_,_=ident; headers=cert_headers(completed)
    body=observation(management,"unit-a",fp,sequence=5)
    first=client.post("/v1/device/observations",headers=headers,json=body)
    app.state.clock=lambda: NOW+timedelta(days=2)
    retry=client.post("/v1/device/observations",headers=headers,json=body)
    assert first.status_code==retry.status_code==202 and first.json()==retry.json()


def test_existing_python_device_agent_golden_vector_verifies():
    signed={"observation":{"clock_confidence":"ntp-synchronized","device_id":"lantern-golden","freshness_expires_at":"2026-09-02T08:05:00Z","observed_at":"2026-09-02T08:00:00Z","schema":"sage.lantern.management.observation.v1","sequence":42,"source_fingerprint":"sha256:56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c","state":{"nested":{"enabled":True,"labels":["kampung","灯"]},"temperature_millicelsius":48500},"state_hash":"57554fe607d43b730773a09134d8f992f5fcbc695abc1a8cb6af0433fd22c979"},"signature":"yBL01UUJwf/38+SIwtIQMSYv77+MOZcX4jqQze9SsjfWu6C+cTpsVysAkODRu13il8bndB7ZRz3tb3z1A9IIDA=="}
    identity={"device_id":"lantern-golden","management_public_key":"A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=","management_fingerprint":"sha256:56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c"}
    assert verify_observation(signed,identity,datetime(2026,9,2,8,1,tzinfo=UTC))["sequence"]==42
