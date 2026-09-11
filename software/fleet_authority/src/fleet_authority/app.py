from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .crypto import AuthorityKeys, b64decode, canonical_json, fingerprint, pem_certificate_fingerprint
from .database import Database
from .observations import verify_observation

DEVICE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")


def utcnow() -> datetime:
    return datetime.now(UTC)


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateEnrollment(StrictModel):
    device_id: str = Field(min_length=1, max_length=64)
    expires_in_seconds: int | None = Field(default=None, ge=60, le=3600)


class ClaimEnrollment(StrictModel):
    setup_secret: str = Field(min_length=40, max_length=128)
    management_public_key: str
    management_fingerprint: str
    tls_csr_pem: str = Field(max_length=16_384)


class ApproveEnrollment(StrictModel):
    comparison_code: str = Field(pattern=r"^\d{6}$")


class CompleteEnrollment(StrictModel):
    proof_signature: str


def problem(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    db, keys = Database(settings.database_path), AuthorityKeys.load_or_create(settings.data_dir)
    app = FastAPI(title="SAGE Fleet Authority", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.db, app.state.keys, app.state.clock = settings, db, keys, utcnow

    def admin(
        authorization: Annotated[str | None, Header()] = None,
        x_fleet_operator_id: Annotated[str | None, Header()] = None,
    ) -> str:
        expected = "Bearer " + settings.admin_token.get_secret_value()
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise problem(status.HTTP_401_UNAUTHORIZED, "admin authentication required")
        operator_id = x_fleet_operator_id or "admin-token"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9@._:-]{0,127}", operator_id):
            raise problem(400, "X-Fleet-Operator-Id is invalid")
        return operator_id

    def now() -> datetime:
        return app.state.clock().astimezone(UTC)

    def public_enrollment(row: sqlite3.Row) -> dict[str, Any]:
        result = {k: row[k] for k in ("device_id", "created_at", "expires_at", "status", "claimed_at", "comparison_code", "approved_at", "issued_at", "management_fingerprint", "tls_csr_fingerprint", "tls_key_fingerprint", "certificate_fingerprint")}
        if result["status"] in {"pending", "claimed", "approved"} and result["expires_at"] <= stamp(now()):
            result["status"] = "expired"
            result["comparison_code"] = None
        return {"enrollment_id": row["id"], **result}

    @app.get("/healthz")
    def health() -> dict[str, str]:
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "service": "sage-fleet-authority"}

    @app.post("/v1/admin/enrollments", status_code=201)
    def create_enrollment(body: CreateEnrollment, operator_id: str = Depends(admin)) -> dict[str, Any]:
        if not DEVICE_ID.fullmatch(body.device_id):
            raise problem(422, "device_id must be a lowercase stable identifier")
        instant = now(); enrollment_id = str(uuid.uuid4()); secret = secrets.token_urlsafe(32)
        expires = instant + timedelta(seconds=body.expires_in_seconds or settings.enrollment_ttl_seconds)
        try:
            with db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute("SELECT 1 FROM devices WHERE device_id=?", (body.device_id,)).fetchone():
                    raise problem(409, "device_id is already enrolled")
                active = conn.execute("SELECT 1 FROM enrollments WHERE device_id=? AND status IN ('pending','claimed','approved') AND expires_at>?", (body.device_id, stamp(instant))).fetchone()
                if active:
                    raise problem(409, "device_id already has an active enrollment")
                conn.execute("INSERT INTO enrollments(id,device_id,secret_hash,created_at,expires_at,status) VALUES(?,?,?,?,?,'pending')", (enrollment_id, body.device_id, hashlib.sha256(secret.encode()).hexdigest(), stamp(instant), stamp(expires)))
                db.audit(conn, stamp(instant), "enrollment.created", operator_id, device_id=body.device_id, enrollment_id=enrollment_id)
                conn.commit()
        except HTTPException:
            raise
        return {"enrollment_id": enrollment_id, "device_id": body.device_id, "setup_secret": secret, "expires_at": stamp(expires), "status": "pending"}

    @app.get("/v1/admin/enrollments", dependencies=[Depends(admin)])
    def list_enrollments(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
        with db.connect() as conn:
            rows = conn.execute("SELECT * FROM enrollments ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return {"enrollments": [public_enrollment(row) for row in rows]}

    @app.get("/v1/admin/enrollments/{enrollment_id}", dependencies=[Depends(admin)])
    def get_enrollment(enrollment_id: str) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM enrollments WHERE id=?", (enrollment_id,)).fetchone()
        if not row: raise problem(404, "enrollment not found")
        return public_enrollment(row)

    @app.post("/v1/enrollments/{enrollment_id}/claim")
    def claim(enrollment_id: str, body: ClaimEnrollment) -> dict[str, Any]:
        instant = now()
        try:
            management_raw = b64decode(body.management_public_key, 32, "management public key")
        except ValueError as exc:
            raise problem(422, str(exc)) from exc
        if fingerprint(management_raw) != body.management_fingerprint:
            raise problem(422, "management fingerprint does not match public key")
        try:
            csr, csr_fingerprint, tls_key_fingerprint, normalized_csr = keys.parse_csr(body.tls_csr_pem, management_raw)
        except ValueError as exc:
            raise problem(422, str(exc)) from exc
        challenge = secrets.token_urlsafe(32)
        try:
            with db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM enrollments WHERE id=?", (enrollment_id,)).fetchone()
                if not row: raise problem(404, "enrollment not found")
                if row["status"] != "pending" or row["setup_secret_used_at"] is not None: raise problem(409, "setup secret has already been used")
                if row["expires_at"] <= stamp(instant):
                    conn.execute("UPDATE enrollments SET status='expired' WHERE id=?", (enrollment_id,)); conn.commit()
                    raise problem(410, "enrollment has expired")
                if not hmac.compare_digest(row["secret_hash"], hashlib.sha256(body.setup_secret.encode()).hexdigest()):
                    raise problem(401, "setup secret is invalid")
                if conn.execute("SELECT 1 FROM devices WHERE management_fingerprint=? OR management_public_key=? OR tls_key_fingerprint=?", (body.management_fingerprint, body.management_public_key, tls_key_fingerprint)).fetchone():
                    raise problem(409, "device-management or TLS identity is already enrolled")
                if conn.execute("SELECT 1 FROM enrollments WHERE id<>? AND (management_fingerprint=? OR management_public_key=? OR tls_csr_fingerprint=? OR tls_key_fingerprint=?)", (enrollment_id, body.management_fingerprint, body.management_public_key, csr_fingerprint, tls_key_fingerprint)).fetchone():
                    raise problem(409, "device-management or TLS identity is already claimed")
                transcript = {"schema":"sage.fleet.enrollment.comparison.v1", "enrollment_id":enrollment_id, "device_id":row["device_id"], "management_fingerprint":body.management_fingerprint, "tls_csr_fingerprint":csr_fingerprint, "challenge":challenge}
                code = f"{int.from_bytes(hashlib.sha256(canonical_json(transcript)).digest()[:8], 'big') % 1_000_000:06d}"
                conn.execute("UPDATE enrollments SET status='claimed',setup_secret_used_at=?,management_public_key=?,management_fingerprint=?,tls_csr_pem=?,tls_csr_fingerprint=?,tls_key_fingerprint=?,proof_challenge=?,comparison_code=?,claimed_at=? WHERE id=?", (stamp(instant),body.management_public_key,body.management_fingerprint,normalized_csr,csr_fingerprint,tls_key_fingerprint,challenge,code,stamp(instant),enrollment_id))
                db.audit(conn, stamp(instant), "enrollment.claimed", "device", device_id=row["device_id"], enrollment_id=enrollment_id, detail={"management_fingerprint":body.management_fingerprint,"tls_csr_fingerprint":csr_fingerprint})
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise problem(409, "device identity is already in use") from exc
        return {"enrollment_id":enrollment_id,"status":"claimed","comparison_code":code,"proof_challenge":challenge,"command_authority":keys.command_authority}

    @app.post("/v1/admin/enrollments/{enrollment_id}/approve")
    def approve(enrollment_id: str, body: ApproveEnrollment, operator_id: str = Depends(admin)) -> dict[str, Any]:
        instant = now()
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE"); row = conn.execute("SELECT * FROM enrollments WHERE id=?", (enrollment_id,)).fetchone()
            if not row: raise problem(404, "enrollment not found")
            if row["status"] != "claimed": raise problem(409, "enrollment is not awaiting approval")
            if row["expires_at"] <= stamp(instant): raise problem(410, "enrollment has expired")
            if not hmac.compare_digest(row["comparison_code"], body.comparison_code): raise problem(403, "comparison code does not match")
            conn.execute("UPDATE enrollments SET status='approved',approved_at=? WHERE id=?", (stamp(instant),enrollment_id))
            db.audit(conn,stamp(instant),"enrollment.approved",operator_id,device_id=row["device_id"],enrollment_id=enrollment_id)
            conn.commit()
        return {"enrollment_id":enrollment_id,"status":"approved","approved_at":stamp(instant)}

    @app.post("/v1/enrollments/{enrollment_id}/complete")
    def complete(enrollment_id: str, body: CompleteEnrollment) -> dict[str, Any]:
        instant = now()
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE"); row = conn.execute("SELECT * FROM enrollments WHERE id=?", (enrollment_id,)).fetchone()
            if not row: raise problem(404, "enrollment not found")
            if row["status"] == "issued": raise problem(409, "client certificate has already been returned")
            if row["status"] != "approved": raise problem(409, "enrollment has not been approved")
            if row["expires_at"] <= stamp(instant): raise problem(410, "enrollment has expired")
            proof = {"schema":"sage.fleet.enrollment.proof.v1","enrollment_id":enrollment_id,"device_id":row["device_id"],"management_fingerprint":row["management_fingerprint"],"tls_csr_fingerprint":row["tls_csr_fingerprint"],"challenge":row["proof_challenge"]}
            try:
                signature = b64decode(body.proof_signature,64,"proof signature")
                Ed25519PublicKey.from_public_bytes(b64decode(row["management_public_key"],32,"management public key")).verify(signature,canonical_json(proof))
            except (ValueError, InvalidSignature) as exc:
                raise problem(403,"proof of possession is invalid") from exc
            cert = keys.issue_client_certificate(row["tls_csr_pem"],row["device_id"],settings.client_cert_days)
            cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode(); cert_fingerprint = pem_certificate_fingerprint(cert)
            conn.execute("INSERT INTO devices(device_id,management_public_key,management_fingerprint,tls_key_fingerprint,certificate_fingerprint,certificate_serial,enrolled_at) VALUES(?,?,?,?,?,?,?)", (row["device_id"],row["management_public_key"],row["management_fingerprint"],row["tls_key_fingerprint"],cert_fingerprint,str(cert.serial_number),stamp(instant)))
            conn.execute("UPDATE enrollments SET status='issued',issued_at=?,certificate_fingerprint=?,tls_csr_pem=NULL,proof_challenge=NULL,secret_hash='' WHERE id=?", (stamp(instant),cert_fingerprint,enrollment_id))
            db.audit(conn,stamp(instant),"enrollment.completed","device",device_id=row["device_id"],enrollment_id=enrollment_id,detail={"certificate_fingerprint":cert_fingerprint})
            conn.commit()
        return {"device_id":row["device_id"],"status":"issued","client_certificate_pem":cert_pem,"ca_certificate_pem":keys.ca_pem,"certificate_fingerprint":cert_fingerprint,"command_authority":keys.command_authority}

    @app.post("/v1/device/observations", status_code=202)
    async def ingest_observation(request: Request, x_fleet_client_cert_verified: Annotated[str | None, Header()] = None, x_fleet_client_cert_fingerprint: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        if x_fleet_client_cert_verified != "SUCCESS" or not x_fleet_client_cert_fingerprint:
            raise problem(401,"a verified Fleet client certificate is required")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}",x_fleet_client_cert_fingerprint): raise problem(401,"client certificate fingerprint is invalid")
        try: raw = await request.json()
        except Exception as exc: raise problem(400,"request body must be JSON") from exc
        instant=now()
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE"); identity=conn.execute("SELECT * FROM devices WHERE certificate_fingerprint=?",(x_fleet_client_cert_fingerprint,)).fetchone()
            if not identity: raise problem(403,"client certificate is not enrolled")
            # Resolve exact retries before freshness validation. The stored hash
            # proves this same envelope already passed all checks, allowing a
            # device to recover an acknowledgement even after its TTL elapses.
            candidate = raw.get("observation") if isinstance(raw, dict) else None
            existing = None
            envelope_hash = None
            if (
                isinstance(candidate, dict)
                and candidate.get("device_id") == identity["device_id"]
                and isinstance(candidate.get("sequence"), int)
                and not isinstance(candidate.get("sequence"), bool)
            ):
                existing = conn.execute(
                    "SELECT state_hash,signature,envelope_hash,ingested_at FROM observations WHERE device_id=? AND sequence=?",
                    (identity["device_id"], candidate["sequence"]),
                ).fetchone()
                if existing:
                    try:
                        envelope_hash = hashlib.sha256(canonical_json(raw)).hexdigest()
                    except (TypeError, ValueError):
                        raise problem(409,"observation sequence was already used by a different envelope")
                    if (
                        hmac.compare_digest(existing["envelope_hash"], envelope_hash)
                        and isinstance(raw.get("signature"), str)
                        and hmac.compare_digest(existing["signature"], raw["signature"])
                        and isinstance(candidate.get("state_hash"), str)
                        and hmac.compare_digest(existing["state_hash"], candidate["state_hash"])
                    ):
                        conn.commit()
                        return {"accepted":True,"device_id":identity["device_id"],"sequence":candidate["sequence"],"state_hash":existing["state_hash"],"ingested_at":existing["ingested_at"]}
                    raise problem(409,"observation sequence was already used by a different envelope")
            try: verified=verify_observation(raw,dict(identity),instant)
            except ValueError as exc: raise problem(422,str(exc)) from exc
            envelope_hash = envelope_hash or hashlib.sha256(canonical_json(raw)).hexdigest()
            if identity["last_sequence"] is not None and verified["sequence"] <= identity["last_sequence"]: raise problem(409,"observation sequence is stale or replayed")
            ingested=stamp(instant)
            try:
                conn.execute("INSERT INTO observations(device_id,sequence,observed_at,freshness_expires_at,clock_confidence,state_json,state_hash,signature,envelope_hash,ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(verified["device_id"],verified["sequence"],verified["observed_at"],verified["freshness_expires_at"],verified["clock_confidence"],verified["state_json"],verified["state_hash"],verified["signature"],envelope_hash,ingested))
                conn.execute("UPDATE devices SET last_sequence=? WHERE device_id=?",(verified["sequence"],verified["device_id"]))
                db.audit(conn,ingested,"observation.accepted","device-certificate",device_id=verified["device_id"],detail={"sequence":verified["sequence"],"state_hash":verified["state_hash"]})
                conn.commit()
            except sqlite3.IntegrityError as exc: raise problem(409,"observation sequence is stale or replayed") from exc
        return {"accepted":True,"device_id":verified["device_id"],"sequence":verified["sequence"],"state_hash":verified["state_hash"],"ingested_at":ingested}

    @app.get("/v1/admin/observations", dependencies=[Depends(admin)])
    def list_observations(device_id: str | None = None, limit: int = Query(100,ge=1,le=500)) -> dict[str,Any]:
        query="SELECT device_id,sequence,observed_at,freshness_expires_at,clock_confidence,state_json,state_hash,ingested_at FROM observations"; params:list[Any]=[]
        if device_id is not None: query+=" WHERE device_id=?"; params.append(device_id)
        query+=" ORDER BY ingested_at DESC, id DESC LIMIT ?"; params.append(limit)
        with db.connect() as conn: rows=conn.execute(query,params).fetchall()
        return {"observations":[{**dict(row),"state":__import__('json').loads(row["state_json"])} for row in rows]}

    @app.get("/v1/admin/audit-events", dependencies=[Depends(admin)])
    def list_audit_events(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
        import json
        with db.connect() as conn:
            rows = conn.execute("SELECT occurred_at,event_type,actor,device_id,enrollment_id,detail_json FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return {"events": [{**dict(row), "detail": json.loads(row["detail_json"])} for row in rows]}

    return app
