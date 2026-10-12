"""Separate finite native100k MB1 scope; no device or issuance entry."""
from types import MappingProxyType

from meshticulum.bench.wire import require
from .profiles import GFSK_MB1_BUNDLE_KIND,GFSK_NAME,profile_for,profile_record

# Root must bind a reviewed future scope, paired source/runtime and actual
# retained history. A source test or the legacy JSON scope cannot fill these.
PHYSICAL_BINDINGS=MappingProxyType(dict(scope_sha256='b71f247250d23411a5ccd4e9a6dbc37620e740aef4ecb3f60198d7c0c8b0f511',
    core_commit='002694c3a7ad3b402891029ded0c82fabecde691',
    history_receipt_sha256='37d961c7e5078fb98a5c4e3e4b31b7aac51d0afa1433966a56b47c00d2de36ca',
    preceding_parent='73c1cda8c02939409d0b1e7e2a39be67'))


def require_bindings():
    from .retained_capture import PAID_HISTORY
    value=dict(PHYSICAL_BINDINGS)
    require(all(type(value[k]) is str and len(value[k])==64 and all(c in '0123456789abcdef' for c in value[k])
        for k in ('scope_sha256','history_receipt_sha256'))
        and type(value['core_commit']) is str and len(value['core_commit'])==40
        and all(c in '0123456789abcdef' for c in value['core_commit'])
        and value['preceding_parent']==PAID_HISTORY[-1], 'pending_native_mb1_scope_source_and_history')
    return value


def require_owner(owner,policy):
    from .pilot_bundle import BundleContext
    pins=require_bindings()
    require(policy.qualification_kind==GFSK_MB1_BUNDLE_KIND
        and owner.get('qualification_kind')==GFSK_MB1_BUNDLE_KIND
        and owner.get('radio_profile_name')==owner.get('radio_modem')==GFSK_NAME
        and owner.get('mb1_bundle_scope_sha256')==pins['scope_sha256']
        and owner.get('core_commit')==pins['core_commit']
        # Runtime/manifest are caller-supplied immutable owner bytes. The
        # native Runtime/Reader and the operator's exact archive/manifest
        # checks authenticate them; source cannot contain its own digest.
        and all(type(owner.get(k)) is str and len(owner[k])==64
            and all(c in '0123456789abcdef' for c in owner[k])
            for k in ('runtime_sha256','manifest_sha256'))
        and owner.get('history_receipt_sha256')==pins['history_receipt_sha256']
        and owner['rf_profile']==profile_record(profile_for(GFSK_NAME)), 'mb1_bundle_owner_changed')
    context=BundleContext(owner['bundle_context'],policy)
    require(context.transport is not None,'mb1_bundle_transport_context_required')
    return context
