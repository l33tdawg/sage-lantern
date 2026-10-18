"""Actual broker/DeviceLock composition; PIN, consent handler and node are synthetic.

No live owner consent, private record, runtime deployment or node qualification.
"""
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lantern/benchmarks/tool_routing'))
from action_owner import BrokerReviewedActionAuthority, ProvisionedOwner
from action_authority import PRIVATE_ACTIONS
from action_session import ActionRefused, AuthenticatedScope
from sage_lantern.device_lock import DeviceLock, DeviceLockError
from sage_lantern.session_context import SessionContext
from test_functiongemma_sdk import sdk, rig as sdk_rig
from action_session import QualifiedActionBroker

spec = importlib.util.spec_from_file_location('owner_auth_broker',
    Path(__file__).resolve().parents[1]/'lantern/unit-a-device-auth.py')
auth_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth_module)
SEARCH = 'Find my saved note about the keys.'
SAVE = "Save a private note titled 'Keys' with exact text 'Use shelf 4.'"


@pytest.fixture
def setup(tmp_path):
    # Match the real SDK transport's monotonic units; individual lifecycle
    # tests advance this clock explicitly without sleeping.
    clock = [time.monotonic()]
    node = [True]
    class SyntheticVerifier:
        cleanup_confirmed = True
        def __call__(self,pin):
            return True
    device = DeviceLock(tmp_path/'lock',verifier=SyntheticVerifier(),
        clock=lambda: clock[0],wall_clock=lambda: 10000+clock[0])
    broker = auth_module.AuthBroker(device)
    binding = ProvisionedOwner('installation-test','unit-test','a'*64,'owner-test',
                              'sage-personal-test','loopback-test','b'*64)
    authority = BrokerReviewedActionAuthority(broker,binding,enabled=True,
        node_policy_check=lambda observed: observed == binding and node[0],clock=lambda: clock[0])
    def unlock():
        token = broker.unlock('12345678')['token']
        assert broker.activate(token) == {'authorized':True}
        return token,SessionContext.parse(broker.session_context(token))
    token,context = unlock()
    yield authority,broker,device,token,context,clock,node,unlock,binding
    authority.close()
    device.close()


def consent(a,token,context,actions=PRIVATE_ACTIONS):
    c = a.prepare_consent(token,context,actions=actions)
    return a.accept_consent(token,context,challenge_id=c.challenge_id,
                            reviewed_actions=actions,accepted=True)


def submitted(a,text=SEARCH):
    review = a.prepare_review(text,source='typed')
    return a.submit_review(review,reviewed_text=text,approve_private_write=text==SAVE)


def test_default_disabled_and_raw_scope_seam_unusable(setup):
    a,b,_,token,context,_,_,_,binding = setup
    inactive = BrokerReviewedActionAuthority(b,binding,node_policy_check=lambda _: True)
    with pytest.raises(ActionRefused):
        inactive.prepare_consent(token,context,actions=PRIVATE_ACTIONS)
    with pytest.raises(ActionRefused):
        a.activate(AuthenticatedScope(*(['synthetic']*9),9999),allowed_actions=PRIVATE_ACTIONS)
    with pytest.raises(ActionRefused):
        a.prepare_review(SEARCH,source='typed')


def test_scope_comes_only_from_provisioned_binding_and_current_context(setup):
    a,_,_,token,context,_,_,_,binding = setup
    scope = consent(a,token,context)
    assert scope.owner == binding.owner_ref and scope.sage_identity == binding.identity
    assert scope.private_domain == binding.domain and scope.sage_chain == binding.chain
    assert scope.sage_endpoint_profile == binding.endpoint_profile and scope.device == binding.device
    assert scope.session == context.session_ref and scope.epoch == context.epoch
    assert token not in repr(scope) and token not in repr(a.public_binding())
    instruction = submitted(a)
    assert a.resolve(instruction) == scope


@pytest.mark.parametrize('accepted',[False,1,'true',None])
def test_unapproved_or_coerced_consent_never_grants(setup,accepted):
    a,_,_,token,context,*_ = setup
    c = a.prepare_consent(token,context,actions=PRIVATE_ACTIONS)
    with pytest.raises(ActionRefused):
        a.accept_consent(token,context,challenge_id=c.challenge_id,
                         reviewed_actions=PRIVATE_ACTIONS,accepted=accepted)
    with pytest.raises(ActionRefused):
        a.accept_consent(token,context,challenge_id=c.challenge_id,
                         reviewed_actions=PRIVATE_ACTIONS,accepted=True)


def test_changed_actions_or_context_consumes_the_challenge(setup):
    a,_,_,token,context,*_ = setup
    c = a.prepare_consent(token,context,actions=PRIVATE_ACTIONS)
    with pytest.raises(ActionRefused):
        a.accept_consent(token,context,challenge_id=c.challenge_id,
                         reviewed_actions=frozenset({'search_memory'}),accepted=True)
    with pytest.raises(ActionRefused):
        a.accept_consent(token,context,challenge_id=c.challenge_id,
                         reviewed_actions=PRIVATE_ACTIONS,accepted=True)
    with pytest.raises(ActionRefused):
        a.prepare_consent(token,replace(context,epoch='c'*32),actions=PRIVATE_ACTIONS)


def test_node_policy_cannot_be_assumed_from_authentication(setup):
    a,_,_,token,context,_,node,*_ = setup
    node[0] = False
    with pytest.raises(ActionRefused):
        a.prepare_consent(token,context,actions=PRIVATE_ACTIONS)
    node[0] = 1
    with pytest.raises(ActionRefused):
        a.prepare_consent(token,context,actions=PRIVATE_ACTIONS)


def test_pending_pin_ticket_is_not_an_active_owner_session(setup):
    a,b,_,_,context,*_ = setup
    pending = b.unlock('12345678')['token']
    with pytest.raises(ActionRefused):
        a.prepare_consent(pending,context,actions=PRIVATE_ACTIONS)


def test_read_only_consent_cannot_prepare_private_save(setup):
    a,_,_,token,context,*_ = setup
    consent(a,token,context,frozenset({'search_memory'}))
    with pytest.raises(ActionRefused):
        a.prepare_review(SAVE,source='typed')
    assert a.check(a.resolve(submitted(a)),submitted(a))


@pytest.mark.parametrize('event',['lock','cancel','shutdown','presence_expiry','idle_expiry','epoch_rotation'])
def test_actual_broker_revocation_discards_grants_and_review_text(setup,event):
    a,b,device,token,context,clock,*_ = setup
    consent(a,token,context)
    review = a.prepare_review(SEARCH,source='typed')
    if event == 'lock':
        b.lock(token)
    elif event == 'cancel':
        b.cancel(token)
    elif event == 'shutdown':
        b.shutdown()
    elif event == 'presence_expiry':
        clock[0] += 7
        assert b.authorize(token) == {'authorized':False}
    elif event == 'idle_expiry':
        device._session_seconds = 1
        # Actual touch applies the configured deadline without renewing presence.
        assert b.activity(token) == {'authorized':True}
        clock[0] += 2
        assert b.state() == {'locked':True}
    else:
        device._access_epoch = 'e'*32
        b.access_epoch()
    assert a._scope is None and a._token is None and a._context is None
    assert a._reviews == {} and a._submissions == {}
    with pytest.raises(ActionRefused):
        a.submit_review(review,reviewed_text=SEARCH)


def test_stale_request_cannot_revoke_another_current_session(setup):
    a,b,_,old,old_context,_,_,unlock,_ = setup
    b.lock(old)
    token,context = unlock()
    scope = consent(a,token,context)
    instruction = submitted(a)
    assert b.authorize(old) == {'authorized':False}
    with pytest.raises(ActionRefused):
        a.prepare_consent(old,old_context,actions=PRIVATE_ACTIONS)
    assert a.check(scope,instruction)


def test_failed_revocation_listener_closes_auth_but_runs_other_listeners(setup):
    a,b,_,token,context,*_ = setup
    consent(a,token,context)
    seen=[]
    def broken():
        raise RuntimeError('synthetic failure')
    b.subscribe_revocation(broken)
    b.subscribe_revocation(lambda:seen.append('last'))
    with pytest.raises(DeviceLockError):
        b.lock(token)
    assert seen == ['last'] and b.closing and b.cleanup_unknown
    assert a._scope is None
    with pytest.raises(DeviceLockError):
        b.authorize(token)


def test_lock_wins_before_first_action_send_and_late_publication(setup):
    a,b,_,token,context,*_ = setup
    scope = consent(a,token,context)
    instruction = submitted(a)
    waiting,release = threading.Event(),threading.Event()
    effects,errors=[],[]
    def worker():
        try:
            def work():
                waiting.set()
                assert release.wait(2)
                return a.dispatch_step(scope,instruction,lambda:effects.append('send'))
            a.run_guarded(scope,instruction,work)
        except ActionRefused:
            errors.append('refused')
    thread=threading.Thread(target=worker)
    thread.start()
    assert waiting.wait(1)
    b.lock(token)
    release.set()
    thread.join(2)
    assert not thread.is_alive() and effects == [] and errors == ['refused']
    assert a.drain(1)


def test_new_session_cannot_steal_inflight_submission(setup):
    a,b,_,token,context,_,_,unlock,_ = setup
    scope=consent(a,token,context)
    instruction=submitted(a)
    waiting,release=threading.Event(),threading.Event()
    errors=[]
    def worker():
        try:
            a.run_guarded(scope,instruction,lambda:(waiting.set(),release.wait(2)))
        except ActionRefused:
            errors.append('refused')
    t=threading.Thread(target=worker)
    t.start(); assert waiting.wait(1)
    b.lock(token)
    new,current=unlock()
    with pytest.raises(ActionRefused):
        consent(a,new,current)
    release.set(); t.join(2)
    assert errors == ['refused'] and not t.is_alive()
    assert a.drain(1)
    assert consent(a,new,current).session == current.session_ref


def test_consent_does_not_renew_presence_or_idle_lease(setup):
    a,b,_,token,context,clock,*_ = setup
    clock[0] += 5
    consent(a,token,context)
    clock[0] += 2
    assert b.authorize(token) == {'authorized':False}
    with pytest.raises(ActionRefused):
        a.prepare_review(SEARCH,source='typed')


def test_expired_consent_or_policy_drift_cannot_dispatch(setup):
    a,_,_,token,context,clock,node,*_ = setup
    c=a.prepare_consent(token,context,actions=PRIVATE_ACTIONS)
    clock[0] += 31
    with pytest.raises(ActionRefused):
        a.accept_consent(token,context,challenge_id=c.challenge_id,
                         reviewed_actions=PRIVATE_ACTIONS,accepted=True)
    # Node drift on a fresh active scope prevents the first send too.
    token,context=setup[7]()
    scope=consent(a,token,context)
    instruction=submitted(a)
    node[0]=False
    effects=[]
    with pytest.raises(ActionRefused):
        a.run_guarded(scope,instruction,lambda:effects.append('send'))
    assert effects == [] and a._scope is None


def test_shutdown_receipt_cannot_hide_an_undrained_action_worker(setup):
    a,b,_,token,context,*_=setup
    scope=consent(a,token,context)
    instruction=submitted(a)
    entered,release=threading.Event(),threading.Event()
    def worker():
        try:
            a.run_guarded(scope,instruction,lambda:(entered.set(),release.wait(2)))
        except ActionRefused:
            pass
    t=threading.Thread(target=worker)
    t.start(); assert entered.wait(1)
    b.shutdown()
    receipt=b.drain_state(True)
    assert receipt['drained'] is False and receipt['upstream_cleanup']=='unknown'
    release.set(); t.join(2)
    assert not t.is_alive() and a.drained()
    # Later quiescence cannot rewrite this invocation's earlier unknown outcome.
    assert b.drain_state(True)['upstream_cleanup']=='unknown'


def test_sleep_preflight_refuses_active_consent_and_unknown_drain_probe(setup):
    a,b,_,token,context,*_=setup
    assert b.sleep_ready()=={'ready':True}
    consent(a,token,context)
    assert b.sleep_ready()=={'ready':False}
    b.lock(token)
    assert b.sleep_ready()=={'ready':True}
    b.subscribe_revocation(lambda:None,drained=lambda:1)
    assert b.sleep_ready()=={'ready':False}


def test_consent_replay_and_regrant_in_the_same_revoked_epoch_are_refused(setup):
    a,_,_,token,context,*_=setup
    challenge=a.prepare_consent(token,context,actions=PRIVATE_ACTIONS)
    a.accept_consent(token,context,challenge_id=challenge.challenge_id,
                     reviewed_actions=PRIVATE_ACTIONS,accepted=True)
    with pytest.raises(ActionRefused):
        a.accept_consent(token,context,challenge_id=challenge.challenge_id,
                         reviewed_actions=PRIVATE_ACTIONS,accepted=True)
    a.revoke()
    with pytest.raises(ActionRefused):
        consent(a,token,context)


def test_failed_bounded_action_drain_remains_tainted(setup):
    a,b,_,token,context,*_=setup
    scope=consent(a,token,context)
    instruction=submitted(a)
    entered,release=threading.Event(),threading.Event()
    def worker():
        try:
            a.run_guarded(scope,instruction,lambda:(entered.set(),release.wait(2)))
        except ActionRefused:
            pass
    t=threading.Thread(target=worker)
    t.start(); assert entered.wait(1)
    b.lock(token)
    assert a.drain(0) is False
    release.set(); t.join(2)
    assert not t.is_alive() and a.drained() is False
    assert a.drain(1) is False and b.sleep_ready()=={'ready':False}


def composed_sdk(setup,rig):
    _,auth,_,token,context,clock,_,_,_=setup
    private=rig['backend']._registered
    binding=ProvisionedOwner('test-installation','test-device',private.identity,
        private.domain,private.chain,private.endpoint_profile,private.private_policy_sha256)
    authority=BrokerReviewedActionAuthority(auth,binding,enabled=True,
        node_policy_check=lambda _:True,clock=lambda:clock[0])
    scope=consent(authority,token,context)
    broker=QualifiedActionBroker(authority=authority,backend=rig['backend'],journal=rig['journal'],
        verifier=rig['backend'],qualification=rig['broker'].qualification,
        backend_profile_sha256=rig['backend'].profile_sha256(),enabled=True,
        clock=lambda:clock[0])
    return authority,broker,scope


def test_real_sdk_save_and_canonical_observation_with_actual_broker_gate(setup,sdk_rig):
    authority,broker,_=composed_sdk(setup,sdk_rig)
    instruction=submitted(authority,SAVE)
    handle=broker.bind(instruction,deadline=setup[5][0]+20)
    published=[]
    result=broker.execute(handle,publish=published.append)
    assert result['receipt'].state=='committed' and published==[result]
    assert len(sdk_rig['state']['requests'])==sdk_rig['state']['verified_signatures']
    setup[1].lock(setup[3])
    assert authority._scope is None


def test_actual_broker_lock_refuses_private_sdk_before_any_bytes(setup,sdk_rig):
    authority,broker,_=composed_sdk(setup,sdk_rig)
    handle=broker.bind(submitted(authority,SAVE),deadline=setup[5][0]+20)
    setup[1].lock(setup[3])
    with pytest.raises(ActionRefused):
        broker.execute(handle)
    assert sdk_rig['state']['requests']==[] and list(sdk_rig['directory'].glob('*.reserved'))==[]


def test_actual_broker_lock_interrupts_stalled_sdk_body_without_false_receipt(setup,sdk_rig):
    authority,broker,_=composed_sdk(setup,sdk_rig)
    handle=broker.bind(submitted(authority,SAVE),deadline=setup[5][0]+20)
    sdk_rig['state']['mode']='blocked_body'
    errors=[]; published=[]
    def worker():
        try:
            broker.execute(handle,publish=published.append)
        except ActionRefused:
            errors.append('refused')
    t=threading.Thread(target=worker)
    t.start(); assert sdk_rig['state']['accepted'].wait(1)
    setup[1].lock(setup[3])
    try:
        t.join(1)
        assert not t.is_alive() and errors==['refused'] and published==[]
        assert authority.drained()
        assert len(list(sdk_rig['directory'].glob('*.reserved')))==1
        assert list(sdk_rig['directory'].glob('*.receipt'))==[]
    finally:
        sdk_rig['state']['release'].set()
        t.join(2)
