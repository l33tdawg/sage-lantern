"""Native provisioner controls with synthetic facts; no device or activation."""
import base64
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from lantern_meshticulum.bench.pilot_provision import provision as native_provision
from lantern_meshticulum.bench.pilot_native_caps import NativeCapsError
from lantern_meshticulum.bench.pilot_parent_caps import ParentCapsError
from meshticulum.bench.services import resource
from meshticulum.bench.wire import WireError
from lantern_meshticulum.ip.node import _write
from tests.test_ip_node import prepare, facts
from tests.public_parent_helpers import authorize

CAPS_ERRORS = (WireError, NativeCapsError, ParentCapsError)


def provision(value, *, state, facts, clock):
    """Explicit synthetic owner enrollment precedes the production callsite."""
    if value['schema'] == 'meshticulum.public-pilot-package.v1':
        payload = base64.b64decode(value['payload_base64']) if value['payload_base64'] else request()
        qualified = authorize(value, state, clock, payload)
        value.clear(); value.update(qualified)
    return native_provision(value, state=state, facts=facts, clock=clock)


def package(tmp_path, payload, *, receiver=False, source_run=None):
    fixture = tmp_path / 'fixture'
    fixture.mkdir()
    directories, grant = prepare(fixture, payload, qualified=False)
    index = 1 if receiver else 0
    plan = json.loads((directories[index] / 'grant.json').read_text())
    plan['application_deadline_monotonic_us'] = None
    return dict(schema='meshticulum.public-pilot-package.v1', unit='unit-b' if receiver else 'unit-a',
        campaign_id='c' * 32, resource_sha256='a' * 64,
        application_expires_epoch_us=grant.application_expires_epoch_us, transport='tcp', grant=plan,
        secret_base64=base64.b64encode(grant.secret).decode(),
        payload_base64=None if receiver or source_run else base64.b64encode(payload).decode(), source_run_id=source_run)


def state(tmp_path):
    result = tmp_path / 'state'
    result.mkdir(mode=0o700)
    return result


def request():
    return resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)


def test_provision_sender_sets_native_original_deadline_without_claim_or_socket(tmp_path):
    value = package(tmp_path, request())
    root = state(tmp_path)
    from lantern_meshticulum.ip.node import clock
    result = provision(value, state=root, facts=facts('unit-a'), clock=clock)
    directory = root / 'runs' / result['run_id']
    context = json.loads((root / 'campaigns' / value['campaign_id'] / 'context.json').read_text())
    plan = json.loads((directory / 'grant.json').read_text())
    assert plan['application_deadline_monotonic_us'] <= context['application_deadline_monotonic_us']
    assert plan['application_deadline_monotonic_us'] == result['parent']['application_deadline_monotonic_us']
    assert result['schema']=='meshticulum.provisioned-public-pilot.v2' and result['parent']['prepared']
    assert (directory / 'sender.bin').read_bytes() == request()
    assert not (directory / 'started').exists() and not (directory / 'bytes.sqlite').exists()
    assert not result['socket_opened'] and not result['radio_opened']
    with pytest.raises(CAPS_ERRORS):
        provision(value, state=root, facts=facts('unit-a'), clock=clock)


def test_receiver_never_receives_management_payload(tmp_path):
    value = package(tmp_path, request(), receiver=True)
    root = state(tmp_path)
    from lantern_meshticulum.ip.node import clock
    value['payload_base64'] = base64.b64encode(request()).decode()
    with pytest.raises(CAPS_ERRORS):
        provision(value, state=root, facts=facts('unit-b'), clock=clock)
    assert not (root / 'runs').exists()
    value['payload_base64'] = None
    # The refused unqualified descriptor did not create trusted enrollment.
    import shutil
    shutil.rmtree(root/'fixture-controller')
    result = provision(value, state=root, facts=facts('unit-b'), clock=clock)
    assert not (root / 'runs' / result['run_id'] / 'sender.bin').exists()


def test_operator_cannot_supply_response_without_native_received_request(tmp_path):
    response = resource.BlockServer(lambda _: b'public fixture').handle(request())
    value = package(tmp_path, response)
    value['grant']['format'] = 'MR1.response'
    root = state(tmp_path)
    from lantern_meshticulum.ip.node import clock
    with pytest.raises(CAPS_ERRORS):
        provision(value, state=root, facts=facts('unit-a'), clock=clock)
    assert not (root / 'runs').exists()


def prior(root, value, payload, clock, **changes):
    run = root / 'runs' / value['source_run_id']
    run.mkdir(parents=True, mode=0o700)
    run.parent.chmod(0o700)
    result = dict(schema='meshticulum.physical-service-run.v1', unit=value['unit'],
        host_boot_id=value['grant']['host_boot_id'], complete=True, sends=False,
        service_format=value['grant']['format'], service_object_durable=True,
        service_object_sha256=hashlib.sha256(payload).hexdigest(),
        original_application_expires_epoch_us=value['application_expires_epoch_us'],
        original_application_deadline_us=clock()[1] + 10000000,
        parent=dict(resource_sha256=value['resource_sha256']))
    result.update(changes)
    _write(run / 'result.json', json.dumps(result).encode())
    _write(run / 'receiver.bin', payload)
    _write(run / 'active', b'active\n')
    return result


def test_relay_uses_exact_local_capture_and_tightens_to_its_original_deadline(tmp_path):
    payload = request(); value = package(tmp_path, payload, source_run='d' * 32)
    root = state(tmp_path)
    from lantern_meshticulum.ip.node import clock
    original = prior(root, value, payload, clock)
    result = provision(value, state=root, facts=facts('unit-a'), clock=clock)
    assert result['source_basis'] == 'prior_local_authenticated_public_quarantine'
    assert result['original_application_deadline_us'] == original['original_application_deadline_us']
    assert (root / 'runs' / result['run_id'] / 'sender.bin').read_bytes() == payload


@pytest.mark.parametrize('change', [{'complete': False}, {'sends': True}, {'service_object_durable': False},
    {'service_format': 'LME.binary'}, {'service_object_sha256': 'b' * 64},
    {'host_boot_id': '31f9c178-6090-40d9-9359-ecf1c7b950b1'}, {'original_application_expires_epoch_us': 1}])
def test_relay_refuses_incomplete_private_or_rebound_source(tmp_path, change):
    payload = request(); value = package(tmp_path, payload, source_run='d' * 32)
    root = state(tmp_path)
    from lantern_meshticulum.ip.node import clock
    prior(root, value, payload, clock, **change)
    with pytest.raises(CAPS_ERRORS):
        provision(value, state=root, facts=facts('unit-a'), clock=clock)
    assert not (root / 'runs' / value['grant']['session']['session_id']).exists()


def test_relay_refuses_operator_substitution_even_when_local_capture_is_valid(tmp_path):
    payload = request(); value = package(tmp_path, payload, source_run='d' * 32)
    root = state(tmp_path)
    from lantern_meshticulum.ip.node import clock
    prior(root, value, payload, clock)
    value['payload_base64'] = base64.b64encode(payload).decode()
    with pytest.raises(CAPS_ERRORS):
        provision(value, state=root, facts=facts('unit-a'), clock=clock)


@pytest.mark.parametrize('change', ['private', 'wrong_boot', 'operator_deadline'])
def test_bad_scope_is_rejected_without_installing_a_run(tmp_path, change):
    value = package(tmp_path, request()); root = state(tmp_path)
    from lantern_meshticulum.ip.node import clock
    if change == 'private': value['grant']['format'] = 'LME.binary'
    if change == 'wrong_boot': value['grant']['host_boot_id'] = '31f9c178-6090-40d9-9359-ecf1c7b950b1'
    if change == 'operator_deadline': value['grant']['application_deadline_monotonic_us'] = clock()[1] + 100000000
    with pytest.raises(CAPS_ERRORS):
        provision(value, state=root, facts=facts('unit-a'), clock=clock)
    assert not (root / 'runs').exists()


def test_historical_v1_package_cannot_create_any_native_campaign_or_run(tmp_path):
    value=package(tmp_path,request()); root=state(tmp_path)
    from lantern_meshticulum.ip.node import clock
    with pytest.raises(NativeCapsError):
        native_provision(value,state=root,facts=facts('unit-a'),clock=clock)
    assert list(root.iterdir())==[]


def test_unqualified_parent_approval_refuses_before_original_context(tmp_path):
    value=package(tmp_path,request());root=state(tmp_path)
    from lantern_meshticulum.ip.node import clock
    value=authorize(value,root,clock,request())
    approval=root/'parents'/value['parent']['parent_id']/'native.sqlite.approval'
    approval.write_bytes(b'retired\n')
    with pytest.raises(NativeCapsError):
        native_provision(value,state=root,facts=facts('unit-a'),clock=clock)
    assert not (root/'campaigns').exists() and not (root/'runs').exists()


def test_actual_captured_request_cannot_relabel_resource_and_full_debt_is_retained(tmp_path):
    wrong=resource.encode_request('/packs/'+'b'*64+'/manifest.json',0,32)
    value=package(tmp_path,wrong,source_run='d'*32);root=state(tmp_path)
    from lantern_meshticulum.ip.node import clock
    prior(root,value,wrong,clock)
    value=authorize(value,root,clock,wrong)
    with pytest.raises(WireError,match='pilot_request_resource_mismatch'):
        native_provision(value,state=root,facts=facts('unit-a'),clock=clock)
    assert not (root/'runs'/value['grant']['session']['session_id']).exists()
    connection=sqlite3.connect(root/'parents'/value['parent']['parent_id']/'native.sqlite')
    try:
        rows=connection.execute('SELECT body FROM claims').fetchall()
        assert len(rows)==1 and json.loads(rows[0][0])['child']['cost']==value['parent']['child']['cost']
        assert connection.execute('SELECT COUNT(*) FROM prepared').fetchone()==(0,)
    finally:connection.close()
