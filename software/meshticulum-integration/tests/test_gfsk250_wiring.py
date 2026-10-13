"""Source-only opt-in250 wiring; no device, live grant or new paid history."""
import hashlib
import struct
from types import SimpleNamespace

import pytest

from meshticulum.bench.admission import GfskAdmission,Gfsk250Admission
from meshticulum.bench.gfsk_profile import GfskProfile,GfskProfileError
from meshticulum.bench.gfsk250_profile import Gfsk250Profile
from meshticulum.bench.serial_protocol import Frame,Message,RadioProfile,SerialError
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import node,profiles
from lantern_meshticulum.bench.pilot_controller import pair_packages
from tests.test_pilot_controller import PEERS,BOOTS,model_parent,Clock,release_model_parents
from meshticulum.resources import resource


def test_explicit250_profile_admission_record_route_and_default125():
    old=profiles.profile_for('gfsk125');new=profiles.profile_for('gfsk250')
    assert type(old) is GfskProfile and old.bit_rate_bps==125000 and old.rx_bandwidth_hz==312000
    assert type(new) is Gfsk250Profile and (new.bit_rate_bps,new.deviation_hz,new.rx_bandwidth_hz)==(250000,62500,467000)
    assert type(profiles.admission_for(old)) is GfskAdmission
    assert type(profiles.admission_for(new)) is Gfsk250Admission
    assert profiles.profile_from_plan(dict(radio_modem='gfsk250',profile=profiles.profile_record(new)))==new
    assert profiles.modem_for(new)=='gfsk250' and profiles.modem_for(old)=='gfsk125'
    for mode in profiles.MODES:
        assert type(profiles.profile_for(mode)) is RadioProfile and profiles.modem_for(profiles.profile_for(mode)) is None
    plan={k:{} for k in ('initiator','responder','session_id','generation','profile','object','sender_unit','country','service')}
    plan['radio_modem']='gfsk250';assert profiles.radio_route(plan)['radio_modem']=='gfsk250'
    assert node._fast_profile(new)


@pytest.mark.parametrize('marker,record',[('gfsk125','gfsk250'),('gfsk250','gfsk125'),(None,'gfsk250')])
def test_profile_modes_never_downgrade(marker,record):
    plan=dict(profile=profiles.profile_record(profiles.profile_for(record)))
    if marker is not None:plan['radio_modem']=marker
    with pytest.raises((WireError,GfskProfileError,TypeError)):profiles.profile_from_plan(plan)


def test_unbound_native250_refuses_before_any_port_open(monkeypatch):
    from lantern_meshticulum.bench import gfsk250_mb1_bundle as mode
    from types import MappingProxyType
    monkeypatch.setattr(mode,'PHYSICAL_BINDINGS',MappingProxyType(dict(mode.PHYSICAL_BINDINGS,
        scope_sha256=None,history_receipt_sha256=None,preceding_parent=None)))
    calls=[];monkeypatch.setattr(node,'ExclusiveSerial',lambda *a,**k:calls.append('opened'))
    with pytest.raises(WireError,match='pending_gfsk250_native_scope'):
        node._serial('unit-a',radio_modem='gfsk250')
    assert calls==[]
    with pytest.raises(TypeError):mode.PHYSICAL_BINDINGS['scope_sha256']='f'*64
    assert all(mode.PHYSICAL_BINDINGS[k] is None for k in ('scope_sha256','history_receipt_sha256','preceding_parent'))


def status250(*,second_refusal=1,boot_mismatch=False,flags=b'\0\0\0'):
    boot=bytes.fromhex('1'*32);identity=SimpleNamespace(boot=boot,board_id=1)
    worker=SimpleNamespace(channel=SimpleNamespace(monotonic=lambda:10),sent=[],ends=[])
    def send(frame,end):worker.sent.append(frame);worker.ends.append(end)
    def wait(request,end):
        n=len(worker.sent)
        if n==1:return Frame(Message.REFUSED,boot,request,b'\1')
        if n==2:return Frame(Message.REFUSED,bytes(16) if boot_mismatch else boot,request,bytes([second_refusal]))
        return Frame(Message.GFSK250_STATUS_REPLY,boot,request,
            profiles.profile_for('gfsk250').encode(board_id=1)+struct.pack('!IQI',7,1000,97)+flags)
    worker._send=send;worker._wait=wait;return worker,identity


def test_status250_fallback_is_readonly_boot_bound_and_uses_same_original_end(monkeypatch):
    monkeypatch.setattr(node.time,'time_ns',lambda:1000000)
    worker,identity=status250();result=node._disarmed_status(worker,identity)
    assert result==dict(boot_attempts=7,boot_reserved_estimated_airtime_us=1000,dropped_receive=97,radio_modem='gfsk250')
    assert [v.type for v in worker.sent]==[Message.STATUS,Message.GFSK_STATUS,Message.GFSK250_STATUS]
    assert worker.ends==[12,12,12]


@pytest.mark.parametrize('kwargs',[dict(second_refusal=2),dict(boot_mismatch=True),dict(flags=b'\1\0\0'),dict(flags=b'\0\1\0'),dict(flags=b'\0\0\1')])
def test_status250_mismatch_or_active_flags_are_not_readiness(kwargs):
    worker,identity=status250(**kwargs)
    with pytest.raises((WireError,SerialError)):node._disarmed_status(worker,identity)
    assert all(f.type not in (Message.ARM,Message.GFSK_ARM,Message.GFSK250_ARM,Message.TRANSMIT) for f in worker.sent)


def test_pure_package250_binds_new_marker_profile_without_native_issuance():
    payload=resource.encode_request('/packs/'+'f'*64+'/chunks/'+'e'*64+'.json',0,16356)
    peers={u:PEERS[u] for u in ('unit-a','unit-c')}
    packages=pair_packages(campaign_id='a'*32,resource_sha256='f'*64,
        application_expires_epoch_us=180000000,sender='unit-c',receiver='unit-a',transport='lora',
        format='MR1.request',descriptor=dict(bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest()),
        source=None,request=payload,peers=peers,boots={u:BOOTS[u] for u in peers},now_epoch_us=1000000,
        profile='gfsk250',radio_permission=dict(country='MY',antenna_confirmed=True))
    for package in packages['packages'].values():
        assert package['grant']['radio_modem']=='gfsk250'
        assert profiles.profile_from_plan(package['grant']).bit_rate_bps==250000


def test_legacy_native_parent_cannot_enroll250_or_spend_old_caps():
    from lantern_meshticulum.bench.pilot_controller import Controller
    clock=Clock();parent=model_parent(clock);before=parent.snapshot()
    with pytest.raises(WireError,match='gfsk250_parent_kind_mismatch'):
        Controller(object(),campaign_id='a'*32,resource_sha256='f'*64,
            expires_epoch_us=clock.epoch()+60000000,handoff_deadline_monotonic=clock.mono()+100,
            peers=PEERS,boots=BOOTS,parent=parent,clock=clock.mono,epoch=clock.epoch,
            radio_profile='gfsk250',radio_permission=dict(country='MY',antenna_confirmed=True))
    assert parent.snapshot()==before

