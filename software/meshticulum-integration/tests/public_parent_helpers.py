# SPDX-License-Identifier: Apache-2.0
"""Explicit synthetic controller/native enrollment for source fixtures only.

The local approval and prior-debt observers below deliberately model a trusted
fixture owner. They do not establish real host consent, observed prior debt or
physical native qualification. No device, controller process or driver opens.
"""
from copy import deepcopy
import base64
from pathlib import Path

from meshticulum.contracts.identity import PeerID
from lantern_meshticulum.bench.pilot_parent_caps import Cost, UnitCaps, ParentPolicy, ParentCaps
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps, NativeFacts


LIMIT = Cost(1024, 240000000, 8192, 8388608, 8388608)


def paired(base, payload):
    """Supply the other exact endpoint; the payload is a declared public fixture."""
    other = deepcopy(base)
    other['unit'] = 'unit-b' if base['unit'] == 'unit-a' else 'unit-a'
    plan = other['grant']
    if base['transport'] == 'tcp':
        peers = plan['session']
        sends = base['grant']['local_peer'] == peers['initiator']
        plan['local_peer'] = peers['responder' if sends else 'initiator']
    else:
        sends = base['grant']['sender_unit'] == base['unit']
        plan['unit'] = other['unit']
        plan['local_peer'] = plan['responder' if sends else 'initiator']
    fmt = plan['format'] if base['transport'] == 'tcp' else plan['service']['format']
    if sends:
        other['payload_base64'] = other['source_run_id'] = None
    elif fmt == 'MR1.request':
        other['payload_base64'] = base64.b64encode(payload).decode()
        other['source_run_id'] = None
    else:
        other['payload_base64'] = None
        other['source_run_id'] = 'd' * 32
    return [base, other]


def qualify(packages, states, clock):
    """Pay both original maxima and separately enroll each requested local unit."""
    packages = [deepcopy(value) for value in packages]
    first = packages[0]
    plan = first['grant']
    peers = plan['session'] if first['transport'] == 'tcp' else plan
    sender = first['unit'] if plan['local_peer'] == peers['initiator'] else packages[1]['unit']
    receiver = next(value['unit'] for value in packages if value['unit'] != sender)
    mapped = {sender: PeerID(**peers['initiator']), receiver: PeerID(**peers['responder'])}
    epoch, mono = clock()
    policy = ParentPolicy('1' * 32, first['grant']['host_boot_id'],
        min(value['grant']['issued_epoch_us'] for value in packages) - 1,
        max(value['grant']['expires_epoch_us'] for value in packages),
        mono - 1, mono + max(value['grant']['expires_epoch_us'] for value in packages) - epoch,
        tuple(UnitCaps(unit, first['grant']['host_boot_id'], mapped[unit], LIMIT, Cost(), 'a' * 64)
              for unit in sorted(mapped)), LIMIT.plus(LIMIT), 'b' * 64)
    # The finite controller ledger is explicit and separate from native state.
    owner_state = Path(next(iter(states.values())))
    control = owner_state / 'fixture-controller'
    control.mkdir(mode=0o700)
    controller = ParentCaps.enroll(control/'caps.sqlite', policy, clock=clock,
                                  boot_reader=lambda: policy.controller_boot_id)
    try:
        permit = controller.prepay_pair(packages)
        children = controller.consume(permit)
        paid = {child.unit: child.to_dict() for child in children}
        allocation = permit.key
    finally:
        controller.close()
    for unit, state in states.items():
        parent_dir = Path(state)/'parents'/policy.parent_id
        parent_dir.parent.mkdir(mode=0o700)
        parent_dir.mkdir(mode=0o700)
        native = NativeCaps.enroll(parent_dir/'native.sqlite', policy, unit=unit, clock=clock,
            facts=lambda unit=unit: NativeFacts(unit, policy.unit(unit).host_boot_id, policy.unit(unit).peer),
            approve=lambda binding: True, prior_check=lambda prior: True)
        assert native.close()
    for value in packages:
        value['schema'] = 'meshticulum.public-pilot-package.v2'
        value['parent'] = dict(schema='meshticulum.public-parent-allocation.v1',
            parent_id=policy.parent_id, policy_digest=policy.digest, allocation_id=allocation,
            child=paid[value['unit']])
    return packages, policy


def authorize(base, state, clock, payload):
    """Single-host preparation fixture with an explicitly paid endpoint pair."""
    values, _ = qualify(paired(base, payload), {base['unit']: state}, clock)
    return values[0]
