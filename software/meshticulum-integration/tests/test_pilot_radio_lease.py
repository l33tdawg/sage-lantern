"""Original-expiry regression through the actual USB worker's source path.

The serial replies and clocks below are modeled. No native owner, port, radio
or production permission is established by these controls.
"""
from dataclasses import asdict
import hashlib

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Decoder, FirmwareIdentity, Frame, Message, RadioProfile
from meshticulum.bench.serial_worker import SerialWorker, journal_binding
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.profiles import RADIO_ADMISSION, profile_for
from tests.test_pilot_controller import BOOTS, MANIFEST, PEERS, PERMISSION, REQUEST, controller


class ModelChannel:
    def __init__(self, now):
        self.now = now
        self.decoder = Decoder()
        self.commands = []
        self.replies = []

    def monotonic(self):
        return self.now

    def write(self, raw, deadline):
        assert self.now < deadline
        for frame in self.decoder.feed(raw):
            self.commands.append(frame)
            if frame.type is Message.DISARM:
                self.replies.append(Frame(Message.DISARM_REPLY, frame.boot, frame.request, b'\0').encode())
            elif frame.type is Message.ARM:
                self.replies.append(Frame(Message.ARM_REPLY, frame.boot, frame.request, frame.body).encode())
            else:
                raise AssertionError('This lease control must send no RF packet.')

    def read(self, timeout):
        assert timeout > 0
        return self.replies.pop(0)


def package(remaining_ms):
    now = 1000000000
    return pair_packages(campaign_id='d'*32, resource_sha256=MANIFEST,
        application_expires_epoch_us=now+remaining_ms*1000,
        sender='unit-c', receiver='unit-b', transport='lora', format='MR1.request',
        descriptor=dict(bytes=len(REQUEST), sha256=hashlib.sha256(REQUEST).hexdigest()),
        source=None, request=REQUEST, peers=PEERS, boots=BOOTS, now_epoch_us=now,
        radio_permission=PERMISSION)


def arm_profile(tmp_path, profile, remaining_ms):
    # Model six seconds spent preparing the child before the native worker runs.
    channel = ModelChannel(16.0)
    worker = SerialWorker(channel)
    worker.identity = FirmwareIdentity(b'\x01'*16, 2, 'mtb1-9816f92488')
    context = b'\x02'*32
    directory = tmp_path / str(profile.lease_ms)
    directory.mkdir(mode=0o700)
    journal = AttemptJournal(directory/'attempts.sqlite',
        binding=journal_binding(context, worker.identity, profile),
        max_attempts=profile.max_boot_attempts,
        max_airtime_us=profile.max_boot_estimated_airtime_us, create=True)
    try:
        worker.arm(journal=journal, profile=profile, context_digest=context,
            authorize=lambda: True, authority_deadline=10.0+remaining_ms/1000)
        assert worker.armed_until < worker.deadline
        assert journal.snapshot()['attempts'] == 0
        return channel
    finally:
        journal.close()


@pytest.mark.parametrize('remaining_ms', [85000, 55000])
def test_fresh_shorter_lease_can_arm_without_extending_the_original_expiry(tmp_path, remaining_ms):
    pair = package(remaining_ms)
    grants = [p['grant'] for p in pair['packages'].values()]
    assert grants[0]['profile'] == grants[1]['profile']
    profile = RadioProfile(**grants[0]['profile'])
    RADIO_ADMISSION.check('MY', True, profile)
    assert profile.lease_ms < profile_for('sf7-500').lease_ms
    assert all(g['expires_epoch_us'] == 1000000000+remaining_ms*1000
        and g['service']['application_expires_epoch_us'] == g['expires_epoch_us'] for g in grants)
    channel = arm_profile(tmp_path, profile, remaining_ms)
    assert [frame.type for frame in channel.commands] == [Message.DISARM, Message.ARM]
    assert channel.commands[-1].body == profile.encode(board_id=2)


def test_fixed_ninety_second_lease_still_refuses_the_observed_short_window(tmp_path):
    with pytest.raises(HardwareError, match='radio_authority_expired'):
        arm_profile(tmp_path, profile_for('sf7-500'), 85000)


def test_ample_time_keeps_the_existing_profile_and_does_not_increase_radio_limits():
    pair = package(160000)
    assert all(p['grant']['profile'] == asdict(profile_for('sf7-500'))
        for p in pair['packages'].values())


def test_insufficient_original_time_refuses_before_preparing_or_starting_any_child():
    pilot, operator, clock = controller()
    clock.now = 146  # Original expiry is still current, with only 24 seconds left.
    with pytest.raises(WireError, match='pilot_original_time_too_short_for_radio_admission'):
        pilot.link('unit-c', 'unit-b', 'lora', 'MR1.request', REQUEST)
    assert not operator.events and not pilot.links
