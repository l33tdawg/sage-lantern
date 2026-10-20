"""Run source-level settlement fault replays and print a JSON record.

This exercises the disabled journal and settlement contracts. It touches only a
temporary directory and makes no device, service or network call.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "software/security/desktop_loader_candidate"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


L = load("lifecycle_settlement_for_replay", "lifecycle_settlement.py")
J = load("settlement_journal_for_replay", "settlement_journal.py")
A = load("settlement_anchor_for_replay", "settlement_anchor.py")

MACHINE = "a" * 32
BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_BOOT = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
CAPTURE = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"
LAUNCH = "eeeeeeee-ffff-aaaa-bbbb-cccccccccccc"
BINDING = L.LaunchBinding(MACHINE, BOOT, CAPTURE, 7, "1" * 64, LAUNCH)
RESOURCES = (
    L.ResourceRef("pidfd", "pidfd:4242"),
    L.ResourceRef("cgroup", "/sys/fs/cgroup/sage-session-7"),
    L.ResourceRef("socket", "unix:/run/sage/native-7"),
)
OBLIGATION = L.SettlementObligation(BINDING, RESOURCES)


def proof(boot=BOOT, evidence="2" * 64, obligation=OBLIGATION):
    digest = J.obligation_digest(obligation)
    observations = tuple(
        L.ResourceObservation(item, state, evidence)
        for item, state in zip(obligation.resources,
                               ("dead", "empty", "closed")))
    unsigned = L.SettlementProof(digest, MACHINE, boot, "native-owner",
                                 observations, "0" * 64)
    return L.SettlementProof(digest, MACHINE, boot, "native-owner",
                             observations, unsigned.expected_sha256())


class AuthorityBackend:
    def __init__(self, value):
        self.value = value

    def read_settlement_proof(self, obligation):
        return self.value


def fresh():
    tmp = tempfile.mkdtemp(prefix="settlement-replay-")
    backend = J.FileJournalBackend(tmp)
    authority = L.SettlementAuthority(AuthorityBackend(proof()))
    return tmp, backend, J.SettlementJournal(backend, authority)


def state(backend):
    record = backend.read(J.obligation_digest(OBLIGATION))
    return None if record is None else record.state


def case(name, function):
    try:
        detail = function()
        return {"case": name, "ok": True, **detail}
    except Exception as error:
        return {"case": name, "ok": False, "error": str(error)}


def normal():
    tmp, backend, journal = fresh()
    try:
        assert journal.record_attempt(OBLIGATION).status == "attempted"
        outcome = journal.settle(OBLIGATION, proof())
        return {"status": outcome.status, "record_state": state(backend),
                "revision": outcome.record.revision}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def create_fail_before():
    tmp, backend, journal = fresh()
    original = J.os.replace
    J.os.replace = lambda *_args: (_ for _ in ()).throw(OSError("before"))
    try:
        outcome = journal.record_attempt(OBLIGATION)
        return {"status": outcome.status, "record_state": state(backend)}
    finally:
        J.os.replace = original
        shutil.rmtree(tmp, ignore_errors=True)


def create_fail_after():
    tmp, backend, journal = fresh()
    original = J.os.replace

    def replace_then_fail(source, target):
        original(source, target)
        raise OSError("after")

    J.os.replace = replace_then_fail
    try:
        outcome = journal.record_attempt(OBLIGATION)
        return {"status": outcome.status, "record_state": state(backend)}
    finally:
        J.os.replace = original
        shutil.rmtree(tmp, ignore_errors=True)


def cas_fail_before():
    tmp, backend, journal = fresh()
    journal.record_attempt(OBLIGATION)
    original = J.os.replace
    J.os.replace = lambda *_args: (_ for _ in ()).throw(OSError("before"))
    try:
        outcome = journal.settle(OBLIGATION, proof())
        return {"status": outcome.status, "record_state": state(backend)}
    finally:
        J.os.replace = original
        shutil.rmtree(tmp, ignore_errors=True)


def cas_fail_after():
    tmp, backend, journal = fresh()
    journal.record_attempt(OBLIGATION)
    original = J.os.replace

    def replace_then_fail(source, target):
        original(source, target)
        raise OSError("after")

    J.os.replace = replace_then_fail
    try:
        first = journal.settle(OBLIGATION, proof())
        J.os.replace = original
        retry = journal.settle(OBLIGATION, proof())
        return {"status": first.status, "retry": retry.status,
                "record_state": state(backend)}
    finally:
        J.os.replace = original
        shutil.rmtree(tmp, ignore_errors=True)


def cas_conflict():
    tmp, backend, journal = fresh()
    journal.record_attempt(OBLIGATION)
    original = backend.compare_exchange
    backend.compare_exchange = lambda before, after: None
    try:
        outcome = journal.settle(OBLIGATION, proof())
        return {"status": outcome.status, "record_state": state(backend)}
    finally:
        backend.compare_exchange = original
        shutil.rmtree(tmp, ignore_errors=True)


def duplicate_settle():
    tmp, backend, journal = fresh()
    journal.record_attempt(OBLIGATION)
    first = journal.settle(OBLIGATION, proof())
    second = journal.settle(OBLIGATION, proof())
    try:
        return {"status": second.status, "first": first.status,
                "second": second.status,
                "revision": second.record.revision, "record_state": state(backend)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def different_proof():
    tmp, backend, journal = fresh()
    journal.record_attempt(OBLIGATION)
    journal.settle(OBLIGATION, proof(evidence="2" * 64))
    second = journal.settle(OBLIGATION, proof(evidence="3" * 64))
    try:
        return {"status": second.status, "second": second.status,
                "record_state": state(backend)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def new_boot_full_evidence():
    tmp, backend, journal = fresh()
    journal.record_attempt(OBLIGATION)
    outcome = journal.settle(OBLIGATION, proof(boot=OTHER_BOOT))
    try:
        return {"status": outcome.status, "record_state": state(backend)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def no_child_identity():
    obligation = L.SettlementObligation(
        BINDING, (L.ResourceRef("socket", "unix:/run/sage/native-7"),))
    outcome = L.SettlementAuthority(AuthorityBackend(proof())).settle(obligation)
    return {"status": outcome.status, "reason": outcome.reason}


def unavailable():
    outcome = J.SettlementJournal().record_attempt(OBLIGATION)
    return {"status": outcome.status, "reason": outcome.reason}


class Counter:
    def __init__(self, value):
        self.value = value

    def read(self):
        return A.CounterValue(self.value)

    def advance(self, expected):
        if expected != self.value:
            raise A.CounterRefused("counter_race")
        self.value += 1
        return A.CounterValue(self.value)


def counter_write_failure():
    tmp = tempfile.mkdtemp(prefix="settlement-counter-replay-")
    try:
        counter = Counter(4)
        store = A.FileAnchorStore(tmp, counter=counter)
        before = store.create(A.AnchorValue(1, "a" * 64, 0))
        write = store._write
        store._write = lambda _value: (_ for _ in ()).throw(
            A.AnchorRefused("simulated_counter_write_failure"))
        try:
            try:
                store.compare_exchange(
                    before, A.AnchorValue(2, "b" * 64, 0))
            except A.AnchorRefused as error:
                result = str(error)
            else:
                result = "unexpected_success"
        finally:
            store._write = write
        rejection = None
        try:
            store.read()
        except A.AnchorRefused as error:
            rejection = str(error)
        return {"write": result, "counter": counter.value,
                "readback": rejection}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def stale_counter():
    tmp = tempfile.mkdtemp(prefix="settlement-counter-stale-")
    try:
        counter = Counter(9)
        store = A.FileAnchorStore(tmp, counter=counter)
        store.create(A.AnchorValue(1, "a" * 64, 0))
        counter.value = 8
        try:
            store.read()
        except A.AnchorRefused as error:
            return {"status": "refused", "reason": str(error)}
        return {"status": "unexpected_success"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


CASES = [
    ("normal", normal),
    ("create_failure_before_replace", create_fail_before),
    ("create_failure_after_replace", create_fail_after),
    ("cas_failure_before_replace", cas_fail_before),
    ("cas_failure_after_replace", cas_fail_after),
    ("cas_conflict", cas_conflict),
    ("duplicate_settle", duplicate_settle),
    ("different_proof", different_proof),
    ("new_boot_full_evidence", new_boot_full_evidence),
    ("no_child_identity", no_child_identity),
    ("unavailable_backend", unavailable),
    ("counter_write_failure", counter_write_failure),
    ("stale_counter", stale_counter),
]


def main():
    result = {
        "schema": "sage.lantern.settlement-fault-replay.v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"),
        "scope": "source contract only; no native owner, device or service",
        "cases": [case(name, function) for name, function in CASES],
    }
    result["passed"] = all(item.get("ok") for item in result["cases"])
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
