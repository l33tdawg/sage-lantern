from __future__ import annotations

import argparse
import base64
import os
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from .config import Settings
from .crypto import AuthorityKeys, pem_certificate_fingerprint
from .database import Database


def stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def run() -> None:
    parser = argparse.ArgumentParser(
        description="Reissue one enrolled device certificate from its proof-bound CSR",
    )
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--csr", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    settings = Settings()
    database = Database(settings.database_path)
    keys = AuthorityKeys.load_or_create(settings.data_dir)
    csr_pem = args.csr.read_text(encoding="ascii")

    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT d.*, e.id AS enrollment_id, e.tls_csr_fingerprint
            FROM devices d JOIN enrollments e ON e.device_id=d.device_id
            WHERE d.device_id=? AND e.status='issued'
            ORDER BY e.issued_at DESC LIMIT 1
            """,
            (args.device_id,),
        ).fetchone()
        if row is None:
            raise SystemExit("device has no issued proof-bound enrollment")
        management_raw = base64.b64decode(row["management_public_key"], validate=True)
        _csr, csr_fingerprint, tls_key_fingerprint, normalized_csr = keys.parse_csr(
            csr_pem,
            management_raw,
        )
        if csr_fingerprint != row["tls_csr_fingerprint"]:
            raise SystemExit("CSR fingerprint does not match the proof-bound enrollment")
        if tls_key_fingerprint != row["tls_key_fingerprint"]:
            raise SystemExit("CSR key does not match the enrolled TLS identity")

        certificate = keys.issue_client_certificate(
            normalized_csr,
            args.device_id,
            settings.client_cert_days,
        )
        certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
        certificate_fingerprint = pem_certificate_fingerprint(certificate)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, certificate_pem)
            os.fsync(fd)
        finally:
            os.close(fd)

        previous = row["certificate_fingerprint"]
        connection.execute(
            "UPDATE devices SET certificate_fingerprint=?,certificate_serial=? WHERE device_id=?",
            (certificate_fingerprint, str(certificate.serial_number), args.device_id),
        )
        connection.execute(
            "UPDATE enrollments SET certificate_fingerprint=?,issued_at=? WHERE id=?",
            (certificate_fingerprint, stamp(), row["enrollment_id"]),
        )
        Database.audit(
            connection,
            stamp(),
            "certificate.reissued",
            "host-maintenance",
            device_id=args.device_id,
            enrollment_id=row["enrollment_id"],
            detail={
                "previous_certificate_fingerprint": previous,
                "certificate_fingerprint": certificate_fingerprint,
            },
        )
        connection.commit()
    print(certificate_fingerprint)


if __name__ == "__main__":
    run()
