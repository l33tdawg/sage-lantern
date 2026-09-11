from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import __version__
from .management import DeviceManagementIdentity, Observation, SignedObservation
from .device_agent import DeviceObservationAgent, ObservationOutbox, SanitizedJSONStatusCollector
from .management_enrollment import EnrollmentClient, PendingEnrollment, TLSIdentity, load_enrolled_config
from .management_transport import MTLSObservationClient, MTLSObservationConfig


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="sage-lantern-device-agent",
        description="Dedicated Lantern device-management identity and signed observation tool",
    )
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)

    identity = commands.add_parser("init-identity", help="create or inspect the dedicated management identity")
    identity.add_argument("--identity-root", type=Path, required=True)

    observe = commands.add_parser("observe", help="sign one freshness-bounded device observation")
    observe.add_argument("--identity-root", type=Path, required=True)
    observe.add_argument("--device-id", required=True)
    observe.add_argument("--sequence", type=int, required=True)
    observe.add_argument("--state", type=Path, required=True, help="sanitized JSON object to sign")
    observe.add_argument("--freshness-seconds", type=int, default=300)
    observe.add_argument("--clock-confidence", default="unknown")

    claim = commands.add_parser("enroll-claim", help="claim a single-use Fleet enrollment and display its comparison code")
    claim.add_argument("--state-root", type=Path, required=True)
    claim.add_argument("--authority-url", required=True)
    claim.add_argument("--enrollment-id", required=True)
    claim.add_argument("--device-id", required=True, help="expected device ID assigned by Fleet Admin")
    claim.add_argument("--setup-secret-file", type=Path, required=True, help="owner-readable file; secret is never persisted")
    claim.add_argument("--bootstrap-ca", type=Path)

    complete = commands.add_parser("enroll-complete", help="prove key possession after operator approval and install credentials")
    complete.add_argument("--state-root", type=Path, required=True)
    complete.add_argument("--bootstrap-ca", type=Path)

    run = commands.add_parser("run", help="run the outbound-only signed observation agent")
    run.add_argument("--state-root", type=Path, required=True)
    run.add_argument("--status-file", type=Path, required=True, help="allow-listed sanitized local JSON status")
    run.add_argument("--interval-seconds", type=float, default=60)
    run.add_argument("--freshness-seconds", type=int, default=300)
    run.add_argument("--clock-confidence", default="unknown")
    run.add_argument("--once", action="store_true")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "init-identity":
            identity = DeviceManagementIdentity.load_or_create(args.identity_root)
            print(json.dumps({"fingerprint": identity.fingerprint, "public_key": identity.public_key}, sort_keys=True))
            return 0
        if args.command == "observe":
            identity = DeviceManagementIdentity.load_or_create(args.identity_root)
            state = json.loads(args.state.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("observation state must be a JSON object")
            observation = Observation.create(
                device_id=args.device_id,
                sequence=args.sequence,
                observed_at=datetime.now(UTC),
                freshness_ttl=timedelta(seconds=args.freshness_seconds),
                clock_confidence=args.clock_confidence,
                source_fingerprint=identity.fingerprint,
                state=state,
            )
            print(SignedObservation.create(observation, identity).to_json())
            return 0
        if args.command == "enroll-claim":
            identity = DeviceManagementIdentity.load_or_create(args.state_root / "management-identity")
            tls_identity = TLSIdentity.load_or_create(args.state_root, identity.fingerprint)
            if args.setup_secret_file.stat().st_mode & 0o077:
                raise ValueError("setup secret file must be owner-only")
            setup_secret = args.setup_secret_file.read_text(encoding="utf-8").strip()
            client = EnrollmentClient(
                authority_url=args.authority_url,
                state_root=args.state_root,
                bootstrap_ca=args.bootstrap_ca,
            )
            pending = client.claim(
                enrollment_id=args.enrollment_id,
                setup_secret=setup_secret,
                expected_device_id=args.device_id,
                identity=identity,
                tls_identity=tls_identity,
            )
            print(json.dumps({
                "enrollment_id": pending.enrollment_id,
                "status": "claimed",
                "comparison_code": pending.comparison_code,
                "command_authority_fingerprint": pending.command_authority["fingerprint"],
                "next_step": "Confirm this comparison code in Fleet Admin, then run enroll-complete.",
            }, sort_keys=True))
            return 0
        if args.command == "enroll-complete":
            pending_value = json.loads((args.state_root / "enrollment-pending.json").read_text(encoding="utf-8"))
            pending = PendingEnrollment(**pending_value)
            identity = DeviceManagementIdentity.load_or_create(args.state_root / "management-identity")
            tls_identity = TLSIdentity.load_or_create(args.state_root, identity.fingerprint)
            config = EnrollmentClient(
                authority_url=pending.authority_url,
                state_root=args.state_root,
                bootstrap_ca=args.bootstrap_ca,
            ).complete(identity=identity, tls_identity=tls_identity)
            print(json.dumps({
                "device_id": config.device_id,
                "status": "issued",
                "certificate_fingerprint": config.certificate_fingerprint,
                "command_authority_fingerprint": config.command_authority_fingerprint,
                "observation_endpoint": config.observation_endpoint,
            }, sort_keys=True))
            return 0
        if args.command == "run":
            config, _command_authority = load_enrolled_config(args.state_root)
            identity = DeviceManagementIdentity.load_or_create(args.state_root / "management-identity")
            outbox = ObservationOutbox(args.state_root / "observations.sqlite3")
            try:
                transport = MTLSObservationClient(MTLSObservationConfig(
                    endpoint=config.observation_endpoint,
                    # Public fleet-api TLS is verified with the operating
                    # system trust store. The saved Fleet CA signs this
                    # device's client certificate, not the HTTPS server.
                    ca_certificate=None,
                    client_certificate=Path(config.client_certificate),
                    client_private_key=args.state_root / "management-tls.key",
                ))
                agent = DeviceObservationAgent(
                    device_id=config.device_id,
                    identity=identity,
                    collector=SanitizedJSONStatusCollector(args.status_file),
                    outbox=outbox,
                    client=transport,
                    freshness_ttl=timedelta(seconds=args.freshness_seconds),
                    clock_confidence=args.clock_confidence,
                )
                if args.once:
                    sent = agent.run_once()
                    print(json.dumps({"sent": sent, "last_sequence": outbox.last_sequence}, sort_keys=True))
                    return 0
                agent.run_forever(args.interval_seconds)
            finally:
                outbox.close()
    except Exception as exc:
        print(f"sage-lantern-device-agent: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
