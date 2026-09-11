"""Explicit root-only gateway role; borrowed owner/store and no identity loader.

The operator supplies the real fixed-loopback bridge, current public radio pins,
unchanged configuration guard and measured timing/lease checks. A legacy 30s
capture lease is insufficient: startup fails unless the root lease provider
positively supports the configured bound. No browser field enables this role.
"""

import copy
import hashlib
import math
import threading

from .community_service import CommunityMeshProvider
from .mesh_community import MeshReceiveConsent
from .public_gateway_bridge import PublicGatewayBridge
from .public_gateway_runtime import PublicGatewayPin, PublicGatewayRuntime
from .public_mesh_protocol import canonical, public_key, require
from .public_mesh_provider import PublicMeshTiming


SCHEMA = "sage.lantern.public-gateway-role.v1"


def validate_gateway_config(value, bridge):
    require(type(bridge) is PublicGatewayBridge and type(value) is dict and set(value) == {
        "schema", "role", "enabled", "endpoint", "chain_id", "domain", "gateway_key", "storage_actor",
        "storage_chain", "pins", "timing", "operation_seconds", "lease_seconds", "poll_interval"})
    require(value["schema"] == SCHEMA and value["role"] == "public_gateway" and type(value["enabled"]) is bool)
    binding = bridge._binding
    require((value["endpoint"], value["chain_id"], value["domain"], value["gateway_key"],
             value["storage_actor"], value["storage_chain"]) == (binding["endpoint"], binding["chain_id"], binding["domain"],
             binding["gateway"], binding["storage_actor"], binding["storage_chain"]))
    require(type(value["pins"]) is list and 1 <= len(value["pins"]) <= 32)
    pins = []
    for pin in value["pins"]:
        require(type(pin) is dict and set(pin) == {"radio_key", "author"})
        public_key(pin["radio_key"])
        public_key(pin["author"])
        require(pin["author"] in bridge._authors)
        pins.append(PublicGatewayPin(bytes.fromhex(pin["radio_key"]), pin["author"]))
    require(len({pin.radio_key[:6] for pin in pins}) == len(pins) and len({pin.author for pin in pins}) == len(pins))
    require(type(value["timing"]) is dict and set(value["timing"]) == {"frame_seconds", "gateway_seconds", "safety_seconds"})
    timing = PublicMeshTiming(**value["timing"])
    timing.validate()
    for key in ("operation_seconds", "lease_seconds", "poll_interval"):
        require(type(value[key]) in (int, float) and math.isfinite(value[key]))
    require(timing.gateway_seconds + timing.frame_seconds + timing.safety_seconds < value["operation_seconds"] <= 60)
    require(value["operation_seconds"] + 5 <= value["lease_seconds"] <= 120)
    require(0.01 <= value["poll_interval"] <= 0.2 and 2 * value["poll_interval"] <= timing.frame_seconds)
    return tuple(pins), timing


class RootPublicGateway:
    def __init__(self, provider, bridge, settings, *, config_check, contact_check,
                 timing_check, lease_budget_check, lease_factory, unlocked_check):
        require(type(provider) is CommunityMeshProvider and provider._runtime is not None)
        require(all(callable(check) for check in (config_check, contact_check, timing_check,
            lease_budget_check, lease_factory, unlocked_check)))
        pins, timing = validate_gateway_config(settings, bridge)
        self.provider, self.runtime = provider, provider._runtime
        require(bridge._workflow is self.runtime.store)
        self.settings = copy.deepcopy(settings)
        self._config, self._timing_check, self._lease_budget = config_check, timing_check, lease_budget_check
        self._lease_factory, self._unlocked, self._contact = lease_factory, unlocked_check, contact_check
        self.operation_timeout = settings["operation_seconds"]
        self.poll_interval = settings["poll_interval"]
        self._revision = hashlib.sha256(canonical(settings)).hexdigest()
        self._mutex = threading.Lock()
        self._started = self._closed = False
        self.gateway = PublicGatewayRuntime(bridge, self.runtime.coordinator, pins, contact_check=self._pin,
            lifecycle_check=self.runtime._storage_lifecycle, timing=timing)

    def _configured(self):
        return (not self._closed and self.settings["enabled"] is True
                and hashlib.sha256(canonical(self.settings)).hexdigest() == self._revision
                and self._config(copy.deepcopy(self.settings)) is True and self.runtime._activation() is True)

    def _pin(self, pin):
        return self._configured() and self._contact(pin) is True

    def _consent(self):
        require(self._configured())
        return MeshReceiveConsent(self.runtime.actor, self._revision, True, False, True, False)

    def _lease(self, consent):
        require(consent == self._consent() and self._lease_budget(self.settings["lease_seconds"]) is True)
        return self._lease_factory(consent)

    def start(self, *, access_check):
        """Attach/start the existing root loop only after real preflight and drain."""
        require(callable(access_check) and self._mutex.acquire(blocking=False))
        try:
            require(not self._started and self._configured() and access_check() is True and self._unlocked() is True)
            require(self.runtime._poller is None and not self.runtime._stop.is_set()
                    and self.runtime._public_mesh is None and not self.runtime._uncertain)
            require(self._timing_check(copy.deepcopy(self.settings)) is True
                    and self._lease_budget(self.settings["lease_seconds"]) is True)
            bridge = self.gateway.bridge
            def check():
                return self._configured() and access_check() is True and self._unlocked() is True
            def preflight(device):
                require(check())
                require(bridge._backend(bridge._endpoint, bridge._chain) is True)
                epoch = self.runtime.store.storage.snapshot().epoch
                inventory = device.get_contacts(authorize=check)
                for pin in self.gateway.pins:
                    require(self._pin(pin))
                    inventory.unique_prefix(pin.radio_key)
                states = self.gateway.inspect_on_owner(device, access_check=check)
                require(all(state.state != "unknown" for state in states))
                require(self.runtime.store.storage.snapshot().epoch == epoch and check())
                return True
            require(self.runtime.coordinator.submit(preflight).result(timeout=self.operation_timeout) is True)
            self.runtime.attach_public_gateway(self)
            self._started = True
            self.provider.start_receiving(consent_reader=self._consent, lease_factory=self._lease,
                unlocked_check=self._unlocked, interval=0.2)
            return True
        finally:
            self._mutex.release()

    def ingest(self, device, message, *, access_check):
        require(self._configured())
        return self.gateway.ingest_on_owner(device, message, access_check=access_check)

    def dispatch(self, device, *, access_check):
        require(self._configured())
        states = self.gateway.inspect_on_owner(device, access_check=access_check)
        require(all(state.state != "unknown" for state in states))
        queued = next((state for state in states if state.state == "queued"), None)
        if queued is not None:
            return self.gateway.dispatch_on_owner(device, queued.slot_id, access_check=access_check)
        return None

    def close(self, timeout):
        self._closed = True
        return self.gateway.close(timeout)
