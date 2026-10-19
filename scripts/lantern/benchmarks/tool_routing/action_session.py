"""Disabled broker contract for qualified actions, authenticated sessions/receipts.

No HTTP client, credentials, endpoint selection or runtime wiring. Authority,
durable journal, backend and receipt verifier are operator-injected adapters;
none may come from a model, transcript, recalled record or request payload.
The authority must serialize admission with revocation/lock/sleep, recheck before
each dispatch, and fence publication. A snapshot is not authentication by itself.
"""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import secrets
import threading
import time

from action_boundary import admit_current


class ActionRefused(RuntimeError):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def boundary_digest():
    sources = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
               for name in ("action_boundary.py", "action_session.py", "action_authority.py",
                            "action_owner.py", "contract.py", "literal_note.py")}
    # The issuer's authentication and revoke gate belong to this qualification
    # bundle too. An older grammar/coordinator qualification cannot admit it.
    from sage_lantern import device_lock, session_context
    sources['auth_broker'] = hashlib.sha256((Path(__file__).parents[2] /
                                          'unit-a-device-auth.py').read_bytes()).hexdigest()
    for name,module in [('device_lock',device_lock),('session_context',session_context)]:
        sources[name] = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    return fingerprint(sources)


@dataclass(frozen=True)
class AuthenticatedScope:
    # These identifiers are resolved by the trusted authority, never the plan.
    owner: str
    session: str
    epoch: str
    private_domain: str
    sage_identity: str
    sage_chain: str
    sage_endpoint_profile: str
    device: str
    consent_revision: str
    expires_at: float

    def binding(self):
        return dict(vars(self))


@dataclass(frozen=True)
class QualifiedPolicy:
    # Trusted qualification registry output, never a model boolean. An exact
    # grammar and a model-gated proposal require separate evidence profiles.
    boundary_sha256: str
    backend_profile_sha256: str
    evidence_sha256: str
    mode: str
    qualified: bool = False


@dataclass(frozen=True)
class SubmittedInstruction:
    # Authority checks this opaque application request ID and exact text hash
    # against its authenticated reviewed-submit record (including speech).
    request_id: str
    text: str


@dataclass(frozen=True)
class VerifiedReceipt:
    operation_id: str
    request_sha256: str
    scope_sha256: str
    calls_sha256: str
    result_sha256: str
    state: str
    evidence_id: str
    trust_basis: str  # signed_receipt or pinned_online_canonical_observation


@dataclass(frozen=True)
class _Plan:
    instruction: SubmittedInstruction
    scope: AuthenticatedScope
    policy: QualifiedPolicy
    calls_json: str
    operation_id: str
    deadline: float


class QualifiedActionBroker:
    def __init__(self, *, authority, backend, journal, verifier, qualification,
                 backend_profile_sha256, enabled=False, clock=time.monotonic):
        if type(enabled) is not bool:
            raise ValueError("strict_enabled_flag_required")
        self.enabled = enabled
        self.authority, self.backend, self.journal, self.verifier = authority, backend, journal, verifier
        self.qualification = qualification
        self.backend_profile = backend_profile_sha256
        self.clock = clock
        self._plans, self._revoked = {}, set()
        self._lock = threading.RLock()

    def _policy(self):
        policy = self.qualification.current()
        if (type(policy) is not QualifiedPolicy or policy.qualified is not True
                or policy.mode not in {"exact_grammar", "model_and_grammar"}
                or policy.boundary_sha256 != boundary_digest()
                or policy.backend_profile_sha256 != self.backend_profile
                or self.backend.profile_sha256() != self.backend_profile
                or any(type(s) is not str or len(s) != 64 or any(c not in "0123456789abcdef" for c in s)
                       for s in (policy.backend_profile_sha256, policy.evidence_sha256))):
            raise ActionRefused("policy_unqualified_or_changed")
        return policy

    def _check(self, scope, instruction, deadline):
        if (self.enabled is not True or type(scope) is not AuthenticatedScope or type(scope.expires_at) not in (int, float)
                or not math.isfinite(scope.expires_at) or self.clock() >= min(deadline, scope.expires_at)
                or any(type(s) is not str or not s or len(s) > 256 for key, s in scope.binding().items() if key != "expires_at")
                or instruction.request_id in self._revoked
                or self.authority.check(scope, instruction) is not True):
            raise ActionRefused("session_instruction_expired_revoked_or_changed")
        return True

    def bind(self, instruction, *, deadline, proposal=None):
        if self.enabled is not True:
            raise ActionRefused("disabled")
        if (type(instruction) is not SubmittedInstruction or type(instruction.request_id) is not str
                or not instruction.request_id or len(instruction.request_id) > 128
                or type(deadline) not in (int, float) or not math.isfinite(deadline)
                or deadline <= self.clock()):
            raise ActionRefused("submitted_instruction_and_finite_deadline_required")
        policy = self._policy()
        if policy.mode == "model_and_grammar" and proposal is None:
            raise ActionRefused("qualified_model_proposal_required")
        decision = admit_current(instruction.text, proposal=proposal,
                                 allow_exact_grammar=policy.mode == "exact_grammar")
        if not decision["calls"]:
            raise ActionRefused(decision["reason"])
        scope = self.authority.resolve(instruction)
        self._check(scope, instruction, deadline)
        calls_json = json.dumps(decision["calls"], sort_keys=True, separators=(",", ":"))
        operation_id = fingerprint({"request_id": instruction.request_id, "instruction_sha256": decision["instruction_sha256"],
                                    "scope": scope.binding(), "calls": decision["calls"]})
        with self._lock:
            handle = secrets.token_hex(32)
            self._plans[handle] = _Plan(instruction, scope, policy, calls_json, operation_id, min(deadline, scope.expires_at))
        return handle

    def cancel(self, request_id):
        primitive = getattr(self.authority, "cancel", None)
        if callable(primitive):
            primitive(request_id)
        with self._lock:
            self._revoked.add(request_id)

    def execute(self, handle, *, publish=None):
        if self.enabled is not True:
            raise ActionRefused("disabled")
        if publish is not None and not callable(publish):
            raise ActionRefused("trusted_publication_sink_required")
        with self._lock:
            plan = self._plans.pop(handle, None)
        if plan is None:
            raise ActionRefused("plan_unknown_consumed_or_replayed")
        # No broker mutex is held during backend work: cancel must revoke an
        # in-flight request promptly. The authority owns lifecycle serialization.
        if plan is not None:
            self._check(plan.scope, plan.instruction, plan.deadline)
            if self._policy() != plan.policy:
                raise ActionRefused("qualification_changed")
            calls = json.loads(plan.calls_json)
            scope_sha = fingerprint(plan.scope.binding())
            calls_sha = fingerprint(calls)
            request_sha = hashlib.sha256(plan.instruction.text.encode()).hexdigest()
            # Reserve durably BEFORE any backend work. Never recycle an unknown
            # reservation. Rebinding the same submitted request cannot retry.
            reservation = fingerprint({"owner": plan.scope.owner, "request_id": plan.instruction.request_id})
            if self.journal.reserve(reservation, fingerprint({"operation_id": plan.operation_id, "scope": scope_sha,
                                                            "calls": calls_sha, "request": request_sha})) is not True:
                raise ActionRefused("operation_already_reserved")

            def plan_check():
                self._check(plan.scope, plan.instruction, plan.deadline)
                if self._policy() != plan.policy:
                    raise ActionRefused("qualification_changed")
                return True

            authority = self.authority
            broker_gate = self._lock

            class GuardedAccess:
                def __call__(self):
                    return plan_check()

                def dispatch_step(self, callback):
                    primitive = getattr(authority, "dispatch_step", None)
                    if not callable(primitive):
                        raise ActionRefused("serialized_dispatch_authority_required")

                    def step():
                        with broker_gate:
                            plan_check()
                            value = callback()
                            plan_check()
                            return value

                    return primitive(plan.scope, plan.instruction, step)

            guarded_access = GuardedAccess()

            def dispatch():
                plan_check()
                # The backend MUST recheck authority between each parallel read
                # and enforce this deadline/cancellation at the transport too.
                raw = self.backend.invoke(calls, scope=plan.scope, operation_id=plan.operation_id,
                                          deadline=plan.deadline,
                                          access_check=guarded_access)
                plan_check()
                receipt = self.verifier.verify(raw, scope=plan.scope, calls=calls,
                                               operation_id=plan.operation_id, request_sha256=request_sha)
                plan_check()
                required = "committed" if calls[0]["name"] == "save_private_note" else "read_complete"
                if (type(receipt) is not VerifiedReceipt or receipt.operation_id != plan.operation_id
                        or receipt.request_sha256 != request_sha or receipt.scope_sha256 != scope_sha
                        or receipt.calls_sha256 != calls_sha or receipt.result_sha256 != fingerprint(raw)
                        or receipt.state != required or type(receipt.evidence_id) is not str or not receipt.evidence_id
                        or receipt.trust_basis not in {"signed_receipt", "pinned_online_canonical_observation"}):
                    raise ActionRefused("receipt_unverified_wrong_scope_content_or_state")
                self.journal.complete(reservation, receipt)
                self._check(plan.scope, plan.instruction, plan.deadline)
                if self._policy() != plan.policy:
                    raise ActionRefused("qualification_changed")
                result = {"state": "verified", "operation_id": plan.operation_id, "receipt": receipt,
                        "result": raw, "completion_text": "Your private note was saved." if required == "committed" else "The requested read completed."}
                if publish is not None:
                    primitive = getattr(authority, "publish", None)
                    if not callable(primitive):
                        raise ActionRefused("serialized_publication_authority_required")

                    def deliver():
                        with broker_gate:
                            plan_check()
                            publish(result)
                            plan_check()

                    primitive(plan.scope, plan.instruction, deliver)
                return result

            # This is a REQUIRED authority primitive, not a check-then-call.
            # Its trusted implementation serializes lock/epoch revocation with
            # dispatch/publication and drains in-flight work before teardown.
            return self.authority.run_guarded(plan.scope, plan.instruction, dispatch)
