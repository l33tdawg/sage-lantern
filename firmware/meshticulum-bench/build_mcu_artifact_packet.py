#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate a pinned app-only source packet; no flash or device authorization.

Both reproduced build directories and an independently supplied current-app
record/pin are mandatory. The eventual trusted flash transaction must match the
actual installed app, board and USB identity before writing application sectors.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile

from source_core import core_source

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'software/meshticulum-integration/src'))
from lantern_meshticulum.bench.mcu_artifact import artifact, canonical, validate_artifact


def need(value, reason):
    if not value: raise ValueError(reason)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def read(path, limit):
    path = Path(path)
    need(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= limit,
         'unbounded_or_unavailable_packet_input')
    with path.open('rb') as stream: raw = stream.read(limit+1)
    need(len(raw) == path.stat().st_size <= limit, 'packet_input_changed')
    return raw


def validate_source(core, selected):
    validate_artifact(selected)
    source = core_source(core)
    prefix = source/'firmware/meshticulum-bench'
    for name, expected in selected['application'].items():
        need(sha(read(prefix/name, 500000)) == expected, 'mcu_application_source_changed')
    need(sha(read(prefix/'prepare_firmware.py',500000)) == selected['recipe_sha256']
        and sha(read(prefix/'inputs.lock.json',500000)) == selected['input_lock_sha256'],
        'mcu_firmware_recipe_or_lock_changed')
    lock = ROOT/'software/meshticulum-integration/meshticulum-source-lock.json'
    need(sha(read(lock,500000)) == selected['source_lock_sha256'], 'mcu_source_lock_changed')
    return source


def reproduced(build_dirs, selected):
    need(len(build_dirs) == 2 and len({Path(p).resolve() for p in build_dirs}) == 2,
        'two_independent_reproduced_builds_required')
    files, images, records = {}, [], []
    for index, directory in enumerate(build_dirs):
        directory = Path(directory)
        source_raw = read(directory/'SOURCE.json',3000000)
        build_raw = read(directory/'evidence/BUILD.json',300000)
        need(sha(source_raw) == selected['reproduced_source_sha256'][index]
            and sha(build_raw) == selected['reproduced_build_sha256'][index],
            'unreviewed_reproduction_record')
        source, build = json.loads(source_raw), json.loads(build_raw)
        need(source['identity'] == selected['firmware'] and source['application'] == selected['application']
            and source['recipe_sha256'] == selected['recipe_sha256']
            and source['lock_sha256'] == selected['input_lock_sha256']
            and build['source_manifest_sha256'] == sha(source_raw)
            and build['platformio_version'] == '6.1.19', 'reproduced_source_binding_changed')
        matches = [r for r in build['results'] if r['target'] == selected['target']]
        need(len(matches) == 1 and matches[0]['returncode'] == 0, 'successful_heltec_build_required')
        row = matches[0]
        for name, expected in selected['verified_artifacts'].items():
            raw = read(directory/'project/.pio/build'/selected['target']/name,20000000)
            need(sha(raw) == row['artifacts'][name]['sha256'] == expected
                and len(raw) == row['artifacts'][name]['bytes'], 'reproduced_artifact_changed')
            if name == 'firmware.bin': images.append(raw)
        # Raw maps are retained with their own checked build-record hashes;
        # build-root/library paths make their raw digests intentionally unequal.
        map_raw = read(directory/'project/.pio/build'/selected['target']/'firmware.map',20000000)
        need(sha(map_raw) == row['artifacts']['firmware.map']['sha256'], 'reproduced_map_changed')
        files[f'provenance/source-{index+1}.json'] = source_raw
        files[f'provenance/build-{index+1}.json'] = build_raw
        records.append(dict(source_sha256=sha(source_raw), build_sha256=sha(build_raw)))
    need(images[0] == images[1] and len(images[0]) == selected['image_bytes'],
        'independent_firmware_reproduction_mismatch')
    return images[0], files, records


def rollback(image, record, expected_pin):
    raw = read(record,16384)
    need(type(expected_pin) is str and re.fullmatch('[0-9a-f]{64}', expected_pin)
        and sha(raw) == expected_pin, 'independent_rollback_record_pin_required')
    value = json.loads(raw)
    fields = {'schema','unit','board_id','target','usb_serial','firmware','image_sha256',
        'image_bytes','current_app_md5','independently_verified'}
    need(type(value) is dict and set(value) == fields
        and value['schema'] == 'meshticulum.independently-verified-current-app.v1'
        and value['unit'] == 'unit-a' and type(value['board_id']) is int and value['board_id'] == 1
        and value['target'] == 'mtbench_heltec_v4'
        and value['usb_serial'] == 'F8:5B:1B:A5:F6:08'
        and value['independently_verified'] is True
        and type(value['firmware']) is str and re.fullmatch('mtb1-[0-9a-f]{10}',value['firmware']),
        'independent_current_rollback_binding_required')
    previous = read(image,4*1024*1024)
    need(type(value['image_bytes']) is int and len(previous) == value['image_bytes']
        and sha(previous) == value['image_sha256']
        and hashlib.md5(previous).hexdigest() == value['current_app_md5']
        and len(previous) >= 32 and previous[0] == 0xe9 and previous[12:14] == b'\x09\0',
        'current_rollback_image_changed_or_wrong_chip')
    return previous, raw, value


def generate(*, core, selector, build_dirs, rollback_image, rollback_record,
             rollback_record_sha256, output):
    selected = artifact(selector)
    source = validate_source(core, selected)
    image, files, builds = reproduced(build_dirs, selected)
    previous, rollback_raw, prior = rollback(rollback_image, rollback_record, rollback_record_sha256)
    output = Path(output)
    need(not output.exists(), 'packet_output_already_exists')
    files.update({'firmware.bin':image,'rollback-current.bin':previous,
                  'provenance/current-rollback.json':rollback_raw})
    files['provenance/meshticulum-source-lock.json'] = read(
        ROOT/'software/meshticulum-integration/meshticulum-source-lock.json',500000)
    for name in ('main.cpp','protocol.hpp','gfsk_profile.hpp','gfsk250_profile.hpp',
                 'prepare_firmware.py','inputs.lock.json'):
        files['source/'+name] = read(source/'firmware/meshticulum-bench'/name,500000)
    for name in ('flash_app.py','linux_hat.py','serial_port.py','serial_protocol.py'):
        files['meshticulum/bench/'+name] = read(source/'src/meshticulum/bench'/name,500000)
    for name in ('LICENSE','NOTICE'): files[name] = read(source/name,500000)
    manifest = dict(schema='meshticulum.explicit-mcu-app-source-packet.v1',mcu_artifact=selected,
        rollback_record_sha256=rollback_record_sha256,rollback=dict(prior),reproduced_builds=builds,
        files={n:sha(raw) for n,raw in sorted(files.items())},app_offset=0x10000,
        write_scope='Application sectors only; original partition table, OTA selector, NVS and other sectors unchanged.',
        trusted_flash_transaction_required=True,current_physical_image_match=None,
        install_authorization=False,flashed=False,rf_qualified=False,notices_complete=False)
    files['manifest.json'] = canonical(manifest)+b'\n'
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w',format=tarfile.USTAR_FORMAT) as archive:
        for name,raw in sorted(files.items()):
            entry=tarfile.TarInfo(name);entry.size=len(raw);entry.mode=0o600;entry.mtime=0
            archive.addfile(entry,io.BytesIO(raw))
    output.mkdir(mode=0o700)
    packet=gzip.compress(stream.getvalue(),mtime=0);(output/'packet.tar.gz').write_bytes(packet)
    receipt=dict(schema='meshticulum.explicit-mcu-source-packet-receipt.v1',packet_sha256=sha(packet),
        manifest_sha256=sha(files['manifest.json']),mcu_artifact=selected,
        current_physical_image_match=None,install_authorization=False,flashed=False,rf_qualified=False)
    (output/'PACKET.json').write_bytes(canonical(receipt)+b'\n')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core-root',type=Path,required=True)
    parser.add_argument('--selector',required=True)
    parser.add_argument('--build-dir',type=Path,action='append',required=True)
    parser.add_argument('--rollback-image',type=Path,required=True)
    parser.add_argument('--rollback-record',type=Path,required=True)
    parser.add_argument('--rollback-record-sha256',required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    result=generate(core=args.core_root,selector=args.selector,build_dirs=args.build_dir,
        rollback_image=args.rollback_image,rollback_record=args.rollback_record,
        rollback_record_sha256=args.rollback_record_sha256,output=args.output_dir)
    print(json.dumps(result,sort_keys=True))


if __name__ == '__main__': main()
