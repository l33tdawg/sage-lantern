"""Malformed object and false qualification claims must never pass review."""
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('confinement_stage',
    ROOT / 'scripts/lantern/stage-desktop-confinement.py')
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


def fixture(sections=None):
    """Tiny ELF structural fixture, deliberately not executable BPF proof."""
    names = [''] + (sections if sections is not None else
        sorted('lsm/' + name for name in stage.HOOKS)) + ['.maps', 'license', '.BTF', '.BTF.ext', '.strtab']
    table = b'\0'
    offsets = {'': 0}
    for name in names[1:]:
        offsets[name] = len(table)
        table += name.encode() + b'\0'
    body = bytearray(64)
    body[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<HH', body, 16, 1, 247)
    records = [bytes(64)]
    for name in names[1:]:
        payload = table if name == '.strtab' else bytes(8)
        records.append(struct.pack('<IIQQQQIIQQ', offsets[name], 3 if name == '.strtab' else 1,
            4 if name.startswith('lsm/') else 0, 0, len(body), len(payload), 0, 0, 1, 0))
        body.extend(payload)
    start = len(body)
    body.extend(b''.join(records))
    struct.pack_into('<Q', body, 40, start)
    struct.pack_into('<HHH', body, 58, 64, len(names), len(names) - 1)
    return bytes(body)


class ObjectReviewTests(unittest.TestCase):
    def test_complete_section_set_only_proves_structure(self):
        self.assertEqual(len(stage.object_sections(fixture())), 13)

    def test_missing_or_extra_hook(self):
        names = sorted('lsm/' + name for name in stage.HOOKS)
        for sections in (names[:-1], names + ['lsm/unreviewed'], names + [names[0]]):
            with self.subTest(sections=sections), self.assertRaises(ValueError):
                stage.object_sections(fixture(sections))

    def test_wrong_architecture_truncated_or_unbounded_table(self):
        body = bytearray(fixture())
        mutations = []
        bad = body.copy(); struct.pack_into('<H', bad, 18, 183); mutations.append(bad)
        bad = body.copy(); struct.pack_into('<Q', bad, 40, 2**63); mutations.append(bad)
        bad = body.copy(); struct.pack_into('<H', bad, 60, 65535); mutations.append(bad)
        mutations.extend([body[:50], b'not an object'])
        for bad in mutations:
            with self.subTest(size=len(bad)), self.assertRaises(ValueError):
                stage.object_sections(bad)

    def test_missing_metadata_empty_code_and_invalid_string_offset(self):
        body = bytearray(fixture())
        start = struct.unpack_from('<Q', body, 40)[0]
        for offset, value, fmt in ((start + 64 + 32, 0, '<Q'),
                                   (start + 64, 2**32 - 1, '<I')):
            bad = body.copy(); struct.pack_into(fmt, bad, offset, value)
            with self.assertRaises(ValueError): stage.object_sections(bad)
        names = sorted('lsm/' + name for name in stage.HOOKS)
        bad = fixture(names).replace(b'.BTF.ext\0', b'.NON.ext\0')
        with self.assertRaises(ValueError): stage.object_sections(bad)


class BundleReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='confinement-review-')
        self.directory = Path(self.temporary.name)
        path = self.directory / 'object/desktop_guard.bpf.o'
        path.parent.mkdir()
        path.write_bytes(fixture())
        self.value = dict(schema='sage.lantern.desktop-confinement-review.v1',
            **{name: False for name in stage.FLAGS}, uid_boundary=1000,
            program_sections=stage.object_sections(path.read_bytes()),
            files={'object/desktop_guard.bpf.o': stage.digest(path.read_bytes())})
        self.save()

    def tearDown(self): self.temporary.cleanup()

    def save(self):
        (self.directory / 'manifest.json').write_text(json.dumps(self.value))

    def test_disabled_review_verifies(self):
        self.assertEqual(stage.verify(self.directory), 1)

    def test_no_claimed_live_acceptance(self):
        for flag in stage.FLAGS:
            for invalid in (True, 0, None, 'false'):
                self.value[flag] = invalid; self.save()
                with self.subTest(flag=flag, invalid=invalid), self.assertRaises(ValueError):
                    stage.verify(self.directory)
            self.value[flag] = False

    def test_changed_payload(self):
        (self.directory / 'object/desktop_guard.bpf.o').write_bytes(b'changed')
        with self.assertRaises(ValueError): stage.verify(self.directory)

    def test_unlisted_file(self):
        (self.directory / 'activate').write_text('unreviewed')
        with self.assertRaises(ValueError): stage.verify(self.directory)

    def test_path_traversal_or_external_file(self):
        for name in ('../outside', '/etc/passwd', 'object/../object/desktop_guard.bpf.o'):
            self.value['files'] = {name: '0' * 64}; self.save()
            with self.subTest(name=name), self.assertRaises(ValueError): stage.verify(self.directory)

    def test_symlink_payload(self):
        path = self.directory / 'object/desktop_guard.bpf.o'
        path.rename(self.directory / 'external')
        path.symlink_to(self.directory / 'external')
        with self.assertRaises(ValueError): stage.verify(self.directory)

    def test_mismatched_contract(self):
        self.value['uid_boundary'] = 0; self.save()
        with self.assertRaises(ValueError): stage.verify(self.directory)


if __name__ == '__main__': unittest.main()
