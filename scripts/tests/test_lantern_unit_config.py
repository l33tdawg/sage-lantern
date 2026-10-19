import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/lantern-unit-config.py"
SPEC = importlib.util.spec_from_file_location("lantern_unit_config", SCRIPT)
CONFIG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIG)


class UnitConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.metadata = self.root / "unit.env"
        self.metadata.write_text("UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n")
        self.radio = self.write_radio("b", "22")
        self.peer = self.write_radio("a", "11")
        self.arguments = ["--unit-id", "unit-b", "--prepared-metadata", str(self.metadata),
                          "--radio-inventory", str(self.radio), "--peer-radio-inventory", str(self.peer),
                          "--radio-usb-serial", "RADIO-B", "--radio-vendor-id", "303a",
                          "--radio-product-id", "1001", "--radio-stable-path", "/dev/serial/by-id/usb-RADIO-B",
                          "--radio-public-fingerprint", "sha256:" + hashlib.sha256(bytes.fromhex("22" * 32)).hexdigest(),
                          "--audio-usb-serial", "AUDIO-B", "--audio-stable-path", "/dev/snd/by-id/usb-AUDIO-B-00"]

    def write_radio(self, suffix, key):
        directory = self.root / suffix
        directory.mkdir()
        path = directory / "meshcore-radio-public.json"
        path.write_text(json.dumps({
            "schema": "sage.lantern.meshcore-radio-public.v1", "unit_id": "unit-" + suffix,
            "usb": {"serial": "RADIO-" + suffix.upper(), "vendor_id": "303a", "product_id": "1001",
                    "stable_path": "/dev/serial/by-id/usb-RADIO-" + suffix.upper(),
                    "managed_path": "/dev/sage-lantern-radio"},
            "radio_identity": {"public_key": key * 32,
                               "public_key_sha256": "sha256:" + hashlib.sha256(bytes.fromhex(key * 32)).hexdigest()},
            "factory_backup": {"path": "/must/not/read/private-backup.bin"},
        }))
        return path

    def args(self, **changes):
        args = CONFIG.parser().parse_args(self.arguments)
        for key, value in changes.items():
            setattr(args, key, value)
        return args

    def modify_radio(self, path, update):
        value = json.loads(path.read_text())
        update(value)
        path.write_text(json.dumps(value))

    def test_plan_matches_real_prepare_and_radio_schemas(self):
        plan = CONFIG.build_plan(self.args())
        self.assertEqual(plan["hostname"], "sage-lantern-b")
        self.assertEqual(plan["evidence_root"], "/home/radxa/sage-commons/evidence/unit-b-provisioning")
        self.assertEqual(plan["operator_account"], "radxa")
        self.assertEqual(plan["compared_peer_units"], ["unit-a"])
        self.assertFalse(plan["hardware_verified"])
        self.assertFalse(plan["provisioned"])
        self.assertNotIn("factory_backup", json.dumps(plan))
        self.assertEqual(plan["radio"]["public_key"], "22" * 32)
        self.assertFalse(plan["ownership_verified"])

    def test_radio_absent_is_explicit_and_forbids_pins_inventories_and_peers(self):
        absent = dict(radio_absent=True, radio_inventory=None, radio_usb_serial=None, radio_vendor_id=None,
                      radio_product_id=None, radio_stable_path=None, radio_public_fingerprint=None,
                      peer_radio_inventory=[])
        plan = CONFIG.build_plan(self.args(**absent))
        self.assertEqual(plan["radio"], {"present": False, "reason": "operator-declared-absent"})
        self.assertEqual(plan["compared_peer_units"], [])
        self.assertFalse(plan["fleet_comparison_performed"])
        self.assertIn("Radio explicitly declared absent; no radio inventory was supplied or compared.",
                      plan["limitations"])
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args(**{**absent, "radio_absent": False}))
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args(**{**absent, "radio_absent": True, "radio_inventory": str(self.radio)}))
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args(**{**absent, "radio_stable_path": "/dev/serial/by-id/usb-RADIO-B"}))
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args(**{**absent, "peer_radio_inventory": [str(self.peer)]}))
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args(radio_inventory=None, radio_usb_serial=None, radio_vendor_id=None,
                                        radio_product_id=None, radio_stable_path=None, radio_public_fingerprint=None,
                                        peer_radio_inventory=[]))

    def test_platform_capture_audio_plan_and_udev_rule(self):
        platform = dict(audio_usb_serial=None, audio_platform_id_path="platform-es8388-sound",
                        audio_stable_path="/dev/snd/by-path/platform-es8388-sound",
                        audio_vendor_id=None, audio_product_id=None)
        plan = CONFIG.build_plan(self.args(**platform))
        self.assertEqual(plan["audio"], {"present": True, "source": "platform",
                                         "stable_path": "/dev/snd/by-path/platform-es8388-sound",
                                         "id_path": "platform-es8388-sound", "platform_device": "es8388-sound"})
        self.assertIn("Capture audio is an operator-named platform codec, not a USB microphone.",
                      plan["limitations"])
        self.assertEqual(CONFIG.audio_udev_rules(plan),
                         'SUBSYSTEM=="sound", KERNELS=="es8388-sound", GROUP="sage-lantern", MODE="0660"')
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args(**{**platform, "audio_stable_path": "/dev/snd/by-path/platform-other-sound"}))
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args(**{**platform, "audio_vendor_id": "1234"}))
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args(**{**platform, "audio_usb_serial": "AUDIO-B"}))
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args(**{**platform, "audio_platform_id_path": "usb-es8388-sound"}))
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.audio_udev_rules({"audio": {"source": "platform", "id_path": "platform-es8388-sound",
                                               "platform_device": "es8388-sound",
                                               "stable_path": "/dev/snd/by-path/platform-other-sound"}})

    def test_c_to_f_plans_require_their_own_metadata_and_radio(self):
        for letter in 'cdef':
            with self.subTest(unit=letter):
                unit = 'unit-' + letter
                radio = self.write_radio(letter, '33')
                self.metadata.write_text(f'UNIT_ID={unit}\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n')
                args = self.args(unit_id=unit, radio_inventory=str(radio),
                    radio_usb_serial='RADIO-' + letter.upper(),
                    radio_stable_path='/dev/serial/by-id/usb-RADIO-' + letter.upper(),
                    radio_public_fingerprint='sha256:' + hashlib.sha256(bytes.fromhex('33' * 32)).hexdigest())
                plan = CONFIG.build_plan(args)
                self.assertEqual(plan['unit_id'], unit)
                self.assertEqual(plan['hostname'], 'sage-lantern-' + letter)
                args.unit_id = 'unit-b'
                with self.assertRaises(CONFIG.PlanError):
                    CONFIG.build_plan(args)

    def test_cli_json_only_and_no_mutation(self):
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result = subprocess.run([sys.executable, str(SCRIPT), *self.arguments], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["mode"], "plan-only")
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_voice_export_requires_inventory_and_explicit_format(self):
        for extra in (["--format", "voice-activation"],
                      ["--identity-inventory", str(self.root / "unit-inventory.env")],
                      ["--activation-staging-root", str(self.root)]):
            with self.subTest(extra=extra):
                result = subprocess.run([sys.executable, str(SCRIPT), *self.arguments, *extra],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_voice_export_does_not_claim_support_for_other_installer_targets(self):
        args = self.args()
        plan = CONFIG.build_plan(args)
        for changes in ({"unit_id": "unit-c"}, {"operator_account": "other"}):
            with self.subTest(changes=changes), self.assertRaises(CONFIG.PlanError):
                CONFIG.voice_activation_config(args, {**plan, **changes})

    def test_unit_subset_and_account_validation(self):
        for unit in ("unit-aa", "unit-B", "unit-g", "unit-z", "unit-b;id", "unit-b/..", "other"):
            with self.subTest(unit=unit), self.assertRaises(CONFIG.PlanError):
                CONFIG.build_plan(self.args(unit_id=unit))
        for account in ("root", "sage-lantern", "../radxa", "radxa;id", ""):
            with self.subTest(account=account), self.assertRaises(CONFIG.PlanError):
                CONFIG.build_plan(self.args(account=account))
        plan = CONFIG.build_plan(self.args(account="operator"))
        self.assertEqual(plan["repo_root"], "/home/operator/sage-commons")

    def test_prepared_metadata_exact_and_no_shell_interpretation(self):
        for data in ("UNIT_ID=unit-a\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n",
                     "UNIT_ID=unit-b\n", "UNIT_ID=$(touch /tmp/not-executed)\n",
                     "UNIT_ID=unit-b\nUNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n",
                     "UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\nSECRET=unexpected\n"):
            self.metadata.write_text(data)
            with self.assertRaises(CONFIG.PlanError):
                CONFIG.build_plan(self.args())

    def test_a_inventory_cannot_be_used_for_b(self):
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args(radio_inventory=str(self.peer)))

    def test_missing_unit_or_hardware_binding_rejected(self):
        original = self.radio.read_text()
        for field in ("unit_id", "usb", "radio_identity"):
            self.radio.write_text(original)
            self.modify_radio(self.radio, lambda value: value.pop(field))
            with self.assertRaises(CONFIG.PlanError):
                CONFIG.build_plan(self.args())
        self.radio.write_text(original)
        self.modify_radio(self.radio, lambda value: value["usb"].pop("serial"))
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args())

    def test_explicit_binding_must_match_not_device_existence(self):
        for field in ("radio_usb_serial", "radio_vendor_id", "radio_product_id",
                      "radio_stable_path", "radio_public_fingerprint"):
            with self.subTest(field=field), self.assertRaises(CONFIG.PlanError):
                CONFIG.build_plan(self.args(**{field: "wrong"}))
        self.assertFalse(CONFIG.build_plan(self.args())["hardware_verified"])

    def test_fingerprint_hash_validated(self):
        self.modify_radio(self.radio, lambda value: value["radio_identity"].update(public_key_sha256="sha256:" + "0" * 64))
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args())

    def test_peer_duplicate_identity_and_serial_rejected(self):
        original = self.peer.read_text()
        for field in ("radio_identity", "usb"):
            self.peer.write_text(original)
            own = json.loads(self.radio.read_text())
            self.modify_radio(self.peer, lambda value: value.update({field: own[field]}))
            with self.assertRaises(CONFIG.PlanError):
                CONFIG.build_plan(self.args())

    def test_peer_comparison_optional_bounded_and_explicit(self):
        plan = CONFIG.build_plan(self.args(peer_radio_inventory=[]))
        self.assertFalse(plan["fleet_comparison_performed"])
        self.assertEqual(plan["compared_peer_units"], [])
        for peers in ([str(self.peer)] * 17, [str(self.peer)] * 2):
            with self.assertRaises(CONFIG.PlanError):
                CONFIG.build_plan(self.args(peer_radio_inventory=peers))

    def test_paths_are_contained_and_not_inferred(self):
        for changes in ({"repo_root": "/etc/sage-commons"}, {"app_root": "/home/radxa/other"},
                        {"evidence_root": "/home/radxa/sage-commons/evidence/unit-a-provisioning"},
                        {"repo_root": "/home/radxa/../root/repo"}, {"audio_stable_path": "/dev/snd/controlC0"},
                        {"audio_stable_path": "/dev/snd/by-id/usb-AUDIO-A-00"}):
            with self.subTest(changes=changes), self.assertRaises(CONFIG.PlanError):
                CONFIG.build_plan(self.args(**changes))

    def test_no_symlink_or_secret_filename_inputs(self):
        link = self.root / "link"
        link.symlink_to(self.root / "b", target_is_directory=True)
        for filename in (str(link / "meshcore-radio-public.json"), str(self.root / "agent.key")):
            with self.assertRaises(CONFIG.PlanError):
                CONFIG.build_plan(self.args(radio_inventory=filename))

    def test_duplicate_json_and_oversize_rejected(self):
        for data in ('{"schema":"x","schema":"y"}', " " * 16385):
            self.radio.write_text(data)
            with self.assertRaises(ValueError):
                CONFIG.build_plan(self.args())

    def test_failure_does_not_echo_input_or_emit_partial_json(self):
        self.radio.write_text("sensitive-invalid-input")
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(CONFIG.main(self.arguments), 2)
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("sensitive-invalid-input", errors.getvalue())

    def test_audio_rule_is_exact_normal_firmware_binding(self):
        result = subprocess.run([sys.executable, str(SCRIPT), *self.arguments,
                                 "--format", "audio-udev", "--audio-vendor-id", "303a",
                                 "--audio-product-id", "8000"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         'SUBSYSTEM=="sound", KERNEL=="controlC*", ENV{ID_VENDOR_ID}=="303a", '
                         'ENV{ID_MODEL_ID}=="8000", ENV{ID_SERIAL_SHORT}=="AUDIO-B", '
                         'GROUP="sage-lantern", MODE="0660", SYMLINK+="sage-lantern-audio-control"\n')
        self.assertNotIn("flash", result.stdout)

    def test_audio_export_missing_ids_and_activation_options_fail_closed(self):
        for extra in ([], ["--audio-vendor-id", "303a"], ["--audio-product-id", "8000"],
                      ["--audio-vendor-id", "303a", "--audio-product-id", "8000",
                       "--activation-staging-root", str(self.root)]):
            with self.subTest(extra=extra):
                result = subprocess.run([sys.executable, str(SCRIPT), *self.arguments,
                                         "--format", "audio-udev", *extra], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_audio_ids_and_serial_cannot_inject_rules(self):
        for field, values in (("audio_vendor_id", ("303A", "*", "12345", '303a", RUN+="id')),
                              ("audio_product_id", ("", "800*", "8000\n")),
                              ("audio_usb_serial", ("*", "AUDIO-?", 'AUDIO-B", RUN+="id', "$env{X}"))):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(CONFIG.PlanError):
                    args = self.args(audio_vendor_id="303a", audio_product_id="8000")
                    setattr(args, field, value)
                    CONFIG.build_plan(args)

    def test_other_unit_cannot_export_known_a_audio_binding(self):
        with self.assertRaises(CONFIG.PlanError):
            CONFIG.build_plan(self.args(audio_usb_serial="SAGE-LANTERN-A-AUDIO",
                                       audio_stable_path="/dev/snd/by-id/usb-SAGE-LANTERN-A-AUDIO-00"))

    def test_audio_rule_cli_no_file_mutations(self):
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result = subprocess.run([sys.executable, str(SCRIPT), *self.arguments,
                                 "--format", "audio-udev", "--audio-vendor-id", "303a",
                                 "--audio-product-id", "8000"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})


if __name__ == "__main__":
    unittest.main()
