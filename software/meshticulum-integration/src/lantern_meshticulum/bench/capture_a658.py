"""Exact unadmitted MB1 capture; no paid parent, run, grant or RF debt.

Independent native observations pin the C/B metadata and original-expiry
proof. The existing H35 paid roster is unchanged.
"""
import re
from .history_metadata_fence import _need

PARENT='a658ae21c653c6f5cadfaa1b28b57498'
REVIEW_SHA256='893f8f3a789b7e46c7cb6be721d77e37e3b7fc498237eedc631df26718974605'
RESULT_SHA256='74ba244f7a37027d00d6e7a60ee411b9ea47f0e1b1412fe47ab402bf7c1ba621'
H35_RECEIPT_SHA256='37d961c7e5078fb98a5c4e3e4b31b7aac51d0afa1433966a56b47c00d2de36ca'
START=1791839647891967
END=1791841002891967
WHOLE_END=1791840997891967
EXPIRY_COMPLETION_SHA256='ac43ad90fdb5aab5c1e544ee5c8063bef76ce390af4e0dfb8555b65167ccf295'
UNITS={'unit-b': {'files': {'capture.json': '5a6aa8ac74585b5bcb3a916d4eb8d66004f0b6e3dd8a9e751dc8dd7ed11fcbd5', 'history-binding.json': 'a103270076f18b471cd49c1a50ebd9cba005d7a7dae9f7e2ef9348d3eec6fce6', 'history-identity-seal.json': 'd27c75beea1262312155bfed5e7bf09f881ae7706979ff9f84f94568d1433dc4', 'legacy-grant-seal.json': '95539e6026afa5b68d01686e15c50661b2d3d1cca5079957d3c682e69f05859a'}, 'boot': 'dfe04f99-f765-45f0-aba7-27b8eaad902b', 'phase': {'deadline_monotonic_us': 300306362117, 'epoch_us': 1791839726585180, 'monotonic_us': 299030055330}, 'history_sha256': 'd246675ab8019685be03ef77d06cccc3aeda8fcf49ff6dc8f7a9dde35bf8c743'}, 'unit-c': {'files': {'capture.json': '885870e4199e276aaee9373369be8df5bdb008bb931a1bcf32a139abb45d3dd3', 'history-binding.json': '8b1c895523d0c7a34bc42ad6a67e7c04f0d8347dc198e3a800e2eca1534e9f15', 'history-identity-seal.json': '46609cd190616792d31ca07bcdd32a2a65869ec55bbffea68f6fb73e87309c1a', 'legacy-grant-seal.json': '7fa4b98f711e9a045c93ac5a1c864fe726ad414d4d8b502485ef31e8028f9cdc'}, 'boot': '21f9c178-6090-40d9-9359-ecf1c7b950b1', 'phase': {'deadline_monotonic_us': 297295611687, 'epoch_us': 1791839656752759, 'monotonic_us': 295949472479}, 'history_sha256': '5c8e6bb7a2005e02e52fdf70401c6237d131af79cf656a9edb681b8a3aa5c7c0'}}


def row(unit):
    """A never reached capture; C/B require exact independently observed pins."""
    _need(unit in ('unit-a','unit-b','unit-c'),'invalid_a658_unit')
    if unit=='unit-a':return None
    value=UNITS[unit]
    _need(type(EXPIRY_COMPLETION_SHA256) is str and re.fullmatch('[0-9a-f]{64}',EXPIRY_COMPLETION_SHA256)
        and type(value['files']) is dict
        and set(value['files'])=={'capture.json','history-binding.json','history-identity-seal.json','legacy-grant-seal.json'}
        and all(type(pin) is str and re.fullmatch('[0-9a-f]{64}',pin) for pin in value['files'].values())
        and type(value['phase']) is dict,'pending_a658_capture_only_original_expiry')
    return value
