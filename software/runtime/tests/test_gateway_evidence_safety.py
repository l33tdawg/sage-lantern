import hashlib
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage_lantern import gateway_evidence
from sage_lantern.mesh_voice_ipc import receive
from sage_lantern.public_mesh_protocol import canonical


class BytesConnection:
    def __init__(self, data, elapsed, increment=0):
        self.data, self.elapsed, self.increment = bytearray(data), elapsed, increment
        self.sent = bytearray()
        self.timeouts = []
    def settimeout(self, value):
        self.timeouts.append(value)
    def recv(self, size):
        self.elapsed[0] += self.increment
        value = bytes(self.data[:1])
        del self.data[:1]
        return value
    def sendall(self, data):
        self.sent.extend(data)


def encoded(value):
    data = canonical(value)
    return struct.pack("!I", len(data)) + data


def test_trickled_reply_cannot_extend_monotonic_deadline(monkeypatch):
    elapsed = [0]
    monkeypatch.setattr(gateway_evidence, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    raw = BytesConnection(encoded(dict(current=True)), elapsed, 0.25)
    with pytest.raises(Exception):
        receive(gateway_evidence.DeadlineConnection(raw, 2), 512)
    assert elapsed[0] == 2 and raw.timeouts[-1] <= 0.25


def test_inspection_overrun_cannot_emit_positive_evidence(monkeypatch):
    elapsed = [0]
    monkeypatch.setattr(gateway_evidence, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    settings = dict(synthetic="fixed")
    raw = BytesConnection(encoded(dict(nonce="a"*32, config_digest=hashlib.sha256(canonical(settings)).hexdigest())), elapsed)
    def inspect(value):
        elapsed[0] = 2.01
        return True
    with pytest.raises(Exception):
        gateway_evidence.handle(raw, config_reader=lambda: dict(public_gateway=settings), inspect=inspect,
            peer_reader=lambda connection: (1, 0))
    assert not raw.sent


def test_notify_readiness_and_capabilities_are_separate_from_mesh_owner():
    root = Path(__file__).resolve().parents[3]
    helper = (root / "software/systemd/sage-lantern-gateway-evidence.service").read_text()
    daemon = (root / "software/systemd/sage-lantern-mesh-runtime.service").read_text()
    assert "Type=notify\nNotifyAccess=main" in helper
    assert "CAP_SYS_PTRACE" in helper and "CAP_SYS_PTRACE" not in daemon
    assert "SystemCallFilter=~ptrace process_vm_readv process_vm_writev" in helper
    assert "ReadOnlyPaths=/etc/sage-lantern" in helper and "InaccessiblePaths=-/var/lib/sage-lantern" in helper
    assert "After=sage-lantern-gateway-evidence.service" in daemon
