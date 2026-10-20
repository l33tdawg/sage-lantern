"""Strict loader-broker framing and descriptor handoff controls."""
from array import array
import json
import os
import socket
import threading

import pytest

from sage_lantern.loader_broker import LoaderBroker, LoaderBrokerError, SCHEMA


def receive(channel):
    body, ancillary, flags, _ = channel.recvmsg(4096, socket.CMSG_SPACE(8))
    assert flags == 0
    return json.loads(body), ancillary


def send(channel, value, fd=None):
    ancillary = [] if fd is None else [
        (socket.SOL_SOCKET, socket.SCM_RIGHTS, array("i", [fd]))]
    channel.sendmsg(
        [json.dumps(value, sort_keys=True).encode() + b"\n"], ancillary)


def server(channel, response, release=True):
    try:
        request, ancillary = receive(channel)
        assert not ancillary
        assert request["action"] == "stage"
        fd = os.open("/dev/null", os.O_RDONLY)
        send(channel, response, fd)
        os.close(fd)
        if release:
            request, ancillary = receive(channel)
            assert not ancillary
            assert request["action"] == "release"
            send(channel, {"schema": SCHEMA, "status": "released"})
    finally:
        channel.close()


def staged_response(**changes):
    value = dict(schema=SCHEMA, status="stopped", pid=123,
                 start_ticks=456, cookie=789)
    value.update(changes)
    return value


def test_stage_receives_exact_pidfd_and_release_matches_cookie():
    client, server_side = socket.socketpair()
    worker = threading.Thread(target=server, args=(server_side, staged_response()))
    worker.start()
    broker = LoaderBroker(client)
    child = broker.stage(launch_id="launch", capture_id="capture",
                         generation=7, policy_sha256="a" * 64)
    assert (child.pid, child.start_ticks, child.cookie) == (123, 456, 789)
    assert os.fstat(child.pidfd)
    broker.release()
    worker.join(2)
    assert not worker.is_alive()
    os.close(child.pidfd)
    broker.close()


@pytest.mark.parametrize("response", [
    staged_response(status="running"),
    staged_response(pid=1),
    staged_response(pid=True),
    staged_response(start_ticks=0),
    staged_response(cookie=0),
    {**staged_response(), "schema": "other"},
])
def test_invalid_stage_result_refuses(response):
    client, server_side = socket.socketpair()
    worker = threading.Thread(target=server, args=(server_side, response, False))
    worker.start()
    broker = LoaderBroker(client)
    with pytest.raises(LoaderBrokerError):
        broker.stage(launch_id="launch", capture_id="capture",
                     generation=7, policy_sha256="a" * 64)
    worker.join(2)
    broker.close()


def test_missing_pidfd_refuses():
    client, server_side = socket.socketpair()
    def worker():
        try:
            receive(server_side)
            send(server_side, staged_response())
        finally:
            server_side.close()
    thread = threading.Thread(target=worker)
    thread.start()
    with pytest.raises(LoaderBrokerError, match="broker_stage_result_invalid"):
        LoaderBroker(client).stage(
            launch_id="launch", capture_id="capture",
            generation=7, policy_sha256="a" * 64)
    thread.join(2)
    client.close()


def test_durable_record_must_succeed_before_release():
    events = []
    client, server_side = socket.socketpair()
    def worker():
        try:
            receive(server_side)
            fd = os.open("/dev/null", os.O_RDONLY)
            send(server_side, staged_response(), fd)
            os.close(fd)
            request, ancillary = receive(server_side)
            assert not ancillary
            events.append(request["action"])
            assert request["action"] == "release"
            send(server_side, {"schema": SCHEMA, "status": "released"})
        finally:
            server_side.close()
    thread = threading.Thread(target=worker)
    thread.start()
    broker = LoaderBroker(client)
    child = broker.stage_until_recorded(
        lambda staged, request: (
            events.append("recorded") or
            staged.cookie == 789 and request.launch_id == "launch"),
        launch_id="launch", capture_id="capture",
        generation=7, policy_sha256="a" * 64)
    assert events == ["recorded", "release"]
    thread.join(2)
    os.close(child.pidfd)
    broker.close()


@pytest.mark.parametrize("failure", [False, RuntimeError("journal unavailable")])
def test_failed_durable_record_aborts_stopped_child(failure):
    events = []
    client, server_side = socket.socketpair()
    def worker():
        try:
            receive(server_side)
            fd = os.open("/dev/null", os.O_RDONLY)
            send(server_side, staged_response(), fd)
            os.close(fd)
            request, ancillary = receive(server_side)
            assert not ancillary
            events.append(request["action"])
            assert request["action"] == "abort"
            send(server_side, {"schema": SCHEMA, "status": "aborted"})
        finally:
            server_side.close()
    thread = threading.Thread(target=worker)
    thread.start()
    broker = LoaderBroker(client)
    def record(_, request):
        assert request.capture_id == "capture"
        if isinstance(failure, BaseException):
            raise failure
        return failure
    with pytest.raises((LoaderBrokerError, RuntimeError)):
        broker.stage_until_recorded(
            record, launch_id="launch", capture_id="capture",
            generation=7, policy_sha256="a" * 64)
    assert events == ["abort"]
    assert broker.staged is None
    thread.join(2)
    broker.close()
