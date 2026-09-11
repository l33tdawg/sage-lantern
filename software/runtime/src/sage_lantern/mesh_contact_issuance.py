"""Owner-approved card issuance core; no key loading, transport or renewal loop.

Root integration must inject live binding/profile readers and an exclusive,
durable CAS revision reservation. Reservation is never rolled back or retried.
An exception after reservation may have consumed a revision; no card is returned.
"""

import hashlib
import json
import re

from .device_profile import DeviceProfile, normalize_display_name
from .mesh_contacts import MAX_INTEGER, MAX_TTL_SECONDS, sign_contact_card
from .message_contacts import RecipientID


class ContactIssuanceUnavailable(ValueError):
    pass


def require(value):
    if value is not True:
        raise ContactIssuanceUnavailable('contact_issuance_unavailable')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def preview(profile, binding, *, previous_revision, now, ttl_seconds, findable, messaging_allowed):
    require(type(profile) is DeviceProfile and profile.configured is True
        and type(profile.public_discoverable) is bool and type(profile.revision) is int and profile.revision > 0
        and normalize_display_name(profile.display_name) == profile.display_name)
    require(type(binding) is dict and set(binding) == {'broker_id', 'epoch', 'config_sha256',
        'chain_id', 'agent_id', 'radio_public_key'})
    for name, length in (('broker_id', 32), ('epoch', 32), ('config_sha256', 64),
            ('agent_id', 64), ('radio_public_key', 64)):
        require(type(binding[name]) is str and re.fullmatch('[0-9a-f]{' + str(length) + '}', binding[name]) is not None)
    RecipientID(binding['chain_id'], binding['agent_id'])
    require(binding['agent_id'] != binding['radio_public_key'])
    require(type(previous_revision) is int and 0 <= previous_revision < MAX_INTEGER
        and type(now) is int and 0 < now <= MAX_INTEGER - MAX_TTL_SECONDS
        and type(ttl_seconds) is int and 1 <= ttl_seconds <= MAX_TTL_SECONDS
        and type(findable) is bool and type(messaging_allowed) is bool)
    return dict(schema='sage.lantern.owner-contact-issuance.v1', binding=dict(binding),
        profile_revision=profile.revision, public_discoverable=profile.public_discoverable, display_name=profile.display_name,
        previous_revision=previous_revision, revision=previous_revision + 1,
        issued_at=now, expires_at=now + ttl_seconds, findable=findable,
        messaging_allowed=messaging_allowed)


def approval_digest(plan):
    return hashlib.sha256(canonical(plan)).hexdigest()


def issue(plan, approval, *, identity, read_profile, read_binding, access_check, reserve_revision, clock):
    plan = json.loads(canonical(plan))
    require(type(approval) is dict and set(approval) == {'consent', 'plan_sha256'}
        and approval['consent'] is True and approval['plan_sha256'] == approval_digest(plan))
    def check():
        require(access_check() is True)
        now = clock()
        require(type(now) is int and plan['issued_at'] <= now < plan['expires_at']
            and now - plan['issued_at'] <= 60)
        current = preview(read_profile(), read_binding(), previous_revision=plan['previous_revision'],
            now=plan['issued_at'], ttl_seconds=plan['expires_at'] - plan['issued_at'],
            findable=plan['findable'], messaging_allowed=plan['messaging_allowed'])
        require(current == plan and identity.agent_id == plan['binding']['agent_id'])
        require(access_check() is True)
        return now
    check()
    require(reserve_revision(plan['previous_revision'], plan['revision'], approval_digest(plan)) is True)
    now = check()
    card = sign_contact_card(identity, chain_id=plan['binding']['chain_id'],
        radio_public_key=plan['binding']['radio_public_key'], display_name=plan['display_name'],
        revision=plan['revision'], issued_at=plan['issued_at'], expires_at=plan['expires_at'],
        now=now, findable=plan['findable'], messaging_allowed=plan['messaging_allowed'])
    check()
    return card
