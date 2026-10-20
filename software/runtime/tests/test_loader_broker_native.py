"""Compiled loader-core handshake over the production broker protocol."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time

import pytest
import socket

from sage_lantern.loader_broker import LoaderBroker, LoaderBrokerError


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "software/security/desktop_loader_candidate"
REPLAY = ROOT / "scripts/tests/native/desktop_loader_broker_replay.c"


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    if not shutil.which("cc"):
        pytest.skip("C compiler required")
    target = tmp_path_factory.mktemp("loader-broker") / "broker"
    subprocess.run([
        "cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
        "-I" + str(SOURCE), str(SOURCE / "loader.c"), str(REPLAY),
        "-o", str(target)], check=True, capture_output=True, timeout=30)
    return target


def start_server(binary):
    client, server = socket.socketpair()
    process = subprocess.Popen(
        [str(binary), str(server.fileno())], pass_fds=[server.fileno()],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    server.close()
    return process, client


def test_native_loader_stages_and_releases_only_after_record(binary, tmp_path):
    process, client = start_server(binary)
    events = []
    with LoaderBroker(client) as broker:
        child = broker.stage_until_recorded(
            lambda staged: events.append(staged.pid) or os.kill(staged.pid, 0) is None,
            launch_id="launch", capture_id="capture",
            generation=7, policy_sha256="a" * 64)
        assert os.fstat(child.pidfd)
        os.close(child.pidfd)
    assert process.wait(3) == 0
    assert events and events[0] > 1


def test_native_loader_aborts_stopped_child_when_record_refuses(binary, tmp_path):
    process, client = start_server(binary)
    with LoaderBroker(client) as broker:
        with pytest.raises(LoaderBrokerError, match="settlement_attempt_not_recorded"):
            broker.stage_until_recorded(
                lambda _: False,
                launch_id="launch", capture_id="capture",
                generation=7, policy_sha256="a" * 64)
    assert process.wait(3) == 1
