from tests.radio_profiles import SOURCE_ADMISSION
import hashlib, importlib.util, json
from pathlib import Path
import pytest
from meshticulum.bench.authority import RunAuthority
from tests.test_meshticulum_bench_runtime import Clock, BOOT
def helper(name):
    path = Path(__file__).resolve().parents[3] / 'firmware/meshticulum-bench' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_fresh_management_packages_never_put_application_payload_on_receiver(tmp_path):
    prep = helper('prepare_run')
    clock = Clock()
    peers = {u: {'chain_id': 'bench-test', 'agent_id': c * 64} for u, c in [('unit-a', 'a'), ('unit-b', 'b')]}
    kwargs = dict(now_us=clock()[0], boot_ids={u: BOOT for u in peers}, peers=peers, payload=b'synthetic')
    one, packages = prep.packages('unit-a', 'unit-b', **kwargs)
    two, fresh = prep.packages('unit-a', 'unit-b', **kwargs)
    assert one != two and packages['unit-a']['secret'] != fresh['unit-a']['secret']
    assert packages['unit-a']['secret'] == packages['unit-b']['secret']
    assert packages['unit-b']['payload'] is None
    assert packages['unit-b']['reuse_verified_chunk'] is False
    authorities = []
    import base64
    try:
        for unit, package in packages.items():
            root = tmp_path / unit; root.mkdir(mode=0o700)
            for name, data in [('grant.json', json.dumps(package['grant']).encode()),
                ('secret.bin', base64.b64decode(package['secret'])), ('active', b'active\n')]:
                (root / name).write_bytes(data); (root / name).chmod(0o600)
            authorities.append(RunAuthority(root, radio_admission=SOURCE_ADMISSION, expected_unit=unit, host_boot_id=BOOT,
                                            clock=clock, owner_probe=lambda: True))
        assert authorities[0].grant.digest == authorities[1].grant.digest
    finally:
        for authority in authorities: authority.close()


def test_runtime_archive_parser_refuses_bad_hash_and_link_member():
    import io, tarfile
    bootstrap = helper('runtime_bootstrap')
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        entry = tarfile.TarInfo('bench_node.py'); entry.type = tarfile.SYMTYPE
        entry.linkname = '/etc/passwd'; archive.addfile(entry)
    data = output.getvalue()
    with pytest.raises(RuntimeError, match='hash_mismatch'):
        bootstrap.members(data, '0' * 64)
    with pytest.raises(RuntimeError, match='archive_member'):
        bootstrap.members(data, hashlib.sha256(data).hexdigest())
