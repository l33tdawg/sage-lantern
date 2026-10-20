"""Explicit public100k GFSK scope; no firmware, reset or transport entry."""
from .profiles import GFSK_BUNDLE_KIND, GFSK_NAME, profile_for, profile_record, require_gfsk_bindings
from meshticulum.bench.wire import require

# Root binds the reviewed whole-file scope after actual status/readiness proof.
GFSK_BUNDLE_SCOPE_SHA256 = None
CLEAN_A_TX_MODEL = 225  # Five one-frame requests plus ~44 ACKs per 170-DATA response.


def require_bundle_bindings():
    require_gfsk_bindings()
    pin = GFSK_BUNDLE_SCOPE_SHA256
    require(type(pin) is str and len(pin) == 64 and all(c in '0123456789abcdef' for c in pin),
            'pending_gfsk_whole_bundle_scope')
    return pin


def require_owner(owner, policy):
    from .pilot_bundle import BundleContext
    pin = require_bundle_bindings()
    require(policy.qualification_kind == GFSK_BUNDLE_KIND
            and owner.get('qualification_kind') == GFSK_BUNDLE_KIND
            and owner.get('radio_profile_name') == GFSK_NAME and owner.get('radio_modem') == GFSK_NAME
            and owner.get('gfsk_bundle_scope_sha256') == pin
            and owner['rf_profile'] == profile_record(profile_for(GFSK_NAME)),
            'gfsk_bundle_owner_changed')
    return BundleContext(owner['bundle_context'], policy)


def fresh_boot_status(status):
    """Planning gate for one whole run; actual proof is supplied by Root."""
    require(type(status) is dict and status.get('firmware') == 'mtb1-b433927ba6'
            and status.get('usb_serial') == 'F8:5B:1B:A5:F6:08' and status.get('disarmed') is True
            and type(status.get('boot_attempts')) is int and status['boot_attempts'] == 0
            and type(status.get('boot_reserved_estimated_airtime_us')) is int
            and status['boot_reserved_estimated_airtime_us'] == 0, 'whole_bundle_fresh_boot_required')
    boot = status.get('mcu_boot')
    require(type(boot) is str and len(boot) == 32 and boot.startswith('000008f6a51b5bf8')
            and all(c in '0123456789abcdef' for c in boot), 'whole_bundle_boot_identity_changed')
    return dict(available_attempts=256, clean_A_TX_model=CLEAN_A_TX_MODEL,
                clean_model_spare_attempts=256-CLEAN_A_TX_MODEL, measured=False)
