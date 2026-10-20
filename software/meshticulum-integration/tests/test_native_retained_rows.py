"""Pure row reuse never replaces current SQL, ownership or lifetime checks."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import os

import pytest

from lantern_meshticulum.bench import pilot_native_caps as native
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from tests.test_pilot_native_caps import state, packet


def warm(state):
    caps, observer, policy, path, root = state
    caps.prepare_claim(packet(policy, 'lora'))
    assert caps.current()
    return caps, observer, policy


def test_current_freshly_reads_enrollment_all_claims_and_each_resource_after_warmup(state):
    caps, observer, policy = warm(state)
    caps.prepare_claim(packet(policy, 'lora', campaign='8'*32))
    assert caps.current()
    before = caps._claim_rows.cache_info()
    queries = []
    caps.db.set_trace_callback(queries.append)
    assert caps.current()
    caps.db.set_trace_callback(None)
    assert queries[0] == 'SELECT body FROM enrollment WHERE id=1'
    assert queries[1] == 'SELECT id,body,run_id FROM claims'
    assert len(queries) == 4
    assert all(query.startswith('SELECT body FROM resources WHERE id=') for query in queries[2:])
    after = caps._claim_rows.cache_info()
    assert after.hits-before.hits == 2 and after.misses == before.misses
    assert caps.snapshot()['debt']['radio_attempts'] == 513


@pytest.mark.parametrize('change', ['body', 'cost', 'unit', 'boot', 'policy', 'metadata_run', 'row_run', 'id'])
def test_changed_claim_bytes_id_and_run_link_are_not_accepted_from_warm_cache(state, change):
    caps, observer, policy = warm(state)
    cid, raw, run = caps.db.execute('SELECT id,body,run_id FROM claims').fetchone()
    claim = json.loads(raw)
    if change == 'body':
        raw = b'{}'
    elif change == 'cost':
        claim['child']['cost']['radio_attempts'] = 0
        raw = canonical(claim)
    elif change in ('unit', 'boot', 'policy', 'metadata_run'):
        key = {'unit': 'unit', 'boot': 'host_boot_id', 'policy': 'policy_digest', 'metadata_run': 'run_id'}[change]
        claim['child']['metadata'][key] += 'changed'
        raw = canonical(claim)
    elif change == 'row_run':
        run = '9'*32
    else:
        cid = '9'*64
    caps.db.execute('DROP TRIGGER immutable_claims_UPDATE')
    caps.db.execute('UPDATE claims SET id=?,body=?,run_id=?', (cid, raw, run))
    assert caps.current() is False and caps._uncertain
    assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone() == (1,)


@pytest.mark.parametrize('change', ['bytes', 'delete'])
def test_cached_claim_still_checks_fresh_resource_body(state, change):
    caps, observer, policy = warm(state)
    if change == 'bytes':
        caps.db.execute('DROP TRIGGER immutable_resources_UPDATE')
        caps.db.execute('UPDATE resources SET body=?', (b'{}',))
    else:
        caps.db.execute('DROP TRIGGER immutable_resources_DELETE')
        caps.db.execute('DELETE FROM resources')
    assert caps.current() is False and caps._uncertain
    assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone() == (1,)


def test_new_appended_row_gets_full_hash_and_cost_validation(state):
    caps, observer, policy = warm(state)
    old = caps._claim_rows.cache_info()
    caps.prepare_claim(packet(policy, 'lora', campaign='8'*32))
    assert caps.current() and caps._claim_rows.cache_info().misses > old.misses
    assert caps.snapshot()['claims'] == 2 and caps.snapshot()['debt']['radio_attempts'] == 513
    cid, raw, run = caps.db.execute('SELECT id,body,run_id FROM claims LIMIT 1').fetchone()
    claim = deepcopy(json.loads(raw))
    claim['child']['metadata']['run_id'] = '7'*32
    claim['child']['cost']['radio_attempts'] = -1
    raw = canonical(claim)
    cid = hashlib.sha256(native.DOMAIN+raw).hexdigest()
    caps.db.execute('INSERT INTO claims VALUES(?,?,?)', (cid, raw, '7'*32))
    assert caps.current() is False
    assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone() == (3,)


@pytest.mark.parametrize('change', ['wrapper', 'binding', 'wrapped', 'code', 'defaults'])
def test_callback_cannot_replace_retained_validator_or_make_it_return_free_costs(state, change):
    caps, observer, policy = warm(state)
    validator = caps._claim_rows_binding[1]
    original_code, original_defaults = validator.__code__, validator.__defaults__
    def attack(_):
        if change == 'wrapper':
            caps._claim_rows = lru_cache(maxsize=4096, typed=True)(validator)
        elif change == 'binding':
            caps._claim_rows_binding = tuple(list(caps._claim_rows_binding))
        elif change == 'wrapped':
            caps._claim_rows.__wrapped__ = lambda *_: ('forged', b'{}', (0, 0, 0, 0, 0))
        elif change == 'code':
            validator.__code__ = (lambda *_: ('forged', b'{}', (0, 0, 0, 0, 0))).__code__
        else:
            validator.__defaults__ = ('changed',)
    observer.hook = attack
    try:
        assert caps.current() is False
        assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone() == (1,)
    finally:
        observer.hook = None
        validator.__code__, validator.__defaults__ = original_code, original_defaults


@pytest.mark.parametrize('change', ['revoked_file', 'approval_denied', 'facts', 'epoch', 'monotonic'])
def test_warm_representation_does_not_reuse_owner_approval_or_deadlines(state, change):
    caps, observer, policy = warm(state)
    if change == 'revoked_file':
        os.ftruncate(caps._fds['approval'], 0)
    elif change == 'approval_denied':
        observer.allowed = False
    elif change == 'facts':
        observer.boot = '00000000-0000-4000-8000-000000000002'
    elif change == 'epoch':
        observer.epoch = policy.expires_epoch_us
    else:
        observer.mono = caps._enrollment['deadline_monotonic_us']
    assert caps.current() is False
    assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone() == (1,)
    assert caps.db.execute('SELECT COUNT(*) FROM prepared').fetchone() == (0,)
    assert caps.db.execute('SELECT COUNT(*) FROM starts').fetchone() == (0,)


def test_retained_values_are_immutable_and_context_is_part_of_the_exact_key(state):
    caps, observer, policy = warm(state)
    cid, raw, run = caps.db.execute('SELECT id,body,run_id FROM claims').fetchone()
    value = caps._claim_rows(cid, raw, run, caps.unit.unit, caps.unit.host_boot_id, caps._policy_digest)
    with pytest.raises(TypeError):
        value[2][0] = 0
    for unit, boot, digest in (('unit-b', caps.unit.host_boot_id, caps._policy_digest),
                               (caps.unit.unit, 'changed', caps._policy_digest),
                               (caps.unit.unit, caps.unit.host_boot_id, 'changed')):
        with pytest.raises(native.NativeCapsError):
            caps._claim_rows(cid, raw, run, unit, boot, digest)
    assert caps.current() and caps.snapshot()['debt']['radio_attempts'] == 257
