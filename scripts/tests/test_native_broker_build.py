"""Linux-only fitted-broker build preflight."""
from pathlib import Path
import os
import platform
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/lantern/build-native-broker.sh"
CONTAINER_SCRIPT = ROOT / "scripts/lantern/build-native-broker-in-container.sh"


def test_non_linux_build_is_refused_without_output(tmp_path):
    output = tmp_path / "broker"
    result = subprocess.run(
        [str(SCRIPT), str(output)], capture_output=True, text=True)
    if platform.system() == "Linux":
        assert result.returncode in (0, 4, 5)
        if result.returncode == 0:
            assert output.is_file() and output.stat().st_mode & 0o111
            usage = subprocess.run([str(output)], capture_output=True, text=True)
            assert usage.returncode == 64
            assert "usage: sage-lantern-desktop-loader" in usage.stderr
    else:
        assert result.returncode == 3
        assert result.stderr.strip() == "linux_build_required"
        assert not output.exists()


def test_container_builder_is_executable_and_shell_valid():
    assert CONTAINER_SCRIPT.is_file()
    assert CONTAINER_SCRIPT.stat().st_mode & 0o111
    subprocess.run(["sh", "-n", str(CONTAINER_SCRIPT)], check=True)


def test_linux_child_identity_replay(tmp_path):
    if platform.system() != "Linux" or os.geteuid() != 0:
        pytest.skip("requires a root Linux replay host")
    if not shutil.which("cc") or not shutil.which("pkg-config"):
        pytest.skip("C toolchain required")
    if subprocess.run(
            ["pkg-config", "--exists", "libbpf", "openssl"]).returncode:
        pytest.skip("libbpf and OpenSSL development files required")
    binary = tmp_path / "child-identity"
    subprocess.run([
        "cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
        "-I" + str(ROOT / "software/security/desktop_loader_candidate"),
        str(ROOT / "software/security/desktop_loader_candidate/loader.c"),
        str(ROOT / "scripts/tests/native/desktop_loader_child_identity_replay.c"),
        *subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "libbpf", "openssl"],
            text=True).split(),
        "-lelf", "-lz", "-o", str(binary),
    ], check=True, capture_output=True)
    result = subprocess.run([str(binary)], check=True, capture_output=True,
                            text=True)
    assert "child identity checks passed" in result.stdout
    assert "child_released=false" in result.stdout
