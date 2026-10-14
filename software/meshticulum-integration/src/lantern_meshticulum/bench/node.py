# SPDX-License-Identifier: Apache-2.0
# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Root-only finite bench process. No listener, automatic enrollment or service."""
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import signal
import stat
import subprocess
import time

from meshticulum.bench.attempts import AttemptJournal
from .profiles import (RADIO_ADMISSION,GFSK_NAME,GFSK250_NAME,profile_from_plan,admission_for,
                       require_gfsk_bindings,require_gfsk250_bindings,modem_for)
from meshticulum.bench.public_block import public32_service, public_mb1_service, object_limit
from meshticulum.bench.authority import RunAuthority, _open, _private_info
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.hat_worker import HATWorker,GfskHATWorker,Gfsk250HATWorker, journal_binding as hat_binding
from meshticulum.bench.linux_hat import LinuxHAT
from meshticulum.bench.objects import VerifiedChunks
from meshticulum.bench.serial_port import ExclusiveSerial
from meshticulum.bench.serial_protocol import Frame, Message,GfskStatus,Gfsk250Status
from meshticulum.bench.serial_worker import SerialWorker,GfskSerialWorker,Gfsk250SerialWorker, journal_binding as serial_binding
from meshticulum.bench.gfsk_profile import GfskProfile,radio_is_fast
from meshticulum.bench.gfsk250_profile import Gfsk250Profile
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import SessionCipher, require
from meshticulum.scheduler.budget import BudgetPolicy, Ledger

FIRMWARE = 'mtb1-9816f92488'
FIRMWARE_BY_UNIT = {'unit-a': 'mtb1-b433927ba6', 'unit-b': FIRMWARE}
USB = {'unit-a': 'F8:5B:1B:A5:F6:08', 'unit-b': '44:1B:F6:62:56:54'}
PERSONAL = ('sage-lantern-personal-sage.service', 'sage-lantern-voice-loop.service',
            'sage-lantern-llm.service', 'sage-lantern-vlm.service')


def clock():
    return time.time_ns() // 1000, time.monotonic_ns() // 1000


def role_facts(unit):
    """One fresh checked systemctl sample of exact unit states and lifetimes."""
    require(platform.system() == 'Linux' and os.geteuid() == 0, 'requires_linux_root')
    require(unit in ('unit-a', 'unit-b', 'unit-c'), 'unknown_bench_unit')
    units = PERSONAL if unit == 'unit-c' else (PERSONAL[:3] + ('sage-lantern-mesh-runtime.service',))
    properties = ('Id', 'ActiveState', 'MainPID', 'InvocationID', 'ExecMainStartTimestampMonotonic')
    command = ['/usr/bin/systemctl', '--no-pager', '--all', 'show', *units]
    for name in properties: command.extend(('-p', name))
    result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    require(result.returncode == 0 and len(result.stdout) <= 65536, 'cannot_read_service_role')
    rows, row = {}, {}
    for line in [*result.stdout.splitlines(), '']:
        if not line:
            if not row: continue
            require(set(row) == set(properties) and row['Id'] in units and row['Id'] not in rows,
                    'invalid_service_role_sample')
            rows[row['Id']] = row; row = {}
        else:
            key, separator, value = line.partition('=')
            require(separator and key in properties and key not in row, 'invalid_service_role_sample')
            row[key] = value
    require(set(rows) == set(units), 'incomplete_service_role_sample')
    states, lifetimes = {}, {}
    for name in units:
        row = rows[name]
        require(row['ActiveState'] and all(row[key].isascii() and row[key].isdecimal()
                for key in ('MainPID','ExecMainStartTimestampMonotonic'))
                and (not row['InvocationID'] or len(row['InvocationID']) == 32
                     and all(c in '0123456789abcdef' for c in row['InvocationID'])), 'invalid_service_role_sample')
        states[name] = row['ActiveState']
        lifetimes[name] = {key:row[key] for key in properties[2:]}
        if unit != 'unit-c':
            require(row['ActiveState'] in ('inactive', 'failed') and row['MainPID'] == '0',
                    'private_or_legacy_radio_service_active')
    if unit == 'unit-c':
        require(b'rockchip,rk3588-orangepi-5-plus\0' in Path('/proc/device-tree/compatible').read_bytes(),
                'wrong_c_board')
    else:
        root = Path('/etc/sage-lamplighter-lab')
        require(json.loads((root / 'operator.json').read_text())['unit'] == unit
                and (root / 'enabled').is_file(), 'wrong_public_carrier_unit')
    return states, lifetimes


def roles(unit):
    return role_facts(unit)[0]


def _disarmed_status(worker,identity):
    request=time.time_ns()//1000;end=worker.channel.monotonic()+2
    worker._send(Frame(Message.STATUS,identity.boot,request),end);reply=worker._wait(request,end)
    require(reply.boot==identity.boot and reply.request==request,'unbound_radio_status')
    if reply.type is Message.REFUSED:
        # Only the exact unsupported opcode means the current modem differs.
        require(reply.body==bytes([1]),'radio_status_refused')
        request+=1
        worker._send(Frame(Message.GFSK_STATUS,identity.boot,request),end);reply=worker._wait(request,end)
        if reply.type is Message.REFUSED:
            require(reply.boot==identity.boot and reply.request==request and reply.body==bytes([1]),
                    'radio_status_refused')
            request+=1
            worker._send(Frame(Message.GFSK250_STATUS,identity.boot,request),end);reply=worker._wait(request,end)
            status=Gfsk250Status.from_reply(reply,boot=identity.boot,request=request,board_id=1)
            modem=GFSK250_NAME
        else:
            status=GfskStatus.from_reply(reply,boot=identity.boot,request=request,board_id=1)
            modem=GFSK_NAME
        require(not status.armed and not status.transmitting and not status.fenced,'radio_not_disarmed_or_fenced')
        return dict(boot_attempts=status.boot_attempts,
            boot_reserved_estimated_airtime_us=status.boot_reserved_estimated_airtime_us,
            dropped_receive=status.dropped_receive,radio_modem=modem)
    require(reply.type is Message.STATUS_REPLY and len(reply.body)==49 and reply.body[46:]==bytes(3),
            'radio_not_disarmed_or_fenced')
    return dict(boot_attempts=int.from_bytes(reply.body[30:34],'big'),
        boot_reserved_estimated_airtime_us=int.from_bytes(reply.body[34:42],'big'),
        dropped_receive=int.from_bytes(reply.body[42:46],'big'))


def _serial(unit,*,radio_modem=None,qualification_kind=None,mcu_artifact_selector=None):
    require(radio_modem in (None,GFSK_NAME,GFSK250_NAME),'unsupported_native_radio_modem')
    firmware=FIRMWARE_BY_UNIT[unit]
    if radio_modem==GFSK_NAME:
        require_gfsk_bindings();require(unit=='unit-a','gfsk_USB_unit_not_reviewed')
        from .profiles import GFSK_FIRMWARE
        firmware=GFSK_FIRMWARE
    elif radio_modem==GFSK250_NAME:
        require(unit=='unit-a','gfsk250_USB_unit_not_reviewed')
        if mcu_artifact_selector is None:
            require_gfsk250_bindings(qualification_kind)
            from .profiles import GFSK250_FIRMWARE
            firmware=GFSK250_FIRMWARE
        else:
            from .mcu_artifact import artifact, require_selector_scope
            selected=artifact(mcu_artifact_selector);require_selector_scope(qualification_kind)
            firmware=selected['firmware']
    require(mcu_artifact_selector is None or radio_modem==GFSK250_NAME,
            'mcu_artifact_wrong_modem')
    serial=USB[unit]
    channel=ExclusiveSerial('/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_'+serial+'-if00',usb_serial=serial)
    try:
        worker_type={None:SerialWorker,GFSK_NAME:GfskSerialWorker,GFSK250_NAME:Gfsk250SerialWorker}[radio_modem]
        worker=worker_type(channel)
        identity=worker.discover(expected_board_id=1,expected_version=firmware)
        require(identity.boot[:8]==bytes(2)+bytes.fromhex(serial.replace(':',''))[::-1],'wrong_radio_efuse_identity')
        counters=_disarmed_status(worker,identity)
        status=dict(firmware=identity.version,mcu_boot=identity.boot.hex(),usb_serial=serial,disarmed=True,**counters)
        return channel,worker,status
    except BaseException:
        channel.close();raise


def inspect(unit,*,radio_modem=None,qualification_kind=None,mcu_artifact_selector=None):
    if mcu_artifact_selector is not None:
        from .mcu_artifact import KIND,artifact
        artifact(mcu_artifact_selector)
        require(unit=='unit-a' and radio_modem==GFSK250_NAME and qualification_kind==KIND,
                'mcu_inspection_original_selection_required')
    import cryptography
    before = roles(unit)
    result = {'unit': unit, 'host_boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'cryptography': cryptography.__version__, 'services_before': before,
              'radio_opened': False, 'arm_commands': 0, 'tx_commands': 0}
    if unit in USB:
        channel, worker, status = (_serial(unit) if radio_modem is None else
            _serial(unit,radio_modem=radio_modem) if qualification_kind is None else
            _serial(unit,radio_modem=radio_modem,qualification_kind=qualification_kind,
                    mcu_artifact_selector=mcu_artifact_selector))
        try:
            result.update(radio=status,radio_opened=True)
            if radio_modem==GFSK250_NAME:
                require(type(worker) is Gfsk250SerialWorker,'gfsk250_inspection_worker_changed')
                result['radio_capability']=dict(verified=True,radio_modem=GFSK250_NAME,record_version=2,
                    firmware=worker.identity.version,mcu_boot=worker.identity.boot.hex())
        finally: channel.close()
    result['services_after'] = roles(unit)
    return result


def _read_payload(path, descriptor, *, maximum=16384):
    require(type(maximum) is int and maximum in (16384,32768), 'invalid_public_object_limit')
    fd = _open(path)
    try:
        data = os.read(fd, maximum + 1)
    finally:
        os.close(fd)
    require(len(data) == descriptor['bytes'] and hashlib.sha256(data).hexdigest() == descriptor['sha256'],
            'sender_chunk_does_not_match_grant')
    return data


def _sender_receive_ready(runner, flow, worker, authority, parent_current):
    """One actual owned receive pass before advertising sender readiness."""
    require(flow.sends is True and flow.worker is worker and flow.authority is authority
            and runner.transfer.flow is flow and flow.journal is worker.journal
            and flow.flow._peer_credit == 0,
            'pilot_sender_receive_owner_changed')
    require(parent_current() and authority.live() and not worker.fenced,
            'pilot_sender_receive_authority_not_live')
    before = flow.journal.snapshot()['attempts']
    # The frozen flow begins at zero peer credit. This pass arms/listens through
    # the real worker and authenticates ingress; it cannot infer peer capacity.
    complete = runner.step()
    require(parent_current() and authority.live() and flow.worker is worker
            and flow.authority is authority and runner.transfer.flow is flow
            and flow.journal is worker.journal and not worker.fenced,
            'pilot_sender_receive_owner_or_authority_changed')
    require(complete is False and flow.journal.snapshot()['attempts']==before
            and not flow.counts.get('tx_RF_DONE',0) and not flow.counts.get('tx_UNKNOWN',0),
            'pilot_sender_dispatched_before_receive_readiness')
    return True


def _sender_readiness_pass(enabled, runner, flow, worker, authority, parent_current):
    """Default callers retain their original loop, including already queued ACKs."""
    require(type(enabled) is bool, 'invalid_native_sender_receive_ready_mode')
    if not enabled: return False
    require(authority.sends is True and (authority.public32 is True or _fast_profile(authority.profile)),
            'sender_receive_ready_requires_public32_sender')
    return _sender_receive_ready(runner, flow, worker, authority, parent_current)


def _fast_profile(profile):
    if type(profile) in (GfskProfile,Gfsk250Profile):
        profile.encode(board_id=1)
        return profile.frequency_hz==921375000 and profile.chip_power_dbm==0
    return (profile.frequency_hz, profile.bandwidth_hz, profile.spreading_factor,
            profile.coding_rate, profile.chip_power_dbm, profile.preamble,
            profile.sync_word) == (921375000, 500000, 5, 5, 0, 12, 0x42)


def _timed_parent_check(check,*,monotonic_ns=time.monotonic_ns):
    """Observe every original check, including exceptions; never cache approval."""
    timing=dict(clock_basis='host_monotonic_ns',count=0,total_elapsed_us=0,max_elapsed_us=0)
    def measured():
        started=monotonic_ns()
        try:return check()
        finally:
            elapsed=max(0,(monotonic_ns()-started)//1000)
            timing['count']+=1;timing['total_elapsed_us']+=elapsed
            timing['max_elapsed_us']=max(timing['max_elapsed_us'],elapsed)
    return measured,timing


def run(unit, directory, object_directory):
    return _run(unit, directory, object_directory, public_service=False)


def run_public_service(unit, directory, *, sender_receive_ready=False,native_phase_timing=False,
                       mcu_artifact_selector=None):
    """Finite public MR1 service flow; private native provider stays unwired.

    Received bytes are quarantined in the root-only run directory. They are
    not a verified knowledge piece, availability advertisement or public seed.
    No service, listener or autonomous forwarding is commissioned by this run.
    """
    return _run(unit, directory, None, public_service=True, sender_receive_ready=sender_receive_ready,
                native_phase_timing=native_phase_timing,mcu_artifact_selector=mcu_artifact_selector)


def _quarantine(directory, data, *, maximum=16384):
    require(type(maximum) is int and maximum in (16384,32768), 'invalid_public_object_limit')
    _private_info(directory.lstat(), directory=True)
    require(type(data) is bytes and 0 < len(data) <= maximum, 'invalid_public_service_capture')
    fd = os.open(directory / 'receiver.bin', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as handle:
            handle.write(data); handle.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _run(unit, directory, object_directory, *, public_service, sender_receive_ready=False,native_phase_timing=False,
         mcu_artifact_selector=None):
    require(mcu_artifact_selector is None or public_service,'mcu_artifact_public_service_only')
    require(type(sender_receive_ready) is bool and (not sender_receive_ready or public_service),
            'invalid_native_sender_receive_ready_mode')
    require(type(native_phase_timing) is bool and (not native_phase_timing or public_service),
            'invalid_native_phase_timing_mode')
    facts_before = None
    if public_service:
        from lantern_meshticulum.ip.node import _facts
        facts_before = _facts(unit)
        before, boot = facts_before['services'], facts_before['host_boot_id']
    else:
        before = roles(unit)
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    directory = Path(directory)
    _private_info(directory.lstat(), directory=True)
    result = {'schema': 'meshticulum.physical-service-run.v1' if public_service else 'meshticulum.physical-run.v1',
              'unit': unit, 'host_boot_id': boot,
              'services_before': before, 'start_epoch_us': clock()[0], 'start_monotonic_us': clock()[1],
              'complete': False, 'measured_energy_joules': None, 'measured_eirp_dbm': None,
              'airtime_basis': 'LoRa source estimate; raw driver completion timings are separate',
              'application_data_channel': 'exclusive local radio only', 'stop_requested': False,
              'native_phase_timing_requested': native_phase_timing,
              'native_phase_timing_enabled': False}
    worker = authority = flow = journal = store = channel = None
    caps = phase_profile = transfer_timing = None
    authority_ref = grant_ref = profile_ref = authority_lifetime = start_binding = None
    runner = None
    saved_handlers = {}
    def stop(signum, _frame): result['stop_requested'] = True
    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            saved_handlers[number] = signal.signal(number, stop)
        def authority_bound():
            if authority_ref is None:
                return authority is None
            try:
                return (authority is authority_ref and authority.grant is grant_ref
                    and authority.profile is profile_ref
                    and hashlib.sha256(json.dumps(authority.plan,sort_keys=True,separators=(',',':'),
                                                 allow_nan=False).encode()).hexdigest()==start_binding[0]
                    and authority.grant.digest.hex()==start_binding[1]
                    and hashlib.sha256(authority.grant.secret).hexdigest()==start_binding[2]
                    and authority.deadlines is authority_lifetime[0]
                    and (authority.application_deadline_us,authority.deadline_us)==authority_lifetime[1:]
                    and authority.application_expires_epoch_us==start_binding[3]
                    and 0<authority.application_deadline_us<=start_binding[4])
            except (TypeError, ValueError, AttributeError):
                return False
        def original_parent_current():
            return (not public_service or (caps is not None and authority_bound()
                    and caps.current() is True and authority_bound()))
        parent_current,result['parent_current_timing']=_timed_parent_check(original_parent_current)
        def owned():
            return parent_current() and channel is not None and channel.lock_fd is not None and all(
                os.fstat(fd) for fd in ([channel.fd] if unit in USB else [channel.spi_fd, channel.lines_fd]))
        if public_service:
            from lantern_meshticulum.bench.pilot_native_caps import open_for_run
            fd = _open(directory / 'grant.json')
            try:
                raw = os.read(fd, 16385)
            finally:
                os.close(fd)
            require(0 < len(raw) <= 16384, 'invalid_grant_size')
            native_plan = json.loads(raw)
            require(native_plan.get('schema') == 'meshticulum.service-run.v1'
                    and type(native_plan.get('service')) is dict
                    and native_plan['service'].get('kind') == 'knowledge'
                    and native_plan['service'].get('format') in ('MR1.request', 'MR1.response'),
                    'private_service_requires_qualified_native_provider')
            if native_plan.get('sender_unit') == unit:
                from meshticulum.bench.services import _shape
                _shape(_read_payload(directory / 'sender.bin', native_plan['object'],
                           maximum=object_limit(native_plan['service'])), native_plan['service']['format'],
                       public32=public32_service(native_plan['service']),
                       mb1_descriptor_sha256=public_mb1_service(native_plan['service']))
            fd = _open(directory / 'secret.bin')
            try:
                secret = os.read(fd, 33)
            finally:
                os.close(fd)
            require(len(secret) == 32, 'invalid_pilot_secret')
            # Consume the independently prepared original claim before the
            # core authority creates started, or any job/store/driver opens.
            caps = open_for_run(directory.parent.parent, directory, facts_before, clock)
            result['parent'] = caps.consume_start(native_plan['session_id'], native_plan, secret)
            from .mcu_artifact import META_SCHEMA, validate_metadata
            metadata=result['parent']['child_caps']['metadata']
            selected=validate_metadata(metadata) if metadata.get('schema')==META_SCHEMA else None
            require((selected['selector'] if selected is not None and unit=='unit-a' else None)
                ==mcu_artifact_selector,'mcu_artifact_original_claim_mismatch')
            if selected is not None:
                require(caps.public32_owner_current() is True and caps._approve.mcu_artifact==selected,
                        'mcu_artifact_original_owner_changed')
            if native_phase_timing:
                from .phase_timing import enable
                phase_profile=enable(caps)
                result['native_phase_timing_enabled']=True
            start_binding=tuple(result['parent'][name] for name in ('native_plan_sha256','grant_context_sha256',
                'secret_sha256','application_expires_epoch_us','application_deadline_monotonic_us'))
            require(parent_current(), 'pilot_parent_not_current')
            result['facts_before'] = facts_before
        # Convert the OS-owner observation to an exact bool, never a caller flag.
        selected_profile=profile_from_plan(native_plan,board_id=1) if public_service else None
        if public_service and native_plan.get('radio_modem')==GFSK_NAME:require_gfsk_bindings()
        if public_service and native_plan.get('radio_modem')==GFSK250_NAME:
            if selected is not None:
                # Both ends carry the original selected claim; only A has a
                # local firmware selector. C must check that same MCU scope.
                from .mcu_artifact import require_selector_scope
                require_selector_scope(caps.policy.qualification_kind)
            else:
                require_gfsk250_bindings(caps.policy.qualification_kind)
        authority = RunAuthority(directory, radio_admission=admission_for(selected_profile) if public_service else RADIO_ADMISSION, expected_unit=unit, host_boot_id=boot,
                                 clock=clock, owner_probe=lambda: bool(owned()))
        if public_service:
            authority_ref, grant_ref, profile_ref = authority, authority.grant, authority.profile
            authority_lifetime=(authority.deadlines,authority.application_deadline_us,authority.deadline_us)
            require(parent_current(), 'pilot_parent_authority_binding_changed')
            from meshticulum.contracts.identity import ServiceKind
            from meshticulum.bench.services import _shape
            require(authority.plan['schema'] == 'meshticulum.service-run.v1'
                    and authority.service_kind is ServiceKind.KNOWLEDGE
                    and authority.service_format in ('MR1.request', 'MR1.response'),
                    'private_service_requires_qualified_native_provider')
            result.update(service_kind=authority.service_kind.value, service_format=authority.service_format,
                          publisher_verified=False, private_admission=False,
                          original_application_deadline_us=authority.application_deadline_us,
                          original_application_expires_epoch_us=authority.application_expires_epoch_us)
        else:
            # Legacy random bench objects never consume service records.
            require(authority.plan['schema'] == 'meshticulum.bench-run.v1',
                    'service_requires_dedicated_runner')
        require(not sender_receive_ready or (public_service and authority.sends is True
                and (authority.public32 is True or _fast_profile(authority.profile))),
                'sender_receive_ready_requires_public32_sender')
        grant, profile = authority.grant, authority.profile
        if type(profile) in (GfskProfile,Gfsk250Profile):
            result['radio_modem']=modem_for(profile)
            result['airtime_basis']='GFSK source estimate; raw driver completion timings are separate'
        result.update(grant_context_sha256=grant.digest.hex(), session_id=grant.session.session_id.hex(),
                      object=authority.plan['object'], sends=authority.sends)
        payload = _read_payload(directory / 'sender.bin', authority.plan['object'],
            maximum=object_limit(authority.plan.get('service',{}))) if authority.sends else None
        if public_service and payload is not None:
            _shape(payload, authority.service_format, public32=authority.public32,
                   mb1_descriptor_sha256=public_mb1_service(authority.plan['service']))
        require(authority.sends or not (directory / 'sender.bin').exists(), 'receiver_has_ip_payload')
        if public_service:
            from lantern_meshticulum.bench.pilot_job import record
            result['native_job'] = record(directory, unit=unit, boot=boot,
                run_id=grant.session.session_id.hex(), grant_digest=grant.digest.hex(), transport='lora')
        # The fixed paths contain only bench state. No system or personal store
        # is queried as an identity/key source.
        require(parent_current(), 'pilot_parent_not_current')
        store = SessionStore(directory / 'wire.sqlite', grant, authority.local_peer,
                             boot_id=boot, clock=clock, create=True)
        require(parent_current(), 'pilot_parent_not_current')
        if unit in USB:
            channel,worker,radio=(_serial(unit,radio_modem=modem_for(profile),qualification_kind=caps.policy.qualification_kind,
                                        mcu_artifact_selector=mcu_artifact_selector)
                                 if mcu_artifact_selector is not None else
                                 _serial(unit,radio_modem=modem_for(profile),qualification_kind=caps.policy.qualification_kind)
                                 if public_service and caps.policy.qualification_kind=='c-gfsk250-mb1-fast-bundle100k-v1' else
                                 _serial(unit,radio_modem=modem_for(profile))
                                 if type(profile) in (GfskProfile,Gfsk250Profile) else _serial(unit))
            require(parent_current(), 'pilot_parent_not_current')
            result['radio'] = radio
            require(radio['boot_attempts'] < profile.max_boot_attempts
                    and radio['boot_reserved_estimated_airtime_us'] < profile.max_boot_estimated_airtime_us,
                    'mcu_boot_budget_already_exhausted')
            binding = serial_binding(grant.digest, worker.identity, profile)
        else:
            channel = LinuxHAT()
            require(parent_current(), 'pilot_parent_not_current')
            binding = hat_binding(grant.digest, boot, profile)
            result['radio'] = {'model': 'Waveshare SX1262 HF', 'spi': '/dev/spidev0.0'}
        journal = AttemptJournal(directory / 'attempts.sqlite', binding=binding,
            max_attempts=profile.max_boot_attempts, max_airtime_us=profile.max_boot_estimated_airtime_us,
            create=True)
        deadline = authority.deadline_us / 1000000
        require(parent_current() and authority.live(), 'pilot_parent_or_grant_not_current_before_arm')
        if unit in USB:
            worker.arm(journal=journal, profile=profile, context_digest=grant.digest,
                       authorize=lambda: parent_current() and authority.live() and parent_current(), authority_deadline=deadline)
            if getattr(worker, 'arm_lease_ms', None) is not None:
                result['radio_arm'] = dict(clock_basis='host_monotonic',
                    admitted_lease_ms=profile.lease_ms, actual_lease_ms=worker.arm_lease_ms,
                    confirmed_monotonic=worker.arm_confirmed_monotonic,
                    firmware_lease_end_upper_bound=worker.arm_lease_end_upper_bound,
                    host_lease_end=worker.armed_until, original_authority_deadline=deadline)
        else:
            worker_type=(Gfsk250HATWorker if type(profile) is Gfsk250Profile
                         else GfskHATWorker if type(profile) is GfskProfile else HATWorker)
            worker = worker_type(channel, journal=journal, profile=profile, context_digest=grant.digest,
                authorize=lambda: parent_current() and authority.live() and parent_current(), authority_deadline=deadline,
                antenna_confirmed=authority.plan['antenna_confirmed'])
        require(parent_current(), 'pilot_parent_not_current')
        now = clock()[1]
        ledger = Ledger(BudgetPolicy(240000000, 60000000, 120000000, 1000000, quantization_us=1),
                        authority.ledger_epoch, now)
        flow = BenchFlow(authority=authority, worker=worker, cipher=SessionCipher(grant, authority.local_peer, store),
            journal=journal, flow_key=authority.flow_key, contract=authority.contract,
            profile=authority.carrier_profile, ledger=ledger, scheduler_epoch=authority.scheduler_epoch,
            broker_epoch=authority.broker_epoch, sends=authority.sends, clock=clock)
        if phase_profile is not None:
            from .transfer_timing import install
            transfer_timing = install(flow, phase_profile)
        if public_service:
            from meshticulum.bench.services import ServiceTransfer
            from meshticulum.bench.service_runner import ServiceRunner
            runner = ServiceRunner(ServiceTransfer(flow), payload=payload)
            runner.start()
            from lantern_meshticulum.bench.pilot_job import ready
            receive_checked = _sender_readiness_pass(sender_receive_ready, runner, flow, worker, authority, parent_current)
            with authority.application_lease():
                require(parent_current(), 'pilot_parent_not_current_before_ready')
                ready(directory, result['native_job'], radio_receive_checked=receive_checked)
            if transfer_timing is not None:
                transfer_timing.mark('ready_complete')
        elif payload is not None:
            flow.send(payload)
        from .profiles import GFSK250_FAST_BUNDLE_KIND
        fast_completion = public_service and caps.policy.qualification_kind == GFSK250_FAST_BUNDLE_KIND
        complete_at = None
        while not result['stop_requested']:
            now = clock()[1]
            require(now / 1000000 < authority.deadlines.application_deadline_monotonic,
                    'bench_application_deadline')
            if (runner.step() if runner is not None else flow.step()):
                if complete_at is None:
                    complete_at = clock()[1]
                    result['flow_complete_monotonic_us'] = complete_at
                    if transfer_timing is not None:
                        transfer_timing.mark('flow_complete_observed')
                    if fast_completion and authority.sends:
                        from .fast_bundle import final_ack_proof
                        with authority.application_lease():
                            require(parent_current(), 'fast_completion_parent_not_current')
                            result['fast_final_ack_proof'] = final_ack_proof(authority,caps,runner)
                    if not authority.sends:
                        if runner is not None:
                            data = runner.public_payload
                            _quarantine(directory, data, maximum=object_limit(authority.plan['service']))
                            result['service_object_sha256'] = hashlib.sha256(data).hexdigest()
                            result['service_object_durable'] = True
                            result['application_status'] = 'public_block_quarantined'
                        else:
                            data = bytes(flow.received)
                            VerifiedChunks(object_directory).commit(authority.plan['object'], data)
                            result['verified_chunk_sha256'] = hashlib.sha256(data).hexdigest()
                            result['chunk_durable'] = True
                # Receiver stays available to re-ACK a lost final receipt.
                if fast_completion and not authority.sends:
                    from .fast_bundle import receiver_completed
                    with authority.application_lease():
                        require(parent_current(), 'fast_completion_parent_not_current')
                        completed = receiver_completed(authority,caps,directory)
                    if completed:
                        result['fast_final_ack_verified'] = True
                        result['complete'] = True
                        break
                elif authority.sends or clock()[1] - complete_at > 20000000:
                    result['complete'] = True
                    break
            time.sleep(.002 if radio_is_fast(profile) else .01)
        result['flow'] = flow.snapshot()
        if runner is not None:
            result['service_runner'] = runner.snapshot()
    except BaseException as error:
        result['error'] = type(error).__name__ + ':' + str(error)[:256]
        if flow is not None:
            result['flow'] = flow.snapshot()
    finally:
        def cleanup(name, action):
            try:
                action()
            except BaseException as error:
                result.setdefault('cleanup_errors', {})[name] = type(error).__name__
                result['complete'] = False
        if transfer_timing is not None:
            cleanup('transfer_timing_cleanup_begin', lambda: transfer_timing.mark('cleanup_begin'))
        if runner is not None:
            cleanup('service_runner', runner.close)
        elif flow is not None:
            cleanup('flow', flow.close)
        if worker is not None and worker.journal is not None:
            try:
                worker.disarm(); result['disarmed_on_exit'] = True
            except BaseException as error:
                result['disarmed_on_exit'] = False
                result['containment_error'] = type(error).__name__
                result['complete'] = False
        if store is not None:
            cleanup('retire_wire', store.retire)
            cleanup('close_wire', store.close)
        if journal is not None:
            cleanup('journal_snapshot', lambda: result.update(attempt_journal=journal.snapshot()))
            cleanup('journal_close', journal.close)
        if channel is not None: cleanup('radio_close', channel.close)
        if authority is not None: cleanup('authority_close', authority.close)
        if caps is not None:
            cleanup('parent_snapshot', lambda: result.update(parent_caps=caps.snapshot()))
            cleanup('parent_close', caps.close)
        if transfer_timing is not None:
            cleanup('transfer_timing_cleanup_end', lambda: transfer_timing.mark('cleanup_end'))
            cleanup('transfer_timing', lambda: result.update(native_transfer_timing=transfer_timing.snapshot()))
        if phase_profile is not None:
            cleanup('phase_timing',lambda:result.update(native_phase_timing=phase_profile.snapshot()))
        for number, handler in saved_handlers.items(): signal.signal(number, handler)
        result['end_monotonic_us'] = clock()[1]
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result['resource'] = {'user_cpu_seconds': usage.ru_utime, 'system_cpu_seconds': usage.ru_stime,
                              'max_rss_linux_kib': usage.ru_maxrss}
        if public_service:
            cleanup('facts_after', lambda: result.update(facts_after=_facts(unit)))
            if result.get('facts_after') != facts_before:
                result['complete'] = False
                result['lifecycle_changed'] = True
            result['services_after'] = result.get('facts_after', {}).get('services')
        else:
            result['services_after'] = roles(unit)
        fd = os.open(directory / 'result.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(result, output, indent=2); output.write('\n'); output.flush(); os.fsync(output.fileno())
    return result
