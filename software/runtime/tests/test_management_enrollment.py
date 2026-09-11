import base64
import hashlib
import json
import ssl
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from sage_lantern.management import DeviceManagementIdentity, canonical_json
from sage_lantern.management_enrollment import EnrollmentClient, TLSIdentity, load_enrolled_config


ENROLLMENT_ID = "11111111-2222-4333-8444-555555555555"


class FakeResponse:
    def __init__(self, status, value):
        self.status = status
        self.body = json.dumps(value).encode()

    def read(self, amount=None):
        return self.body[:amount]


class FakeConnection:
    responses = []
    requests = []

    def __init__(self, host, port, **kwargs):
        self.host, self.port, self.kwargs = host, port, kwargs

    def request(self, method, path, body, headers):
        type(self).requests.append((method, path, json.loads(body), headers))

    def getresponse(self):
        return type(self).responses.pop(0)

    def close(self):
        pass


def certificate_for(tls, device_id="lantern-a"):
    csr = x509.load_pem_x509_csr(tls.csr_pem.encode())
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Fleet CA")])
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .sign(ca_key, hashes.SHA256())
    )
    certificate = (
        x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)]))
        .issuer_name(ca.subject).public_key(csr.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), False)
        .sign(ca_key, hashes.SHA256())
    )
    return certificate, ca


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "agent"
        self.identity = DeviceManagementIdentity.load_or_create(self.root / "management-identity")
        self.tls = TLSIdentity.load_or_create(self.root, self.identity.fingerprint)
        command_key = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.authority = {
            "algorithm": "Ed25519",
            "public_key": base64.b64encode(command_key).decode(),
            "fingerprint": "sha256:" + hashlib.sha256(command_key).hexdigest(),
        }
        self.client = EnrollmentClient(
            authority_url="https://fleet-api.sage.delivery",
            state_root=self.root,
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            connection_factory=FakeConnection,
        )
        FakeConnection.requests = []
        FakeConnection.responses = []

    def tearDown(self):
        self.temp.cleanup()

    def claim_response(self, **changes):
        value = {
            "enrollment_id": ENROLLMENT_ID,
            "status": "claimed",
            "comparison_code": "042817",
            "proof_challenge": "challenge-value",
            "command_authority": self.authority,
        }
        value.update(changes)
        return FakeResponse(200, value)

    def claim(self):
        FakeConnection.responses.append(self.claim_response())
        return self.client.claim(
            enrollment_id=ENROLLMENT_ID, setup_secret="single-use", expected_device_id="lantern-a",
            identity=self.identity, tls_identity=self.tls,
        )

    def test_claim_and_complete_bind_keys_and_persist_separate_pins(self):
        pending = self.claim()
        self.assertEqual(pending.comparison_code, "042817")
        request = FakeConnection.requests[0]
        self.assertEqual(request[1], f"/v1/enrollments/{ENROLLMENT_ID}/claim")
        self.assertEqual(request[2]["setup_secret"], "single-use")
        self.assertNotIn("single-use", (self.root / "enrollment-pending.json").read_text())
        self.assertEqual((self.root / "management-tls.key").stat().st_mode & 0o777, 0o600)

        certificate, ca = certificate_for(self.tls)
        certificate_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
        fingerprint = "sha256:" + certificate.fingerprint(hashes.SHA256()).hex()
        FakeConnection.responses.append(FakeResponse(200, {
            "device_id": "lantern-a", "status": "issued", "client_certificate_pem": certificate_pem,
            "ca_certificate_pem": ca.public_bytes(serialization.Encoding.PEM).decode(),
            "certificate_fingerprint": fingerprint, "command_authority": self.authority,
        }))
        config = self.client.complete(identity=self.identity, tls_identity=self.tls)
        proof_signature = FakeConnection.requests[1][2]["proof_signature"]
        proof = {
            "schema": "sage.fleet.enrollment.proof.v1", "enrollment_id": ENROLLMENT_ID,
            "device_id": "lantern-a", "management_fingerprint": self.identity.fingerprint,
            "tls_csr_fingerprint": self.tls.csr_fingerprint, "challenge": "challenge-value",
        }
        self.identity.signing_key.verify_key.verify(canonical_json(proof).encode(), base64.b64decode(proof_signature))
        self.assertEqual(config.observation_endpoint, "https://fleet-api.sage.delivery/v1/device/observations")
        self.assertEqual(json.loads((self.root / "command-authority.json").read_text()), self.authority)
        loaded, pin = load_enrolled_config(self.root)
        self.assertEqual(loaded.command_authority_fingerprint, pin.fingerprint)
        self.assertFalse((self.root / "enrollment-pending.json").exists())

    def test_rejects_bad_comparison_code_without_persisting_claim(self):
        FakeConnection.responses.append(self.claim_response(comparison_code="12A456"))
        with self.assertRaisesRegex(ValueError, "six ASCII digits"):
            self.client.claim(
                enrollment_id=ENROLLMENT_ID, setup_secret="single-use", expected_device_id="lantern-a",
                identity=self.identity, tls_identity=self.tls,
            )
        self.assertFalse((self.root / "enrollment-pending.json").exists())

    def test_rejects_changed_command_authority_on_completion(self):
        self.claim()
        certificate, ca = certificate_for(self.tls)
        other_key = b"z" * 32
        other = {"algorithm":"Ed25519", "public_key":base64.b64encode(other_key).decode(),
                 "fingerprint":"sha256:" + hashlib.sha256(other_key).hexdigest()}
        FakeConnection.responses.append(FakeResponse(200, {
            "device_id":"lantern-a", "status":"issued",
            "client_certificate_pem":certificate.public_bytes(serialization.Encoding.PEM).decode(),
            "ca_certificate_pem":ca.public_bytes(serialization.Encoding.PEM).decode(),
            "certificate_fingerprint":"sha256:" + certificate.fingerprint(hashes.SHA256()).hex(),
            "command_authority":other,
        }))
        with self.assertRaisesRegex(ValueError, "changed"):
            self.client.complete(identity=self.identity, tls_identity=self.tls)
        self.assertTrue((self.root / "enrollment-pending.json").exists())

    def test_loader_rejects_command_pin_mismatch(self):
        self.claim()
        certificate, ca = certificate_for(self.tls)
        FakeConnection.responses.append(FakeResponse(200, {
            "device_id":"lantern-a", "status":"issued",
            "client_certificate_pem":certificate.public_bytes(serialization.Encoding.PEM).decode(),
            "ca_certificate_pem":ca.public_bytes(serialization.Encoding.PEM).decode(),
            "certificate_fingerprint":"sha256:" + certificate.fingerprint(hashes.SHA256()).hex(),
            "command_authority":self.authority,
        }))
        self.client.complete(identity=self.identity, tls_identity=self.tls)
        other_key = b"q" * 32
        (self.root / "command-authority.json").write_text(json.dumps({
            "algorithm":"Ed25519", "public_key":base64.b64encode(other_key).decode(),
            "fingerprint":"sha256:" + hashlib.sha256(other_key).hexdigest(),
        }))
        with self.assertRaisesRegex(ValueError, "pin does not match"):
            load_enrolled_config(self.root)


if __name__ == "__main__":
    unittest.main()
