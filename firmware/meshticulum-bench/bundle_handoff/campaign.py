"""Source-only alpha.3 workload/cost planning; no device or scope execution.

The current owner's install/test instruction is the authority context. This
module creates neither a lease nor a grant and does not copy expired v3 limits.
Every physical operation still needs current endpoint facts and finite caps.
"""
from copy import deepcopy
import hashlib
import json
import math


def need(value, reason):
    if not value:
        raise ValueError(reason)


def integer(value, minimum=0):
    need(type(value) is int and minimum <= value <= 2**63 - 1, 'invalid_integer')
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    need(type(value) is str and len(value) == 64
         and all(c in '0123456789abcdef' for c in value), 'invalid_digest')
    return value


def sequence(*, pairs=2, sender='unit-b', receiver='unit-a'):
    """An ordered menu, not permission to auto-run every item after a failure."""
    integer(pairs, 1)
    need(pairs <= 8 and sender != receiver
         and {sender, receiver} <= {'unit-a', 'unit-b', 'unit-c'}, 'invalid_pair_plan')
    runs = []
    def add(label, mode, useful_bytes, profile=False, **extra):
        runs.append(dict(index=len(runs), label=label, mode=mode, useful_bytes=useful_bytes,
            sender=sender, receiver=receiver, profile_host=profile, **extra))
    for pair in range(pairs):
        # Reverse the historical baseline-first run, then counterbalance order.
        for mode in (('queued', 'baseline') if pair % 2 == 0 else ('baseline', 'queued')):
            add('paired-' + str(pair), mode, 8164, True, comparison_pair=pair)
    add('larger-file', 'queued', 100000, True)
    return dict(schema='meshticulum.alpha3-workload-menu.v1', source_only=True,
        automatic_progression=False, scopes_issued=0, runs=runs,
        separate_recovery_cases=[
            dict(case='one_authenticated_data_drop', useful_bytes=8164,
                 max_injections=1, source_adapter_review_required=True),
            dict(case='one_final_ack_drop', useful_bytes=8164,
                 max_injections=1, source_adapter_review_required=True),
            dict(case='host_disconnect_or_cancel', useful_bytes=8164,
                 max_injections=1, expect_unknown_or_not_sent=True,
                 successor_requires_accounted_terminal_state=True),
        ], limits=['Profiled and unprofiled rates are separate distributions.',
            'Verified-piece continuation is not in-flight crash recovery.',
            'Recovery cases require exact injection/cleanup source before execution.'])


def airtime_us(size):
    """SF5/BW500/CR4:5/preamble12 estimate; not measured airtime."""
    integer(size, 1)
    need(size <= 255, 'frame_too_large')
    coded = (8 * size + 36 - 20 + 19) // 20
    return (105 + 20 * coded) * 16


def reservation_us(size):
    # Match the SDK worker's actual floating-point ceil. Integer 11/10 can
    # underestimate this by one microsecond (for example a 70-byte SF5 ACK).
    return math.ceil(airtime_us(size) * 1.10) + 1000


def costs(wire_sizes):
    """Keep worker/native cost and frozen scheduler double charging separate."""
    need(type(wire_sizes) is list and wire_sizes, 'frame_sizes_required')
    worker = native = debt_own = debt_shared = 0
    peak_own = peak_shared = 1
    own, shared = reservation_us(255), reservation_us(255) + reservation_us(70)
    for size in wire_sizes:
        peak_own = max(peak_own, worker + debt_own + own + 1)
        peak_shared = max(peak_shared, worker + debt_shared + shared + 1)
        worker += reservation_us(size)
        native += airtime_us(size)
        debt_own += own
        debt_shared += shared
        peak_own = max(peak_own, worker + debt_own + 1)
        peak_shared = max(peak_shared, worker + debt_shared + 1)
    return dict(attempts=len(wire_sizes), host_reserved_us=worker,
        native_estimated_us=native, frozen_minimum_own_us=peak_own,
        frozen_minimum_shared_us=peak_shared)


def part_cost(response_bytes, *, extra_data_attempts=8, receiver_ack_allowance=64):
    integer(response_bytes, 1)
    integer(extra_data_attempts)
    integer(receiver_ack_allowance, 1)
    need(response_bytes <= 32768 and extra_data_attempts <= 64
         and receiver_ack_allowance <= 512, 'invalid_part_bounds')
    full, tail = divmod(response_bytes, 193)
    frames = [255] * full + ([tail + 62] if tail else [])
    data_count = len(frames)
    frames += [255] * extra_data_attempts + [79]  # authenticated receipt confirmation
    return dict(response_bytes=response_bytes, data_frames=data_count,
        max_frames=max(64, len(frames), receiver_ack_allowance),
        sender=costs(frames), receiver=costs([70] * receiver_ack_allowance),
        extra_data_attempts=extra_data_attempts,
        receiver_ack_allowance=receiver_ack_allowance,
        source_estimates_only=True)


def admission_check(parts, *, sender, receiver, native, campaign_remaining,
                    operation_caps, max_boot_attempts, max_boot_airtime_us,
                    max_frames, own_limit_us, shared_limit_us):
    """Pure conservative fit check; native absolutes never restart at zero.

    campaign_remaining is the new finite allocation minus all its paid spend;
    historical records are retained separately, never converted into refunds.
    Journal limits are per part. Native and campaign headroom cover every part.
    """
    need(type(parts) is list and parts and sender != receiver, 'invalid_parts')
    integer(max_boot_attempts, 1); integer(max_boot_airtime_us, 1)
    integer(max_frames, 1); integer(own_limit_us, 1); integer(shared_limit_us, 1)
    need(max_boot_attempts <= 1024 and max_boot_airtime_us <= 300000000
         and max_frames <= 512, 'sdk_absolute_ceiling')
    totals = {}
    for role, unit in (('sender', sender), ('receiver', receiver)):
        need(unit in native and unit in campaign_remaining and unit in operation_caps,
             'endpoint_budget_missing')
        for values in (native[unit], campaign_remaining[unit], operation_caps[unit]):
            need(set(values) == {'attempts', 'estimated_airtime_us'}, 'exact_budget_fields')
            for value in values.values(): integer(value)
        role_parts = [p[role] for p in parts]
        used = dict(attempts=sum(p['attempts'] for p in role_parts),
                    estimated_airtime_us=sum(p['host_reserved_us'] for p in role_parts))
        native_used = sum(p['native_estimated_us'] for p in role_parts)
        need(all(used[key] <= campaign_remaining[unit][key] for key in used),
             'campaign_remaining_insufficient')
        need(native[unit]['attempts'] + used['attempts'] <= max_boot_attempts
             and native[unit]['estimated_airtime_us'] + native_used <= max_boot_airtime_us,
             'native_cumulative_headroom_insufficient')
        need(all(p['attempts'] <= operation_caps[unit]['attempts']
                 and p['host_reserved_us'] <= operation_caps[unit]['estimated_airtime_us']
                 and p['frozen_minimum_own_us'] <= own_limit_us
                 and p['frozen_minimum_shared_us'] <= shared_limit_us for p in role_parts),
             'per_part_journal_or_frozen_scheduler_insufficient')
        need(all(p['max_frames'] <= max_frames for p in parts), 'frame_bound_insufficient')
        totals[unit] = dict(host_paid=used, native_estimated_airtime_us=native_used)
    return dict(source_fit=True, physical_success_inferred=False, totals=totals)


def carry_forward(previous, observation, *, evidence_sha256, install_receipt_sha256=None):
    """Retain an externally prepared history capsule and append a fresh observation.

    This helper is not the v3 ledger and cannot retire its fences or unknowns.
    Root must reconcile journals and close children before supplying quiescent
    observations. Incomplete historical actual cost remains explicitly unknown.
    """
    digest(evidence_sha256)
    need(type(previous) is dict and type(previous.get('history')) is list
         and previous['history'] and previous.get('historical_actual_cost_complete') is False,
         'immutable_history_capsule_required')
    old = previous['current']
    need(set(observation) == {'boot', 'attempts', 'estimated_airtime_us',
                             'fenced', 'quiescent'}, 'exact_observation_fields')
    need(type(observation['boot']) is str and observation['boot'], 'boot_required')
    for key in ('attempts', 'estimated_airtime_us'): integer(observation[key])
    need(type(observation['fenced']) is bool and observation['quiescent'] is True,
         'bounded_cleanup_required')
    if observation['boot'] == old['boot']:
        need(install_receipt_sha256 is None, 'same_boot_not_install_transition')
        need(all(observation[key] >= old[key] for key in ('attempts', 'estimated_airtime_us')),
             'native_counter_regression')
        need(not old['fenced'] or observation['fenced'], 'same_boot_fence_cannot_clear')
    else:
        digest(install_receipt_sha256)
        need(old['quiescent'] is True, 'old_boot_not_closed')
    result = deepcopy(previous)
    result['history'].append(dict(previous_state_sha256=sha(canonical(previous)),
        previous_observation=deepcopy(old), evidence_sha256=evidence_sha256,
        install_receipt_sha256=install_receipt_sha256))
    result['current'] = deepcopy(observation)
    return result
