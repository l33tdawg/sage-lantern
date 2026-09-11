"""Strict evidence checks, not a light client or a synthetic BFT implementation.

Callers must obtain all observations from their owned isolated network. These
functions do not authenticate supplied dictionaries or authorize enrollment.
"""

import base64
import hashlib
import re
import uuid


class AcceptanceError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise AcceptanceError(code)


def decimal(value):
    require(type(value) is str and re.fullmatch(r'[1-9][0-9]{0,15}', value) is not None,
            'invalid_height')
    return int(value)


def hash_hex(value):
    require(type(value) is str and re.fullmatch(r'[0-9a-fA-F]{64}', value) is not None,
            'invalid_hash')
    return value.lower()


def validator_set(response, height):
    result = response['result']
    require(decimal(result['block_height']) == height, 'validator_height_mismatch')
    require(result['count'] == result['total'] == '4', 'not_four_validators')
    entries = result['validators']
    require(type(entries) is list and len(entries) == 4, 'not_four_validators')
    result_set = set()
    for entry in entries:
        require(entry['pub_key']['type'] == 'tendermint/PubKeyEd25519', 'invalid_validator')
        encoded = entry['pub_key']['value']
        key = base64.b64decode(encoded, validate=True)
        require(len(key) == 32 and base64.b64encode(key).decode() == encoded, 'invalid_validator')
        result_set.add((key.hex(), decimal(entry['voting_power'])))
    require(len({key for key, _ in result_set}) == 4, 'duplicate_validator')
    return result_set


def verify_network(observations, *, chain_id, expected_validators, acceptance_barrier_height=None):
    """Four actual RPC samples at one requested block height; no latest-height comparison.

    Each sample contains responses to /block?height=H, /validators?height=H
    and /abci_info. The block header AppHash represents application state H-1.
    Caller must independently pin these endpoints and genesis validator keys.
    """
    try:
        require(type(observations) is list and len(observations) == 4, 'four_samples_required')
        require(len(expected_validators) == 4, 'four_expected_validators_required')
        headers = [item['block']['result']['block']['header'] for item in observations]
        height = decimal(headers[0]['height'])
        require(height > 1, 'uncommitted_state')
        if acceptance_barrier_height is not None:
            require(type(acceptance_barrier_height) is int and 0 < acceptance_barrier_height <= height - 1,
                    'acceptance_barrier_uncovered')
        hashes = set()
        versions = set()
        current_versions = []
        for item, header in zip(observations, headers):
            require(header['chain_id'] == chain_id, 'wrong_chain')
            require(decimal(header['height']) == height, 'height_mismatch')
            hashes.add(hash_hex(header['app_hash']))
            require(validator_set(item['validators'], height) == expected_validators,
                    'validator_set_mismatch')
            version = header['version']['app']
            require(type(version) in (str, int) and str(version).isdigit()
                    and 25 <= int(version) <= 27, 'unreviewed_app_version')
            versions.add(int(version))
            current = item['abci_info']['result']['response']['app_version']
            require(type(current) in (str, int) and str(current).isdigit()
                    and int(version) <= int(current) <= 27, 'invalid_current_app_version')
            current_versions.append(int(current))
        require(len(hashes) == 1, 'apphash_mismatch')
        require(len(versions) == 1, 'app_version_mismatch')
        return dict(block_height=height, application_state_height=height - 1,
                    app_hash=hashes.pop(), validator_count=4, historical_app_version=versions.pop(),
                    current_app_versions=current_versions, acceptance_barrier_height=acceptance_barrier_height,
                    offline_proof=False)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        if isinstance(error, AcceptanceError):
            raise
        raise AcceptanceError('malformed_network_evidence') from None


def verify_profile(profile, *, agent_id, domain):
    expected = dict(agent_id=agent_id, role='member', enrollment_status='active',
                    registration_status='active', approval_required=False,
                    can_read=True, can_write=True, clearance=0, home_domain=domain)
    require(type(profile) is dict and all(type(profile.get(key)) is type(value)
            and profile[key] == value for key, value in expected.items()), 'ordinary_enrollment_required')


def verify_record_bindings(records, submission, *, content, agent_id, domain):
    """Validate all accepted projections before the caller captures its height barrier."""
    try:
        identifier = submission['memory_id']
        require(str(uuid.UUID(identifier)) == identifier, 'invalid_memory_id')
        hash_hex(submission['tx_hash'])
        require(submission['committed'] is True, 'submission_not_committed')
        height = submission['committed_height']
        require(type(height) is int and height > 0, 'invalid_submission_height')
        require(type(records) is list and len(records) == 4, 'four_records_required')
        expected = dict(memory_id=identifier, submitting_agent=agent_id, domain_tag=domain,
                        memory_type='inference', classification=0, content=content,
                        content_hash=hashlib.sha256(content.encode()).hexdigest(), status='committed')
        for record in records:
            require(type(record) is dict and all(type(record.get(key)) is type(value)
                    and record[key] == value for key, value in expected.items()), 'memory_binding_or_lifecycle_mismatch')
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        if isinstance(error, AcceptanceError):
            raise
        raise AcceptanceError('malformed_memory_evidence') from None


def verify_memory(records, submission, *, content, agent_id, domain, network):
    """Submission inclusion alone cannot cover the later accepted lifecycle."""
    verify_record_bindings(records, submission, content=content, agent_id=agent_id, domain=domain)
    try:
        barrier = network['acceptance_barrier_height']
        state_height = network['application_state_height']
        require(type(barrier) is int and type(state_height) is int
                and submission['committed_height'] <= barrier <= state_height, 'acceptance_barrier_uncovered')
        return dict(memory_id=submission['memory_id'], lifecycle='committed',
                    basis='isolated_nodes_canonical_observation', acceptance_barrier_height=barrier,
                    offline_proof=False)
    except (KeyError, TypeError):
        raise AcceptanceError('acceptance_barrier_missing') from None
