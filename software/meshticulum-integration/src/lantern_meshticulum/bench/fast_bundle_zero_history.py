"""Original2756 zero-allocation fast scope; historical decoding only.

The final controller result records noRF/allocations. Its full2560/600M perA/C
reservation remains owed. Native database/expiry/history pins stay pending until
Root observes them; no live scope is inferred from these source descriptors.
"""
import hashlib
from dataclasses import asdict
from meshticulum.bench.wire import require
from .pilot_parent_caps import ParentPolicy,Cost,canonical
from .pilot_bundle import BundleContext,public_context_commitment
from .profiles import GFSK250_FAST_BUNDLE_KIND,GFSK250_NAME,profile_for,profile_record
from .retained_capture import PAID_HISTORY

PARENT='27563497b7f6d89526f7249f2bf9005a'
POLICY_DIGEST='0e2675102aa9259d5969af5b592c49728138e07b331ae014735e3c63ce99c315'
POLICY_SHA256='8a7491a9031c6a3fa1c23975ff2e0391c39cc91245f2a777910f150d51e9af76'
OWNER_SHA256={'unit-a': 'ad6e867be2b55cbfca760ec4169e51e4de0cc06f0c4a5bc39e7a4e11609a42f9', 'unit-b': '77ec2e93f65174cebb7927bf7d2ce020136f06cefc14691c400db5eb60e30aa5', 'unit-c': 'fe8c41555bcecf5fd7fbfd3f7f9556af6ae5c911115e63067a2c678e33b1aac2'}
ORIGINAL_SCOPE_SHA256='d4280ec13e6352c0aba0c19978952556c07df495e3f879f1110ef38681c7a7be'
ORIGINAL_RUNTIME_SHA256='4b7d4c3092765c2957455eb2f343ebf82bea2d9da25ee9c9c999360d73225168'
ORIGINAL_MANIFEST_SHA256='853e1f55ff1b9cc699613b0f80bb3a0feb11617b7be61419faf526a50127200e'
ORIGINAL_CORE_COMMIT='a45c051aafd0be061f5b8160687277fe2b5eaca9'
ORIGINAL_PHASE_END=1791861289055042
from .gfsk250_mb1_partial_history import H37_PREFIX,PARENT as H38_LAST
H38_PREFIX=H37_PREFIX+(H38_LAST,)
FINAL_RESULT_SHA256='103b3517aa965840230eb2ec1b609e93a9ccb5c5bd8b64687f8f89ce9f770b03'
EXPIRY_COMPLETION_SHA256='605a2e1b2efc5e9edc668ab04f9698f27b15c4a29a57d24f66f8bb7d2bf1b346'
NEXT_HISTORY_RECEIPT_SHA256='0d9dd9910c9aa7bcb1f890963649e7c850cea8450b6d94ee9be108a3f52fc0c9'
NATIVE_DATABASE_SHA256={
    'unit-a': '7c9d5f22aeb0c99f0890f9f9e5709aaa211f352ca41f8604d574a57a3edefd54',
    'unit-b': '50aac8b7c8404c6fe427dc8279c9ff302e2d9f38c204c025b6674af14bd15005',
    'unit-c': '1a217a08104ede17b7f07cbe30171f5ea9af9acaa84535332b07e750f74ff01f'}

def _sha(value):return hashlib.sha256(canonical(value)).hexdigest()

QUALIFICATION_KIND=GFSK250_FAST_BUNDLE_KIND
ORIGINAL_ABSOLUTE_END=ORIGINAL_PHASE_END
from . import gfsk250_mb1_partial_history as shared
from functools import partial
import sys
declared_partition=partial(shared._declared_partition,sys.modules[__name__])
original_context=partial(shared._original_context,sys.modules[__name__])
retained_context=partial(shared._retained_context,sys.modules[__name__])

def require_final_bindings():
    def pin(value):return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    require(all(pin(v) for v in (FINAL_RESULT_SHA256,EXPIRY_COMPLETION_SHA256,NEXT_HISTORY_RECEIPT_SHA256))
        and set(NATIVE_DATABASE_SHA256)=={'unit-a','unit-b','unit-c'}
        and all(pin(v) for v in NATIVE_DATABASE_SHA256.values()),'pending_2756_actual_database_expiry_history')
    return True


def pending_next_roster():
    require_final_bindings()
    require(len(PAID_HISTORY)>=39 and PAID_HISTORY[:38]==H38_PREFIX and PAID_HISTORY[38]==PARENT,
        '2756_H38_prefix_changed')
    return PAID_HISTORY[:39]
