from dataclasses import replace

import pytest
from sage_sdk import AgentIdentity

from sage_lantern.device_profile import DeviceProfile
from sage_lantern.mesh_contacts import verify_contact_card
from sage_lantern import mesh_contact_issuance as module


def fixture():
    identity = AgentIdentity.from_seed(bytes(range(32)))
    radio = AgentIdentity.from_seed(bytes(range(32, 64)))
    profile = DeviceProfile(configured=True, display_name='Lantern owner', revision=1)
    binding = dict(broker_id='b' * 32, epoch='e' * 32, config_sha256='c' * 64,
        chain_id='private-local', agent_id=identity.agent_id, radio_public_key=radio.agent_id)
    plan = module.preview(profile, binding, previous_revision=4, now=1000, ttl_seconds=600,
        findable=True, messaging_allowed=True)
    return identity, profile, binding, plan


@pytest.mark.parametrize('fault', [None, 'consent', 'digest', 'profile', 'broker', 'epoch',
    'policy', 'expired_preview', 'locked', 'cas', 'revoked_after_reservation', 'numeric_consent'])
def test_exact_preview_live_authority_and_single_reservation(fault):
    identity, profile, binding, plan = fixture()
    approval = dict(consent=True, plan_sha256=module.approval_digest(plan))
    current = dict(profile=profile, binding=binding, active=True, now=1000)
    calls = []
    if fault == 'consent': approval['consent'] = False
    if fault == 'numeric_consent': approval['consent'] = 1
    if fault == 'digest': approval['plan_sha256'] = '0' * 64
    if fault == 'profile': current['profile'] = replace(profile, revision=2)
    if fault in ('broker', 'epoch', 'policy'):
        key = {'broker': 'broker_id', 'epoch': 'epoch', 'policy': 'config_sha256'}[fault]
        current['binding'] = {**binding, key: 'd' * len(binding[key])}
    if fault == 'expired_preview': current['now'] = 1061
    if fault == 'locked': current['active'] = False
    def reserve(previous, following, digest):
        calls.append((previous, following, digest))
        if fault == 'revoked_after_reservation': current['active'] = False
        return fault != 'cas'
    def run():
        return module.issue(plan, approval, identity=identity, read_profile=lambda: current['profile'],
            read_binding=lambda: current['binding'], access_check=lambda: current['active'],
            reserve_revision=reserve, clock=lambda: current['now'])
    if fault:
        with pytest.raises(module.ContactIssuanceUnavailable): run()
        assert len(calls) == (1 if fault in ('cas', 'revoked_after_reservation') else 0)
    else:
        card = verify_contact_card(run(), now=1000)
        assert card.revision == 5 and card.findable and card.messaging_allowed
        assert len(calls) == 1 and profile.public_discoverable is False


def test_false_consent_card_revokes_without_changing_profile_defaults():
    identity, profile, binding, old = fixture()
    plan = module.preview(profile, binding, previous_revision=5, now=1000, ttl_seconds=600,
        findable=False, messaging_allowed=False)
    card = module.issue(plan, dict(consent=True, plan_sha256=module.approval_digest(plan)),
        identity=identity, read_profile=lambda: profile, read_binding=lambda: binding,
        access_check=lambda: True, reserve_revision=lambda *args: True, clock=lambda: 1000)
    verified = verify_contact_card(card, now=1000)
    assert verified.revision == 6 and not verified.findable and not verified.messaging_allowed


@pytest.mark.parametrize('public', [False, True])
@pytest.mark.parametrize('changed', [False, True])
def test_private_pairing_preserves_both_public_preferences_and_binds_toggle(public, changed):
    identity, profile, binding, old = fixture()
    profile = replace(profile, public_discoverable=public)
    plan = module.preview(profile, binding, previous_revision=4, now=1000, ttl_seconds=600,
        findable=False, messaging_allowed=False)
    current = replace(profile, public_discoverable=not public, revision=2) if changed else profile
    calls = []
    def run():
        return module.issue(plan, dict(consent=True, plan_sha256=module.approval_digest(plan)),
            identity=identity, read_profile=lambda: current, read_binding=lambda: binding,
            access_check=lambda: True, reserve_revision=lambda *args: calls.append(args) or True, clock=lambda: 1000)
    if changed:
        with pytest.raises(module.ContactIssuanceUnavailable): run()
        assert calls == []
    else:
        assert verify_contact_card(run(), now=1000).messaging_allowed is False
        assert profile.public_discoverable is public and len(calls) == 1


def test_reserved_revision_cannot_be_reissued_and_post_sign_revocation_returns_no_card(monkeypatch):
    identity, profile, binding, plan = fixture()
    state = {'revision': 4, 'active': True, 'signed': 0}
    original = module.sign_contact_card
    def sign(*args, **kwargs):
        state['signed'] += 1
        card = original(*args, **kwargs)
        state['active'] = False
        return card
    monkeypatch.setattr(module, 'sign_contact_card', sign)
    def reserve(previous, following, digest):
        if state['revision'] != previous:
            return False
        state['revision'] = following
        return True
    def run():
        return module.issue(plan, dict(consent=True, plan_sha256=module.approval_digest(plan)),
            identity=identity, read_profile=lambda: profile, read_binding=lambda: binding,
            access_check=lambda: state['active'], reserve_revision=reserve, clock=lambda: 1000)
    with pytest.raises(module.ContactIssuanceUnavailable): run()
    assert state['revision'] == 5 and state['signed'] == 1
    state['active'] = True
    with pytest.raises(module.ContactIssuanceUnavailable): run()
    assert state['revision'] == 5 and state['signed'] == 1


@pytest.mark.parametrize('field,value', [('ttl_seconds', 3601), ('ttl_seconds', True),
    ('findable', 1), ('messaging_allowed', None), ('previous_revision', True)])
def test_no_implicit_consent_or_unbounded_card_lifetime(field, value):
    identity, profile, binding, old = fixture()
    arguments = dict(previous_revision=0, now=1000, ttl_seconds=600, findable=False, messaging_allowed=False)
    arguments[field] = value
    with pytest.raises(module.ContactIssuanceUnavailable): module.preview(profile, binding, **arguments)
