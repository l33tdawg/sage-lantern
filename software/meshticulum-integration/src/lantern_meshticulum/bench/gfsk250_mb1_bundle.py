"""Distinct finite GFSK250 MB1 scope, pending actual history/device binding."""
from meshticulum.bench.wire import require
from types import MappingProxyType
from .profiles import (GFSK250_MB1_BUNDLE_KIND,GFSK250_NAME,GFSK250_CORE_COMMIT,
    GFSK250_FIRMWARE,GFSK250_FIRMWARE_SHA256,profile_for,profile_record)

PHYSICAL_BINDINGS=MappingProxyType(dict(scope_sha256=None,core_commit=GFSK250_CORE_COMMIT,
    history_receipt_sha256=None,preceding_parent=None))


def require_bindings():
    from .retained_capture import PAID_HISTORY
    pins=dict(PHYSICAL_BINDINGS)
    require(set(pins)=={'scope_sha256','core_commit','history_receipt_sha256','preceding_parent'}
        and all(type(pins.get(k)) is str and len(pins[k])==64
            and all(c in '0123456789abcdef' for c in pins[k])
            for k in ('scope_sha256','history_receipt_sha256'))
        and pins['core_commit']==GFSK250_CORE_COMMIT
        and pins['preceding_parent']==PAID_HISTORY[-1]
        and type(GFSK250_FIRMWARE) is str and len(GFSK250_FIRMWARE)==15
        and GFSK250_FIRMWARE.startswith('mtb1-')
        and all(c in '0123456789abcdef' for c in GFSK250_FIRMWARE[5:])
        and type(GFSK250_FIRMWARE_SHA256) is str and len(GFSK250_FIRMWARE_SHA256)==64
        and all(c in '0123456789abcdef' for c in GFSK250_FIRMWARE_SHA256),
        'pending_gfsk250_native_scope_source_and_history_bindings')
    return pins


def require_owner(owner,policy):
    from .pilot_bundle import BundleContext
    pins=require_bindings()
    require(policy.qualification_kind==GFSK250_MB1_BUNDLE_KIND
        and owner.get('qualification_kind')==GFSK250_MB1_BUNDLE_KIND
        and owner.get('radio_profile_name')==owner.get('radio_modem')==GFSK250_NAME
        and owner.get('mb1_bundle_scope_sha256')==pins['scope_sha256']
        and owner.get('core_commit')==pins['core_commit']
        and owner.get('history_receipt_sha256')==pins['history_receipt_sha256']
        and all(type(owner.get(k)) is str and len(owner[k])==64
            and all(c in '0123456789abcdef' for c in owner[k])
            for k in ('runtime_sha256','manifest_sha256'))
        and owner['rf_profile']==profile_record(profile_for(GFSK250_NAME)),
        'gfsk250_mb1_bundle_owner_changed')
    # Runtime/manifest remain external immutable owner pins, verified by the
    # existing Runtime/Reader. No included source can contain its own hash.
    context=BundleContext(owner['bundle_context'],policy)
    require(context.transport is not None,'mb1_bundle_transport_context_required')
    return context
