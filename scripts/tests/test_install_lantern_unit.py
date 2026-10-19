from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = (ROOT / "scripts" / "install-lantern-unit.sh").read_text(encoding="utf-8")


def test_local_ai_native_builds_are_static_and_source_locked() -> None:
    assert "software/cache/sources/llama.cpp-b9637.tar.gz" in INSTALLER
    assert "software/cache/sources/whisper.cpp-v1.8.6.tar.gz" in INSTALLER
    assert INSTALLER.count("-DBUILD_SHARED_LIBS=OFF") == 2
    assert "--target llama-server" in INSTALLER
    assert "--target whisper-cli" in INSTALLER
    assert 'bundle/linux-arm64/llama.cpp/."' not in INSTALLER
    assert "-DLLAMA_BUILD_UI=OFF" in INSTALLER
    assert "-DLLAMA_USE_PREBUILT_UI=OFF" in INSTALLER
    assert 'build_jobs="${SAGE_LANTERN_BUILD_JOBS:-2}"' in INSTALLER
    assert INSTALLER.count('-j "${build_jobs}"') == 2


def test_piper_uses_numpy_one_x_abi() -> None:
    assert "numpy==1.26.4" in INSTALLER


def test_fresh_install_includes_vision_projector() -> None:
    assert 'install -m 0644 "${source_root}/software/cache/models/mmproj-BF16.gguf" "${install_root}/models/mmproj-BF16.gguf"' in INSTALLER


def run_preflight(root: Path, *, outputs=("", ""), failure=None):
    source = INSTALLER.split("<<'FRESH_TARGET_PY'\n", 1)[1].split("\nFRESH_TARGET_PY", 1)[0]
    results = [subprocess.CompletedProcess([], 0, output, "") for output in outputs]
    with patch.object(sys, "argv", ["preflight-test", str(root)]), \
            patch("subprocess.run", side_effect=failure or results) as execute:
        exec(compile(source, "install-lantern-unit.sh:FRESH_TARGET_PY", "exec"), {})
    return execute.call_args_list


def test_help_and_unknown_arguments_do_not_run_target_commands(tmp_path):
    for arguments, expected in ((["--help"], 0), (["-h"], 0), (["--dry-run"], 2),
                                (["--unknown"], 2), (["anything"], 2),
                                (["--help", "--dry-run"], 2), (["--"], 2)):
        result = subprocess.run(["/bin/bash", str(ROOT / "scripts/install-lantern-unit.sh"), *arguments],
                                env={"PATH": str(tmp_path)}, capture_output=True, text=True)
        assert result.returncode == expected
        assert "Usage:" in result.stdout + result.stderr
        assert "command not found" not in result.stderr
        assert "Run as root" not in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_bash_syntax_only():
    result = subprocess.run(["/bin/bash", "-n", str(ROOT / "scripts/install-lantern-unit.sh")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_fresh_preflight_is_read_only_and_queries_loaded_and_installed_units(tmp_path):
    calls = run_preflight(tmp_path, outputs=("ssh.service loaded active running SSH\n",
                                            "ssh.service enabled enabled\n"))
    assert list(tmp_path.iterdir()) == []
    assert [entry.args[0] for entry in calls] == [
        ["systemctl", operation, "--all", "--plain", "--no-legend", "--no-pager"]
        for operation in ("list-units", "list-unit-files")]
    assert all(entry.kwargs["timeout"] == 10 for entry in calls)


@pytest.mark.parametrize("name", ["opt/sage-lantern", "var/lib/sage-lantern", "etc/sage-lantern",
                                    "etc/sage-lantern-device-lock.enabled",
                                    "etc/sage-lantern-vault-setup.enabled", "run/sage-lantern-suspend"])
@pytest.mark.parametrize("kind", ["directory", "file", "dangling", "symlink"])
def test_existing_roots_and_markers_refuse_without_changes(tmp_path, name, kind):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "directory":
        path.mkdir()
    elif kind == "file":
        path.write_text("preserve")
    else:
        target = tmp_path / "target"
        if kind == "symlink":
            target.mkdir()
        path.symlink_to(target)
    before = path.lstat()
    with pytest.raises(SystemExit, match="Fresh-target preflight refused"), \
            patch("subprocess.run", side_effect=AssertionError("target must be rejected before systemctl")):
        source = INSTALLER.split("<<'FRESH_TARGET_PY'\n", 1)[1].split("\nFRESH_TARGET_PY", 1)[0]
        with patch.object(sys, "argv", ["preflight-test", str(tmp_path)]):
            exec(compile(source, "preflight", "exec"), {})
    assert path.lstat() == before
    if kind == "file":
        assert path.read_text() == "preserve"


@pytest.mark.parametrize("directory", ["etc/systemd/system", "run/systemd/system",
                                         "usr/local/lib/systemd/system", "usr/lib/systemd/system",
                                         "lib/systemd/system", "run/systemd/transient",
                                         "etc/systemd/system.control", "run/systemd/system.control",
                                         "etc/systemd/system.attached", "run/systemd/system.attached",
                                         "run/systemd/generator", "run/systemd/generator.early",
                                         "run/systemd/generator.late"])
@pytest.mark.parametrize("artifact", ["sage-lantern-llm.service", "sage-lantern@unit-b.service.d",
                                        "multi-user.target.wants/sage-lantern-runtime.service"])
def test_units_dropins_and_dangling_dependency_links_refused(tmp_path, directory, artifact):
    path = tmp_path / directory / artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    if artifact.endswith(".d"):
        path.mkdir()
    elif ".wants/" in artifact:
        path.symlink_to(tmp_path / "missing.service")
    else:
        path.write_text("preserve unit")
    with pytest.raises(SystemExit, match="existing Lantern unit"):
        run_preflight(tmp_path)
    assert os.path.lexists(path)


def test_mask_and_prepared_metadata_are_not_a_fresh_target(tmp_path):
    mask = tmp_path / "etc/systemd/system/sage-lantern-device-auth.service"
    mask.parent.mkdir(parents=True)
    mask.symlink_to("/dev/null")
    with pytest.raises(SystemExit):
        run_preflight(tmp_path)
    assert os.readlink(mask) == "/dev/null"
    mask.unlink()
    metadata = tmp_path / "etc/sage-lantern/unit.env"
    metadata.parent.mkdir()
    metadata.write_text("UNIT_ID=unit-b\n")
    with pytest.raises(SystemExit):
        run_preflight(tmp_path)
    assert metadata.read_text() == "UNIT_ID=unit-b\n"


def test_existing_provisioning_profile_is_not_a_fresh_target(tmp_path):
    profile = tmp_path / "etc/sage/provisioning-profile.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text("schema: sage.provisioning-profile.v1\n")
    with pytest.raises(SystemExit, match="existing /etc/sage/provisioning-profile.yaml"):
        run_preflight(tmp_path)
    assert profile.read_text() == "schema: sage.provisioning-profile.v1\n"


@pytest.mark.parametrize("outputs", [("sage-lantern-voice-loop.service loaded active running Voice\n", ""),
                                      ("", "sage-lantern-camera.service masked enabled\n")])
def test_loaded_or_installed_units_refuse_even_without_disk_artifacts(tmp_path, outputs):
    with pytest.raises(SystemExit, match="loaded or installed"):
        run_preflight(tmp_path, outputs=outputs)
    assert list(tmp_path.iterdir()) == []


def test_systemd_failure_and_unreadable_configuration_fail_closed(tmp_path):
    for failure in (OSError(), subprocess.TimeoutExpired("systemctl", 10),
                    [subprocess.CompletedProcess([], 1, "", "failure")]):
        with pytest.raises(SystemExit, match="cannot establish"):
            run_preflight(tmp_path, failure=failure)
    directory = tmp_path / "etc/systemd/system"
    directory.mkdir(parents=True)
    with patch("os.scandir", side_effect=PermissionError()), pytest.raises(SystemExit, match="cannot inspect"):
        run_preflight(tmp_path)


def test_linked_configuration_directory_refuses_but_usrmerge_path_can_be_read(tmp_path):
    directory = tmp_path / "usr/lib/systemd/system"
    directory.mkdir(parents=True)
    (tmp_path / "lib").symlink_to(tmp_path / "usr/lib")
    run_preflight(tmp_path)
    (directory / "multi-user.target.wants").symlink_to(tmp_path / "missing")
    with pytest.raises(SystemExit, match="linked unit configuration"):
        run_preflight(tmp_path)


def test_preflight_and_atomic_claim_precede_account_and_install_mutations():
    first_check = INSTALLER.index("\nfresh_target_preflight\n")
    verify = INSTALLER.index('python3 "${source_root}/scripts/verify-lantern-bundle.py"')
    second_check = INSTALLER.index("\nfresh_target_preflight\n", first_check + 1)
    claim = INSTALLER.index("\nclaim_install_root\n")
    assert first_check < verify < second_check < claim < INSTALLER.index("\nif ! id sage-lantern")
    assert claim < INSTALLER.index("\ninstall -d")
    assert INSTALLER.index("Unsupported arguments") < INSTALLER.index('${EUID}')


def test_atomic_claim_has_one_winner_and_preserves_failed_install(tmp_path):
    function = INSTALLER.split("claim_install_root() {\n", 1)[1].split("\n}\nclaim_install_root", 1)[0]
    harness = 'install_root="$1"\nclaim_install_root() {\n' + function + '\n}\nclaim_install_root\n'
    destination = tmp_path / "installation"

    def claim():
        return subprocess.run(["/bin/bash", "-c", harness, "claim", str(destination)],
                              capture_output=True, text=True)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda unused: claim(), range(2)))
    assert sorted(result.returncode for result in results) == [0, 2]
    sentinel = destination / "partial-state"
    sentinel.write_text("review, do not remove")
    assert claim().returncode == 2
    assert sentinel.read_text() == "review, do not remove"


def test_final_guidance_is_base_only_and_does_not_create_provisioned_manually():
    tail = INSTALLER.split("Installed the sterile Lantern runtime", 1)[1]
    assert "PROVISIONED" not in tail
    assert "no services enabled" in tail
    assert "provision-lantern-unit.sh prepare --unit-id" in tail
    assert "independent identity/recovery and verified inventory" in tail
    assert "Secure app and owner-PIN setup are not provided" in tail
