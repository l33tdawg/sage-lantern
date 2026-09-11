#!/usr/bin/env python3
"""Read only a pinned installed SAGE YAML; never load keys or contact services.

Default output describes known schema fields without their string values.
--policy emits an allowlisted public projection only when every selected value
is safe to disclose. Neither mode approves policy or explains commissioning drift.
Nonempty credential fields anywhere refuse both modes without printing names.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys

import yaml


CONFIG = Path('/var/lib/sage-lantern/sage-personal/config.yaml')
FIELDS = {
    'chain_id': None, 'data_dir': None, 'agent_key_file': None, 'rest_addr': None,
    'encryption': {'enabled'},
    'private_media': {'enabled', 'actor_bytes', 'node_bytes', 'actor_objects', 'node_objects', 'min_free_bytes'},
    'embedding': {'provider', 'dimension', 'model', 'base_url', 'api_key'},
    'federation': {'enabled', 'listen_addr', 'p2p_enabled', 'p2p_force_private',
                   'p2p_listen_addrs', 'p2p_relay_addrs', 'p2p_peers', 'p2p_routes'},
    'quorum': {'enabled', 'peers', 'p2p_addr', 'tls_addr', 'state_sync'},
    'voter': {'enabled', 'poll_interval', 'required'},
    'vendored_agent_bootstrap': {'agent_key_file', 'home_domain', 'clearance'},
}
CREDENTIAL = re.compile(r'(?:password|passphrase|secret|token|cookie|credential|private_key|secret_key|seed|api_key)', re.I)


def require(condition):
    if not condition:
        raise ValueError('policy inspection refused')


def identity(info):
    return tuple(getattr(info, name) for name in ('st_dev', 'st_ino', 'st_uid', 'st_mode',
        'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def read_config(uid):
    for parent in CONFIG.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, uid) and not info.st_mode & 0o022)
    descriptor = os.open(CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == uid and before.st_nlink == 1
            and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= 65536)
        raw = os.read(descriptor, 65537)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(descriptor))
            and identity(before) == identity(CONFIG.lstat()))
        return raw
    finally:
        os.close(descriptor)


def parse(raw):
    require(len(raw) <= 65536)
    require(not any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(raw)))
    class UniqueLoader(yaml.SafeLoader):
        pass
    def mapping(loader, node):
        result = {}
        for key, value in node.value:
            require(key.tag == 'tag:yaml.org,2002:str' and key.value not in result)
            result[key.value] = loader.construct_object(value)
        return result
    UniqueLoader.add_constructor('tag:yaml.org,2002:map', mapping)
    value = yaml.load(raw, Loader=UniqueLoader)
    require(type(value) is dict)
    def credentials(current, depth=0):
        require(depth <= 16)
        if type(current) is dict:
            for key, child in current.items():
                require(not CREDENTIAL.search(key.replace('-', '_')) or child is None or child == '')
                credentials(child, depth + 1)
        elif type(current) is list:
            for child in current:
                credentials(child, depth + 1)
    credentials(value)
    return value


def schema(value):
    result = {}
    for name, fields in FIELDS.items():
        if name not in value:
            result[name] = {'type': 'missing'}
            continue
        section = value[name]
        if fields is not None and type(section) is dict:
            result[name] = {'fields': {key: type(section[key]).__name__ for key in sorted(fields & set(section))},
                'unknown_fields': len(set(section) - fields)}
            result[name]['safe_values'] = {key: section[key] for key in sorted(fields & set(section))
                if type(section[key]) is bool or (type(section[key]) is int and 0 <= section[key] <= 2**53 - 1)}
            if name == 'embedding':
                result[name]['model_nonempty'] = section.get('model') not in (None, '')
        else:
            result[name] = {'type': type(section).__name__}
    return result


def public_policy(value):
    require(set(FIELDS) <= set(value))
    result = {name: value[name] for name in FIELDS}
    patterns = {
        'chain_id': r'sage-personal-[A-Za-z0-9._-]{1,110}',
        'data_dir': r'/var/lib/sage-lantern/sage-personal/data',
        'agent_key_file': r'/var/lib/sage-lantern/sage-personal/agent\.key',
    }
    for name, pattern in patterns.items():
        require(type(result[name]) is str and re.fullmatch(pattern, result[name]))
    for name, fields in FIELDS.items():
        if fields is None:
            continue
        section = result[name]
        require(type(section) is dict and set(section) <= fields)
        for key, child in section.items():
            if key in {'enabled', 'p2p_enabled', 'p2p_force_private', 'required'}:
                require(type(child) is bool)
            elif key in {'dimension', 'clearance', 'actor_bytes', 'node_bytes', 'actor_objects', 'node_objects', 'min_free_bytes'}:
                require(type(child) is int and 0 <= child <= 2**53 - 1)
            elif (name, key) == ('embedding', 'provider'):
                require(child in ('hash', 'ollama', 'openai-compatible'))
            elif key in {'model', 'base_url', 'api_key'}:
                require(child is None or child == '')
            elif key == 'agent_key_file':
                require(child == '/var/lib/sage-lantern/identity/lantern-agent.key')
            elif key == 'home_domain':
                require(type(child) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,63}', child))
            elif key == 'poll_interval':
                require(type(child) is str and re.fullmatch(r'[0-9]{1,6}(?:ms|s|m)', child))
            elif key in {'listen_addr', 'p2p_addr', 'tls_addr'}:
                require(type(child) is str and re.fullmatch(r'(?:tcp://)?(?:127\.0\.0\.1|0\.0\.0\.0|\[::\])?:[0-9]{1,5}', child))
            else:
                require(child == [] or child == {} or child is None)
    require(type(result['rest_addr']) is str and re.fullmatch(r'(?:127\.0\.0\.1|0\.0\.0\.0):[0-9]{1,5}', result['rest_addr']))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--current-sage-sha256', required=True)
    parser.add_argument('--policy', action='store_true')
    args = parser.parse_args(argv)
    try:
        require(os.geteuid() == 0 and sys.platform == 'linux')
        require(re.fullmatch(r'[0-9a-f]{64}', args.current_sage_sha256))
        uid = pwd.getpwnam('sage-lantern').pw_uid
        require(uid > 0)
        raw = read_config(uid)
        require(hashlib.sha256(raw).hexdigest() == args.current_sage_sha256)
        value = parse(raw)
        result = public_policy(value) if args.policy else schema(value)
        require(read_config(uid) == raw)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print('Policy inspection refused; no configuration values disclosed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
