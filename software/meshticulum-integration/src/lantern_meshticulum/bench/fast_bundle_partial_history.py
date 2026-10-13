"""H41 first-pair retention with actual expired native metadata."""
import hashlib
import sys,os
from functools import partial
from . import gfsk250_mb1_partial_history as shared
from .fast_bundle_unstarted_history import H39_PREFIX,PARENT as H40_LAST,stage_files
from .profiles import GFSK250_FAST_BUNDLE_KIND as QUALIFICATION_KIND
from meshticulum.bench.wire import require

PARENT='1d4359b68baf1d8572bb33790f96fa21'
POLICY_DIGEST='d570f42d9d3c6dbde1edf0a783de2d5ba3039dc13a54776e4a6f13f34435cc51'
POLICY_SHA256='fb94092df9f750ed4fb7451b43cb3bc089f81c8d39238e8500a7440d9a358e2a'
OWNER_SHA256={'unit-a': '1156f01e7e35349ef651c9a3c814e1506acc523dfd4e7bdc9a9eb12c9e39f465', 'unit-b': '43d498f2abe34fdf4d80009b4e4d2c56873ee80b47e491e6b085fa20d5a942fd', 'unit-c': '1c97ab0973efecd96c21b8240e8a8782ac48e0eed7667c3fc83ce39829308957'}
ORIGINAL_SCOPE_SHA256='710acdfab4492efcc478318114fa8c2404733efbb393b0108566e3556df3646b'
ORIGINAL_RUNTIME_SHA256='0726499132dd9e03da7902d8261e5d01fbb163784d06903774cd1cff67e08db5'
ORIGINAL_MANIFEST_SHA256='14c7e724482822233b63caa25e2b36bf1c45bd5365d2767b8bcd29731d49315a'
ORIGINAL_PHASE_END=1791871985690391
ORIGINAL_ABSOLUTE_END=1791872097928892
ORIGINAL_APPLICATION_END=1791871000744432
FINAL_RESULT_SHA256='22ba72fe5d75874b1e0800443cc6b55fc3b4a08e1afa86d9d444726f7db68b2f'
EXPIRY_COMPLETION_SHA256='4d77000cae9eecbafd22e95abf418d55c87c46e5cb6d57569d74a6ffbd08181a'
NEXT_HISTORY_RECEIPT_SHA256='cecd4d2ac76daa094e4234d4bfbc15b0c23acbd01a52da5a4d3ea15e62c1f2bc'
NATIVE_DATABASE_SHA256={'unit-a': 'e95f75fd9c48e1262adccc97c846d0f150bf432f377ee14e80f52e27be7611f9', 'unit-b': '59139190a9a23b4d9999fbb59efecc0196e76f54c739829613e54d125283d5e3', 'unit-c': 'ca7d5757aef16ce98e2ce8f3fcf5378d94ac7dce483d8c6095060fa37b783087'}
NATIVE_OUTCOME_SHA256={'unit-a': {'7b485ffdf147ca470e28bae31a5fb845': 'deb03e33b4f61008344335c31b158329d6c93d4ef110e16d9b54fe16c361c5f0', '9913129134d9d676b7d8768284f9cfff': 'ba7148652ce076adf827614afc5df4a5659f037417b5017b95381e23ecb14553'}, 'unit-b': {}, 'unit-c': {'7b485ffdf147ca470e28bae31a5fb845': '99d95a0dd6131935a5e50337befa78fbba22411e223eda5c3c1933789c61317f', '9913129134d9d676b7d8768284f9cfff': 'dd0da12e6e31520a0329952f12df784305040cd76c52819503eb840ea658599d'}}
FAST_STAGE_SHA256={'unit-a': 'ae237bf4eb1adf888f322eb76b08045a5edc8cf6e3c34eaf116e31c771ccffd5', 'unit-b': None, 'unit-c': '4ae2d0e05b047f355ee6ebf92f118af7d4829063e4036d89bb54a041fb6aba16'}
FINAL_ACK_SHA256={'unit-a': {'9913129134d9d676b7d8768284f9cfff': '5903a70ca3e73a4775e9ec4ae291b89d6c24de7655eee4017567cd6583ca24b3'}, 'unit-b': {}, 'unit-c': {'7b485ffdf147ca470e28bae31a5fb845': 'f4eee606624f09516a3d6ba55e559b85ada4d4e703409eeb12862f3858178cea'}}
VERIFIED_PIECE_SHA256={'unit-a': {}, 'unit-b': {}, 'unit-c': {}}
FAILED_STARTED_RUNS={'unit-a': [], 'unit-b': [], 'unit-c': []}
H40_PREFIX=H39_PREFIX+(H40_LAST,)
declared_partition=partial(shared._declared_partition,sys.modules[__name__])
original_context=partial(shared._original_context,sys.modules[__name__])
retained_context=partial(shared._retained_context,sys.modules[__name__])


def require_final_bindings():
    shared._require_final_bindings(sys.modules[__name__])
    def pin(v):return type(v) is str and len(v)==64 and all(c in '0123456789abcdef' for c in v)
    require(set(FAST_STAGE_SHA256)==set(FINAL_ACK_SHA256)==set(NATIVE_OUTCOME_SHA256)
        and FAST_STAGE_SHA256['unit-b'] is None and FINAL_ACK_SHA256['unit-b']=={}
        and all(type(v) is dict for v in FINAL_ACK_SHA256.values())
        and all(pin(FAST_STAGE_SHA256[u]) and set(FINAL_ACK_SHA256[u])==({'9913129134d9d676b7d8768284f9cfff'} if u=='unit-a' else {'7b485ffdf147ca470e28bae31a5fb845'})
            and all(pin(v) for v in FINAL_ACK_SHA256[u].values()) for u in ('unit-a','unit-c')),
        'pending_1d43_stage_and_final_ack_metadata')
    require(set(VERIFIED_PIECE_SHA256)==set(FINAL_ACK_SHA256)
        and all(type(v) is dict and set(v)<=({'9913129134d9d676b7d8768284f9cfff'} if u=='unit-a' else set())
            and all(pin(p) for p in v.values()) for u,v in VERIFIED_PIECE_SHA256.items()),
        'pending_1d43_optional_verified_piece_metadata')
    return True


def pending_next_roster():
    from .retained_capture import PAID_HISTORY
    require_final_bindings()
    require(len(PAID_HISTORY)>=41 and PAID_HISTORY[:40]==H40_PREFIX and PAID_HISTORY[40]==PARENT,
        '1d43_H40_prefix_changed')
    return PAID_HISTORY[:41]


def metadata_files(reader,state,unit,value):
    require_final_bindings()
    files=stage_files(reader,state,unit,value,contract=sys.modules[__name__])
    for field,pins,name in (('fast_final_ack_sha256',FINAL_ACK_SHA256,'fast-final-ack.json'),
                            ('verified_piece_sha256',VERIFIED_PIECE_SHA256,'verified-piece.json')):
        require(value[field]==pins[unit],'1d43_public_metadata_scope_changed')
        for run in NATIVE_OUTCOME_SHA256[unit]:
            path=state/'runs'/run/name
            if run not in pins[unit]:require(not os.path.lexists(path),'1d43_unexpected_public_metadata');continue
            raw=reader.read(path,100000)
            require(hashlib.sha256(raw).hexdigest()==pins[unit][run],'1d43_public_metadata_changed')
            files[str(path.relative_to(state))]=pins[unit][run]
    reader.check();return files
