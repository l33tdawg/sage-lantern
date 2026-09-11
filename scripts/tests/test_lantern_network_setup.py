import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/lantern-network-setup.py"
SPEC = importlib.util.spec_from_file_location("lantern_network_setup", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TOKEN = "A" * 43


@pytest.fixture
def setup():
    clock = [100.0]
    adapter = Mock()
    adapter.status.return_value = dict(connected=False, ssid=None)
    adapter.scan.return_value = [{"ssid": "test", "signal": 75, "security": "wpa-psk"},
                                 {"ssid": "enterprise", "signal": 50, "security": "unsupported"}]
    adapter.connect.return_value = {"connected": True, "ssid": "test"}
    broker = MODULE.NetworkSetupBroker(unit_id="unit-b", interface="wlan0", adapter=adapter,
                                      token=TOKEN, clock=lambda: clock[0])
    server = MODULE.NetworkSetupServer(broker)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    thread.start()
    try:
        yield SimpleNamespace(clock=clock, adapter=adapter, broker=broker, server=server)
    finally:
        broker.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def request(path="/status", payload=None, *, method="POST", headers=None, raw=None):
    values = {"Host": MODULE.HOST, "Origin": MODULE.ORIGIN,
              "Content-Type": "application/json", "X-Lantern-Setup": TOKEN}
    values.update(headers or {})
    values = {name: value for name, value in values.items() if value is not None}
    body = json.dumps({} if payload is None else payload).encode() if raw is None else raw
    connection = http.client.HTTPConnection(*MODULE.ADDRESS, timeout=3)
    try:
        connection.request(method, path, body=body, headers=values)
        response = connection.getresponse()
        data = response.read()
        result = json.loads(data) if data else None
        response_headers = dict(response.getheaders())
        assert "Set-Cookie" not in response_headers
        assert "Access-Control-Allow-Credentials" not in response_headers
        assert response_headers["Cache-Control"] == "no-store"
        assert TOKEN.encode() not in data
        assert b"private-password" not in data
        return response.status, result, response_headers
    finally:
        connection.close()


def test_exact_api_real_http(setup):
    assert setup.server.server_address == MODULE.ADDRESS
    assert request()[:2] == (200, dict(unit_id="unit-b", interface="wlan0", connected=False, ssid=None))
    assert request("/scan")[:2] == (200, {"networks": setup.adapter.scan.return_value})
    assert request("/connect", {"ssid": "test", "password": "private-password", "security": "wpa-psk"})[:2] == (
        200, {"connected": True, "ssid": "test"})
    setup.adapter.connect.assert_called_once_with(ssid="test", password="private-password", security="wpa-psk")
    assert request("/finish")[:2] == (200, {"finished": True})
    for path in MODULE.ROUTES:
        assert request(path)[0] == 401


@pytest.mark.parametrize("headers", [
    {"Host": "localhost:3195"}, {"Host": "evil.invalid:3195"},
    {"Origin": None}, {"Origin": "null"}, {"Origin": "http://localhost:3100"},
    {"Origin": "https://127.0.0.1:3100"}, {"Origin": MODULE.ORIGIN + "/"},
])
def test_host_and_origin_reject_before_adapter(setup, headers):
    assert request(headers=headers)[0] == 403
    assert setup.adapter.mock_calls == []


@pytest.mark.parametrize("token", [None, "", "bad", "B" * 43, TOKEN + " "])
def test_every_data_endpoint_requires_exact_capability(setup, token):
    for path in MODULE.ROUTES:
        assert request(path, headers={"X-Lantern-Setup": token})[0] == 401
    assert setup.adapter.mock_calls == []


def test_preflight_has_no_data_and_requires_exact_cors(setup):
    headers = {"X-Lantern-Setup": None, "Access-Control-Request-Method": "POST",
               "Access-Control-Request-Headers": "content-type,x-lantern-setup"}
    status, result, returned = request(method="OPTIONS", headers=headers, raw=b"")
    assert status == 204 and result is None
    assert returned["Access-Control-Allow-Origin"] == MODULE.ORIGIN
    assert returned["Access-Control-Allow-Headers"] == "Content-Type, X-Lantern-Setup"
    for change in ({"Access-Control-Request-Method": "GET"},
                   {"Access-Control-Request-Headers": "content-type,x-lantern-setup,cookie"},
                   {"Access-Control-Request-Headers": "content-type,content-type,x-lantern-setup"}):
        assert request(method="OPTIONS", headers={**headers, **change}, raw=b"")[0] == 403
    assert setup.adapter.mock_calls == []


@pytest.mark.parametrize("path", ["/status?x=1", "/status/", "/%73tatus", "/auth/login", "http://evil.invalid/status"])
def test_unknown_routes_and_proxy_targets_reject(setup, path):
    assert request(path)[0] == 404
    assert setup.adapter.mock_calls == []


def test_no_get_or_private_api(setup):
    assert request(method="GET")[0] == 405
    assert request("/profile")[0] == 404


@pytest.mark.parametrize("body", [b'{"x":1,"x":2}', b'{"ssid":"a","ssid":"b"}', b'[]',
                                   b'null', b'{"x":NaN}', b'{"x":1}', b'\xff', b'{} trailing'])
def test_strict_json_and_exact_fields(setup, body):
    assert request(raw=body)[0] == 400
    assert setup.adapter.mock_calls == []


def test_body_bounds_and_header_rejections(setup):
    assert request(raw=b" " * 4095 + b"{}")[0] == 413
    assert request(raw=b" " * 4094 + b"{}")[0] == 200
    assert request(headers={"Content-Type": "text/plain"})[0] == 415
    for name in ("Cookie", "Authorization", "Proxy-Authorization", "X-Forwarded-For", "Forwarded", "Upgrade"):
        assert request(headers={name: "secret"})[0] == 400


def test_scan_count_and_wire_size_are_bounded(setup):
    setup.adapter.scan.return_value = [{"ssid": "network-" + str(index), "signal": 50, "security": "open"}
                                      for index in range(128)]
    status, payload, headers = request("/scan")
    assert status == 200 and len(payload["networks"]) == 128
    assert int(headers["Content-Length"]) <= 32768
    setup.adapter.scan.return_value.append({"ssid": "excess", "signal": 50, "security": "open"})
    assert request("/scan")[0] == 503


def test_incomplete_request_has_absolute_read_deadline(setup):
    with socket.create_connection(MODULE.ADDRESS, timeout=3) as connection:
        connection.sendall(("POST /status HTTP/1.1\r\nHost: " + MODULE.HOST + "\r\nOrigin: " + MODULE.ORIGIN
                            + "\r\nX-Lantern-Setup: " + TOKEN
                            + "\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{").encode())
        response = connection.recv(4096)
        assert b" 400 " in response or b" 408 " in response
    assert setup.adapter.mock_calls == []


@pytest.mark.parametrize("duplicate", ["Host", "Origin", "Content-Length", "Content-Type", "X-Lantern-Setup"])
def test_duplicate_headers_reject_real_socket(setup, duplicate):
    values = {"Host": MODULE.HOST, "Origin": MODULE.ORIGIN, "Content-Type": "application/json",
              "Content-Length": "2", "X-Lantern-Setup": TOKEN}
    raw = "POST /status HTTP/1.1\r\n" + "".join(f"{name}: {value}\r\n" for name, value in values.items())
    raw += f"{duplicate}: {values[duplicate]}\r\n\r\n{{}}"
    with socket.create_connection(MODULE.ADDRESS, timeout=3) as connection:
        connection.sendall(raw.encode())
        assert b" 400 " in connection.recv(4096).split(b"\r\n", 1)[0]
    assert setup.adapter.mock_calls == []


@pytest.mark.parametrize("value", [
    dict(ssid="test", password="", security="wpa-psk"),
    dict(ssid="test", password="secret", security="open"),
    dict(ssid="test", password="password", security="enterprise"),
    dict(ssid="test", password="password", security="sae"),
    dict(ssid="test\n", password="password", security="wpa-psk"),
    dict(ssid="a" * 33, password="password", security="wpa-psk"),
    dict(ssid="test", password="password", security="wpa-psk", interface="eth0"),
])
def test_unsupported_or_invalid_connect_never_reaches_backend(setup, value):
    assert request("/connect", value)[0] == 400
    assert setup.adapter.mock_calls == []


def test_open_network_and_hex_psk(setup):
    for password, security in (("", "open"), ("a" * 64, "wpa-psk")):
        assert request("/connect", dict(ssid="test", password=password, security=security))[0] == 200


def test_ttl_and_bad_clock_permanently_revoke_without_renewal(setup):
    setup.clock[0] += 899
    assert request()[0] == 200
    setup.clock[0] += 1
    assert request()[0] == 401
    setup.clock[0] = 100
    assert request()[0] == 401


@pytest.mark.parametrize("clock", [float("nan"), 99, float("inf")])
def test_clock_failure_revokes(setup, clock):
    setup.clock[0] = clock
    assert request()[0] == 401


def test_adapter_errors_and_unexpected_secrets_are_not_returned(setup):
    setup.adapter.status.side_effect = RuntimeError("private-password")
    assert request()[:2] == (503, {"error": "network_unavailable"})
    setup.adapter.status.side_effect = None
    for value in (True, {"password": "private-password"},
                  dict(unit_id="unit-a", interface="wlan0", connected=False, ssid=None),
                  dict(unit_id="unit-b", interface="wlan0", connected=1, ssid="test")):
        setup.adapter.status.return_value = value
        assert request()[0] == 503
    setup.adapter.scan.return_value = [{"ssid": "test", "signal": True, "security": "open"}]
    assert request("/scan")[0] == 503


def test_finish_wins_over_inflight_connect_and_backend_is_serialized(setup):
    entered, release = threading.Event(), threading.Event()

    def connect(**kwargs):
        entered.set()
        assert release.wait(2)
        return {"connected": True, "ssid": "test"}

    setup.adapter.connect.side_effect = connect
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(request, "/connect", dict(ssid="test", password="password", security="wpa-psk"))
        assert entered.wait(1)
        try:
            assert request("/scan")[0] == 409
            assert request("/finish")[:2] == (200, {"finished": True})
        finally:
            release.set()
        assert future.result(timeout=2)[0] == 401
    setup.adapter.scan.assert_not_called()


def test_adapter_wait_outlives_request_read_timeout(setup):
    def connect(**kwargs):
        time.sleep(0.2)
        return {"connected": True, "ssid": "test"}

    setup.adapter.connect.side_effect = connect
    with patch.object(MODULE, "READ_TIMEOUT", 0.05):
        assert request("/connect", dict(ssid="test", password="password", security="wpa-psk"))[:2] == (
            200, {"connected": True, "ssid": "test"})


def test_expiry_during_connect_denies_result_and_never_reopens_capability(setup):
    def connect(**kwargs):
        setup.clock[0] += MODULE.TTL_SECONDS
        return {"connected": True, "ssid": "test"}

    setup.adapter.connect.side_effect = connect
    assert request("/connect", dict(ssid="test", password="password", security="wpa-psk"))[0] == 401
    for path in ("/status", "/scan", "/finish", "/connect"):
        assert request(path)[0] == 401
    assert setup.adapter.connect.call_count == 1
    setup.adapter.status.assert_not_called()
    setup.adapter.scan.assert_not_called()


def test_token_only_hashed_and_not_logged(setup, capsys):
    assert TOKEN not in repr(vars(setup.broker))
    request()
    request(headers={"X-Lantern-Setup": "wrong"})
    assert capsys.readouterr() == ("", "")


def test_cli_rejects_missing_serve_and_extra_bind_parameters():
    for args in ([], ["--unit-id", "unit-b", "--interface", "wlan0"],
                 ["--serve", "--unit-id", "unit-b", "--interface", "wlan0", "--host", "0.0.0.0"]):
        with patch.object(MODULE, "make_adapter") as factory, pytest.raises(SystemExit):
            MODULE.main(args)
        factory.assert_not_called()


def test_cli_unit_mismatch_prevents_backend_and_token():
    with patch.object(MODULE.os, "geteuid", return_value=0), \
            patch.object(MODULE, "read_prepared_unit", return_value="unit-a"), \
            patch.object(MODULE, "make_adapter") as factory, \
            patch.object(MODULE, "terminal_token") as terminal, pytest.raises(SystemExit):
        MODULE.main(["--serve", "--unit-id", "unit-b", "--interface", "wlan0"])
    factory.assert_not_called()
    terminal.assert_not_called()


def test_real_runtime_adapter_import_and_constructor_do_not_run_commands():
    from sage_lantern.network_setup import NetworkSetup
    with patch("subprocess.run", side_effect=AssertionError("constructor must not run commands")):
        adapter = MODULE.make_adapter("unit-b", "wlan0")
    assert type(adapter) is NetworkSetup
    assert adapter.interface == "wlan0"


def test_cli_prints_once_only_to_terminal_and_cleans_up(capsys):
    server = Mock()
    server.handle_request.side_effect = RuntimeError("private failure")
    with patch.object(MODULE.os, "geteuid", return_value=0), \
            patch.object(MODULE, "read_prepared_unit", return_value="unit-b"), \
            patch.object(MODULE, "make_adapter", return_value=Mock()) as factory, \
            patch.object(MODULE.secrets, "token_urlsafe", return_value=TOKEN) as generate, \
            patch.object(MODULE, "terminal_token") as terminal, \
            patch.object(MODULE, "NetworkSetupServer", return_value=server) as constructor, \
            patch.object(MODULE.signal, "signal"), pytest.raises(SystemExit):
        MODULE.main(["--serve", "--unit-id", "unit-b", "--interface", "wlan0"])
    factory.assert_called_once_with("unit-b", "wlan0")
    generate.assert_called_once_with(32)
    terminal.assert_called_once_with(TOKEN)
    server.server_close.assert_called_once()
    with pytest.raises(MODULE.SetupError):
        constructor.call_args.args[0].require_token(TOKEN)
    assert TOKEN not in str(capsys.readouterr())


def test_terminal_requires_tty_and_never_uses_stdout():
    with patch.object(MODULE.os, "open", return_value=17) as opened, \
            patch.object(MODULE.os, "isatty", return_value=False), \
            patch.object(MODULE.os, "close") as closed, pytest.raises(ValueError):
        MODULE.terminal_token(TOKEN)
    assert opened.call_args.args[0] == "/dev/tty"
    closed.assert_called_once_with(17)


def test_metadata_safe_bounded_exact_fields(tmp_path):
    path = tmp_path / "unit.env"
    path.write_text("UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n")
    path.chmod(0o640)
    original = MODULE.os.fstat

    def trusted_fixture(descriptor):
        info = original(descriptor)
        return SimpleNamespace(st_mode=info.st_mode & ~0o022 if stat.S_ISDIR(info.st_mode) else info.st_mode,
                               st_uid=0, st_nlink=info.st_nlink, st_size=info.st_size)

    with patch.object(MODULE.os, "fstat", side_effect=trusted_fixture):
        assert MODULE.read_prepared_unit(path) == "unit-b"
        for raw in ("UNIT_ID=unit-b\nUNIT_ID=unit-a\n", "x" * 257, "UNIT_ID=unit-b\n", "bad"):
            path.write_text(raw)
            with pytest.raises(ValueError):
                MODULE.read_prepared_unit(path)
        path.chmod(0o666)
        with pytest.raises(ValueError):
            MODULE.read_prepared_unit(path)
        path.unlink()
        path.symlink_to(tmp_path / "missing")
        with pytest.raises(OSError):
            MODULE.read_prepared_unit(path)
