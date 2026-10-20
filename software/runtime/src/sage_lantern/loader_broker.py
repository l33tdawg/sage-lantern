"""Strict client for the root desktop-loader broker.

The broker stages one stopped child and passes its pidfd back. This module
grants no launch authority; the caller must record the settlement attempt and
explicitly release the exact staged child.
"""
from __future__ import annotations

from dataclasses import dataclass
import array
import json
import socket


SCHEMA = "sage.lantern.desktop-loader-broker.v1"


class LoaderBrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class StagedChild:
    pid: int
    pidfd: int
    start_ticks: int
    cookie: int


def _message(value):
    if type(value) is not dict or value.get("schema") != SCHEMA:
        raise LoaderBrokerError("broker_schema_invalid")
    return value


def _close_fds(ancillary):
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            for fd in array.array("i", data):
                socket.close(fd)


class LoaderBroker:
    """One staged launch over a connected Unix socket."""
    def __init__(self, channel):
        self.channel = channel
        self.launch_id = None
        self.staged = None

    @classmethod
    def connect(cls, path, timeout=3.0):
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        channel.settimeout(timeout)
        try:
            channel.connect(path)
        except BaseException:
            channel.close()
            raise
        return cls(channel)

    def _send(self, value, *, fd=None):
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ancillary = []
        if fd is not None:
            ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                          array.array("i", [fd]))]
        self.channel.sendmsg([body + b"\n"], ancillary)

    def _receive(self):
        body, ancillary, flags, _ = self.channel.recvmsg(
            4096, socket.CMSG_SPACE(array.array("i").itemsize))
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            _close_fds(ancillary)
            raise LoaderBrokerError("broker_message_truncated")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            _close_fds(ancillary)
            raise LoaderBrokerError("broker_json_invalid") from None
        try:
            return _message(value), ancillary
        except BaseException:
            _close_fds(ancillary)
            raise

    def stage(self, *, launch_id, capture_id, generation, policy_sha256):
        if self.staged is not None or self.launch_id is not None:
            raise LoaderBrokerError("broker_already_staged")
        self._send(dict(
            schema=SCHEMA, action="stage", launch_id=launch_id,
            capture_id=capture_id, generation=generation,
            policy_sha256=policy_sha256))
        message, ancillary = self._receive()
        fds = []
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                fds.extend(array.array("i", data))
        if (message.get("status") != "stopped" or len(fds) != 1
                or type(message.get("pid")) is not int
                or message["pid"] <= 1
                or type(message.get("start_ticks")) is not int
                or message["start_ticks"] < 1
                or type(message.get("cookie")) is not int
                or not 0 < message["cookie"] < 2**64):
            for fd in fds:
                socket.close(fd)
            raise LoaderBrokerError("broker_stage_result_invalid")
        self.launch_id = launch_id
        self.staged = StagedChild(
            message["pid"], fds[0], message["start_ticks"], message["cookie"])
        return self.staged

    def release(self):
        if self.launch_id is None or self.staged is None:
            raise LoaderBrokerError("broker_not_staged")
        self._send(dict(
            schema=SCHEMA, action="release", launch_id=self.launch_id,
            cookie=self.staged.cookie))
        message, ancillary = self._receive()
        if ancillary:
            _close_fds(ancillary)
        if ancillary or message.get("status") != "released":
            raise LoaderBrokerError("broker_release_result_invalid")

    def stage_until_recorded(self, record, *, launch_id, capture_id,
                             generation, policy_sha256):
        """Persist the stopped-child obligation before permitting release."""
        child = self.stage(
            launch_id=launch_id, capture_id=capture_id,
            generation=generation, policy_sha256=policy_sha256)
        try:
            recorded = record(child)
        except BaseException:
            try:
                self.abort()
            finally:
                socket.close(child.pidfd)
                self.staged = None
            raise
        if recorded is not True:
            try:
                self.abort()
            finally:
                socket.close(child.pidfd)
                self.staged = None
            raise LoaderBrokerError("settlement_attempt_not_recorded")
        self.release()
        return child

    def abort(self):
        if self.launch_id is not None and self.staged is not None:
            self._send(dict(
                schema=SCHEMA, action="abort", launch_id=self.launch_id,
                cookie=self.staged.cookie))
            message, ancillary = self._receive()
            if ancillary:
                _close_fds(ancillary)
            if ancillary or message.get("status") != "aborted":
                raise LoaderBrokerError("broker_abort_result_invalid")

    def close(self):
        self.channel.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
