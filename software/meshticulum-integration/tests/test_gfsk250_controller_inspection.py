"""Controller regression for the actual first250 inspection; no device access."""
import copy
import importlib.util
from pathlib import Path
import sys

import pytest

BENCH=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
sys.path.insert(0,str(BENCH))
spec=importlib.util.spec_from_file_location('gfsk250_controller_inspection',BENCH/'run_public_gfsk250_mb1_bundle100k.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
FIRMWARE='mtb1-def16514fc'
BOOT='000008f6a51b5bf80000000000000031'
EXPECTED=dict(mcu_boot=BOOT,boot_attempts=0,boot_reserved_estimated_airtime_us=0)
# Relevant protocol fields from the first actual250 inspection. This fixture
# verifies source behavior only; it does not stand in for a native trial.
INSPECTION=dict(schema='meshticulum.native-bench-inspection.v1',unit='unit-a',
    arm_commands=0,tx_commands=0,
    radio=dict(firmware=FIRMWARE,mcu_boot=BOOT,usb_serial='F8:5B:1B:A5:F6:08',
               disarmed=True,boot_attempts=0,boot_reserved_estimated_airtime_us=0),
    radio_capability=dict(firmware=FIRMWARE,mcu_boot=BOOT,radio_modem='gfsk250',record_version=2,verified=True))


def test_actual250_inspection_accepts_and_does_not_mutate():
    value=copy.deepcopy(INSPECTION);expected=dict(EXPECTED)
    assert runner.require_fresh_radio(value,expected,FIRMWARE)==value['radio']
    assert value==INSPECTION and expected==EXPECTED


@pytest.mark.parametrize('section,key,value',[
    ('radio','firmware','mtb1-b433927ba6'),('radio','mcu_boot','0'*32),
    ('radio','usb_serial','wrong-device'),('radio','disarmed',False),('radio','disarmed',1),
    ('radio','boot_attempts',1),('radio','boot_attempts',False),
    ('radio','boot_reserved_estimated_airtime_us',1),('radio','boot_reserved_estimated_airtime_us',False),
    ('radio_capability','firmware','mtb1-b433927ba6'),('radio_capability','mcu_boot','0'*32),
    ('radio_capability','radio_modem','gfsk125'),('radio_capability','record_version',1),
    ('radio_capability','record_version',2.0),('radio_capability','verified',False),
    ('radio_capability','verified',1),(None,'unit','unit-c'),(None,'arm_commands',1),
    (None,'tx_commands',1),(None,'tx_commands',False),(None,'radio_capability',None)])
def test_unsafe_or_legacy_inspection_refuses(section,key,value):
    observed=copy.deepcopy(INSPECTION)
    (observed if section is None else observed[section])[key]=value
    with pytest.raises(RuntimeError):runner.require_fresh_radio(observed,EXPECTED,FIRMWARE)


@pytest.mark.parametrize('change',[dict(boot_attempts=False),dict(boot_attempts=1),
    dict(boot_reserved_estimated_airtime_us=1),dict(mcu_boot='0'*32),dict(extra=0)])
def test_expectation_cannot_accept_used_or_unbound_boot(change):
    with pytest.raises(RuntimeError):runner.require_fresh_radio(INSPECTION,dict(EXPECTED,**change),FIRMWARE)


def test_old_manifest_firmware_cannot_reopen_old125_gate():
    with pytest.raises(RuntimeError):runner.require_fresh_radio(INSPECTION,EXPECTED,'mtb1-b433927ba6')
