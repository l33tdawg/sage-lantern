"""Disabled owner/consent issuer bound to the actual in-process AuthBroker.

No production import, HTTP route, credentials, key provisioning or node-policy
verification. Trusted construction pins installation owner/node/signer policy;
the human consent handler must remain separate from model tools. Context alone
never grants access. Every check/send/publication shares the broker's lock gate.
"""
from dataclasses import dataclass
import math
import re
import secrets
import threading
import time

from sage_lantern.session_context import SessionContext
from action_authority import PRIVATE_ACTIONS, ReviewedActionAuthority
from action_session import ActionRefused, AuthenticatedScope


def require(value, code='owner_binding_unavailable'):
    if not value:
        raise ActionRefused(code)


@dataclass(frozen=True)
class ProvisionedOwner:
    """Operator-pinned installation reference, not a display-name/person claim.

    Caller-supplied model/request values are never accepted for these fields.
    The live verifier must check the exact installed policy and grant state.
    """
    owner_ref: str
    device: str
    identity: str
    domain: str
    chain: str
    endpoint_profile: str
    policy_sha256: str

    def __post_init__(self):
        for value in (self.owner_ref, self.device, self.domain, self.chain, self.endpoint_profile):
            require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value))
        for value in (self.identity, self.policy_sha256):
            require(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value))
        require(self.domain not in {'general','self','meta'} and not self.domain.startswith(('sage-','scope:')))


@dataclass(frozen=True)
class ConsentChallenge:
    challenge_id: str
    context: SessionContext
    actions: frozenset
    expires_at: float


class BrokerReviewedActionAuthority(ReviewedActionAuthority):
    def __init__(self, auth_broker, binding, *, node_policy_check, enabled=False,
                 clock=time.monotonic, capacity=256, lifetime=30):
        require(type(binding) is ProvisionedOwner and callable(node_policy_check)
                and type(lifetime) in (int,float) and math.isfinite(lifetime) and 0 < lifetime <= 30)
        require(callable(getattr(auth_broker,'session_context',None))
                and callable(getattr(auth_broker,'subscribe_revocation',None)))
        super().__init__(enabled=enabled,clock=clock,capacity=capacity)
        self._auth, self._binding, self._node_check = auth_broker, binding, node_policy_check
        # One gate owns PIN-token revocation and every action dispatch/publication.
        self._gate = threading.Condition(auth_broker.gate)
        self._lifetime = lifetime
        self._token = self._context = self._challenge = None
        self._consents_issued = 0
        auth_broker.subscribe_revocation(self.revoke,drained=self.drained)

    def activate(self, *args, **kwargs):
        # This issuer cannot inherit the synthetic scope injection seam.
        raise ActionRefused('authenticated_consent_handler_required')

    def _current(self, token, context):
        require(type(context) is SessionContext)
        try:
            current = SessionContext.parse(self._auth.session_context(token))
            private = self._node_check(self._binding)
        except Exception:
            raise ActionRefused('owner_session_or_node_policy_unavailable') from None
        require(current == context and private is True,
                'owner_session_or_node_policy_changed')
        return current

    def _enabled(self):
        require(self.enabled is True and not self._closed and not self._drain_failed,
                'owner_issuer_disabled_or_closed')

    def prepare_consent(self, token, context, *, actions):
        """Trusted application consent-screen entry; no private SDK work.

        This must display these exact actions, scoped to this current context.
        Opening the screen invalidates previous consent, not the device token.
        """
        require(type(actions) is frozenset and actions and actions <= PRIVATE_ACTIONS,
                'unsupported_private_consent')
        with self._gate:
            self._enabled()
            self._current(token,context)
            self.revoke()
            require(self._consents_issued < self.capacity, 'consent_capacity_exhausted')
            self._consents_issued += 1
            challenge = ConsentChallenge(secrets.token_hex(32),context,actions,
                                         self.clock() + self._lifetime)
            self._challenge = challenge
            return challenge

    def accept_consent(self, token, context, *, challenge_id, reviewed_actions, accepted):
        """Trusted human handler; request/model flags cannot call this seam.

        A consumed, changed, expired or unapproved challenge cannot authorize.
        Existing history/public-share settings never supply this consent.
        """
        with self._gate:
            self._enabled()
            challenge, self._challenge = self._challenge, None
            require(type(accepted) is bool and accepted is True
                    and type(reviewed_actions) is frozenset
                    and challenge is not None and type(challenge_id) is str
                    and secrets.compare_digest(challenge.challenge_id,challenge_id)
                    and context == challenge.context and reviewed_actions == challenge.actions
                    and self.clock() < challenge.expires_at,
                    'consent_changed_expired_consumed_or_refused')
            self._current(token,context)
            b = self._binding
            scope = AuthenticatedScope(b.owner_ref,context.session_ref,context.epoch,b.domain,
                b.identity,b.chain,b.endpoint_profile,b.device,challenge.challenge_id,
                min(challenge.expires_at,self.clock()+self._lifetime))
            # Direct call intentionally bypasses this subclass's forbidden
            # injected-scope seam only after all authenticated consent checks.
            super().activate(scope,allowed_actions=challenge.actions)
            self._token, self._context = token, context
            return scope

    def _live(self):
        super()._live()
        try:
            self._current(self._token,self._context)
        except ActionRefused:
            self.revoke()
            raise

    def revoke(self):
        with self._gate:
            self._token = self._context = self._challenge = None
            super().revoke()

    def revoke_and_drain(self, timeout=2):
        """Trusted teardown before lock/sleep/service ownership can be released."""
        self.revoke()
        return self.drain(timeout)

    def drained(self):
        """Bounded nonblocking probe for the broker's own shutdown receipt."""
        with self._gate:
            return self._scope is None and not self._flight and not self._drain_failed

    def public_binding(self):
        """Configuration reference only; never consent or a live scope."""
        return dict(vars(self._binding))
