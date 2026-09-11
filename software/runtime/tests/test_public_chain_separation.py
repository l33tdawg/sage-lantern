import copy
from types import SimpleNamespace

import pytest

from sage_lantern.mesh_daemon import validate_config
from sage_lantern.mesh_public_root import RootPublicMesh
from sage_lantern.message_contacts import RecipientID
from test_mesh_daemon import configuration
from test_mesh_runtime import factory
from test_public_mesh_protocol import IDENTITY, GATEWAY_KEY


def settings(state, private):
    return dict(identity_file="/var/lib/sage-lantern/identity/public.key", author=IDENTITY.agent_id,
        chain_id="reviewed-public-chain", domain="public-domain", gateway_key=GATEWAY_KEY,
        radio_key=state.radio.hex(), storage_actor=private["agent_id"], storage_chain=private["chain_id"],
        timing=dict(frame_seconds=0.1, gateway_seconds=30, safety_seconds=10))


@pytest.mark.parametrize("chain", ["sage-personal-unit-a", "sage-personal-unit-b"])
def test_separate_proposer_key_does_not_make_private_chain_public(factory, chain):
    config = configuration(factory)
    config["chain_id"] = chain
    config["radio"]["public_key"] = factory.device._key.hex()
    config["public_mesh"] = settings(factory, config)
    before = copy.deepcopy(config)
    validate_config(config)
    assert config == before
    config["public_mesh"]["chain_id"] = chain
    with pytest.raises(ValueError, match="public_mesh_unavailable"):
        validate_config(config)
    assert not factory.transmits


def test_direct_root_composition_rejects_private_chain_before_store_or_radio(factory):
    config = configuration(factory)
    value = settings(factory, config)
    value["chain_id"] = config["chain_id"]
    runtime = SimpleNamespace(actor=RecipientID(config["chain_id"], config["agent_id"]))
    with pytest.raises(ValueError, match="public_mesh_unavailable"):
        RootPublicMesh(runtime, None, value, IDENTITY, factory.identity, active=lambda: True)
    assert not factory.transmits
