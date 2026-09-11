"""Explicit, local NetworkManager setup; constructor/import perform no I/O.

NetworkSetup(interface, runner=None) takes a trusted interface and optionally a
subprocess.run-compatible runner. The broker owns authorization, serialization,
rate limits and any deployment/Polkit permissions; this module never uses sudo.
status() is Wi-Fi association/activation, NOT Internet reachability. scan() is
the only rescan; connect() selects a visible cached AP with matching security.
Hidden, enterprise, WEP, OWE and WPA3-only networks are unsupported. Existing
active Wi-Fi is never intentionally replaced; Ethernet is never touched. A
separate process can still race the final state check: serialize network owners.

Each explicit connect creates a new UUID profile with autoconnect disabled.
Only after SSID and active UUID match does it enable that UUID's autoconnect,
then read back connection.uuid and connection.autoconnect. It never modifies
unrelated profiles. Failures after enabling trigger bounded disable/readback
of only our new UUID. If NetworkManager is unavailable even that rollback can
be uncertain: report failure, never success; operator reconciliation is needed.
Already-connected returns only status, not a persistence claim. No profiles are
automatically deleted. NetworkManager owns profile/secret persistence;
this helper writes no files and promises neither encryption nor memory erasure.

Passwords travel only in the stdin pipe of `nmcli --ask connection up uuid ...`.
Upstream src/nmcli/common.c nmc_readline_helper watches STDIN_FILENO, while
nmc_readline_echo suppresses secret history/echo. Both child output streams are
discarded during mutation, and all errors are fixed strings without chaining.
See https://github.com/NetworkManager/NetworkManager/blob/main/src/nmcli/common.c
and https://networkmanager.dev/docs/api/latest/nmcli.html . Native distro/Polkit
acceptance still requires an authorized test; no live behavior is proven here.
"""

import re
import os
import selectors
import subprocess
import time
import unicodedata
import uuid


QUERY_TIMEOUT = 8
CONTROL_TIMEOUT = 8
CONNECT_TIMEOUT = 45
CONNECT_BUDGET_SECONDS = 133
MAX_OUTPUT = 65536
MAX_APS = 256
MAX_NETWORKS = 128


def _bounded_run(command, **options):
    """Run-compatible default; cap query bytes while reading, not afterwards.

Mutation streams are DEVNULL. Only empty-input queries enter the bounded pipe
reader. The connect command budgets total 117s success / 133s with rollback,
plus process creation/reap overhead; a broker/client budget of 180s is suitable.
Injected runners are trusted and must honor timeout and output limits too.
"""
    if options["stdout"] != subprocess.PIPE:
        return subprocess.run(command, **options)
    timeout = options.pop("timeout")
    options.pop("check")
    if options.pop("input") != b"":
        _fail()
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, **options)
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    _fail()
                chunk = os.read(process.stdout.fileno(), min(4096, MAX_OUTPUT + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > MAX_OUTPUT:
                    _fail()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _fail()
        return subprocess.CompletedProcess(command, process.wait(timeout=remaining), bytes(output))
    finally:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        process.stdout.close()


class NetworkSetupError(RuntimeError):
    """Only fixed public error codes; never include subprocess diagnostics."""


def _fail():
    raise NetworkSetupError("network_setup_failed") from None


def _ssid_valid(ssid):
    try:
        return (type(ssid) is str and 1 <= len(ssid.encode("utf-8")) <= 32
                and not any(unicodedata.category(char).startswith("C") for char in ssid))
    except UnicodeError:
        return False


def _fields(line):
    fields = []
    current = []
    escaped = False
    for char in line:
        if escaped:
            if char not in (":", "\\"):
                _fail()
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        _fail()
    return [*fields, "".join(current)]


def _security(value):
    if value in ("", "--"):
        return "open"
    tokens = set(value.split())
    if tokens and tokens <= {"WPA1", "WPA2"}:
        return "wpa-psk"
    return "unsupported"


class NetworkSetup:
    def __init__(self, interface, runner=None):
        if type(interface) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}", interface):
            raise NetworkSetupError("invalid_network_request")
        self.interface = interface
        self._runner = _bounded_run if runner is None else runner

    def _run(self, arguments, *, password=None, mutation=False, activation=False):
        timeout = CONNECT_TIMEOUT if activation else CONTROL_TIMEOUT if mutation else QUERY_TIMEOUT
        command = ["/usr/bin/nmcli", "--colors", "no", "--terse", "--escape", "yes",
                   "--wait", str(timeout - 2), *arguments]
        try:
            result = self._runner(
                command, input=b"" if password is None else (password + "\n").encode("ascii"),
                stdout=subprocess.DEVNULL if mutation else subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=timeout, check=False, shell=False,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
                start_new_session=True,
            )
            if result.returncode != 0:
                _fail()
            if mutation:
                return ""
            if type(result.stdout) is not bytes or len(result.stdout) > MAX_OUTPUT:
                _fail()
            return result.stdout.decode("utf-8")
        except Exception:
            _fail()

    def _state(self):
        raw = self._run(["--get-values", "GENERAL.TYPE,GENERAL.STATE", "device", "show", self.interface])
        lines = raw.splitlines()
        if len(lines) != 2 or lines[0] != "wifi":
            _fail()
        match = re.fullmatch(r"([0-9]{2,3}) \([^\r\n]+\)", lines[1])
        if not match or int(match[1]) not in {10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120}:
            _fail()
        return int(match[1])

    def _aps(self, rescan=False):
        raw = self._run(["--fields", "IN-USE,BSSID,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                         "ifname", self.interface, "--rescan", "yes" if rescan else "no"])
        lines = raw.splitlines()
        if len(lines) > MAX_APS:
            _fail()
        result = []
        for line in lines:
            fields = _fields(line)
            if len(fields) != 5:
                _fail()
            active, bssid, ssid, signal, security = fields
            if (active not in ("", " ", "*") or not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", bssid)
                    or not re.fullmatch(r"[0-9]{1,3}", signal) or int(signal) > 100):
                _fail()
            if not ssid and active != "*":
                continue
            if not _ssid_valid(ssid):
                _fail()
            result.append({"ssid": ssid, "signal": int(signal), "security": _security(security),
                           "bssid": bssid, "active": active == "*"})
        return result

    def status(self):
        if self._state() != 100:
            return {"connected": False, "ssid": None}
        active = [entry for entry in self._aps() if entry["active"]]
        if len(active) != 1:
            _fail()
        return {"connected": True, "ssid": active[0]["ssid"]}

    def _verify_autoconnect(self, identifier, expected):
        value = self._run(["--get-values", "connection.uuid,connection.autoconnect",
                           "connection", "show", "uuid", identifier])
        if value.splitlines() != [identifier, expected]:
            _fail()

    def _enable_persistence(self, identifier):
        active = self._run(["--get-values", "GENERAL.CON-UUID", "device", "show", self.interface])
        if active.splitlines() != [identifier]:
            _fail()
        try:
            self._run(["connection", "modify", "uuid", identifier, "connection.autoconnect", "yes"], mutation=True)
            self._verify_autoconnect(identifier, "yes")
        except NetworkSetupError:
            try:
                self._run(["connection", "modify", "uuid", identifier, "connection.autoconnect", "no"], mutation=True)
                self._verify_autoconnect(identifier, "no")
            except NetworkSetupError:
                pass
            _fail()

    def scan(self):
        self._state()
        grouped = {}
        for entry in self._aps(rescan=True):
            previous = grouped.get(entry["ssid"])
            if previous is None:
                grouped[entry["ssid"]] = {key: entry[key] for key in ("ssid", "signal", "security")}
            else:
                previous["signal"] = max(previous["signal"], entry["signal"])
                if previous["security"] != entry["security"]:
                    previous["security"] = "unsupported"
        return sorted(grouped.values(), key=lambda entry: (-entry["signal"], entry["ssid"]))[:MAX_NETWORKS]

    def connect(self, ssid, password, security):
        if (not _ssid_valid(ssid) or ssid.startswith("-") or type(security) is not str
                or security not in {"open", "wpa-psk"} or type(password) is not str):
            raise NetworkSetupError("invalid_network_request")
        if security == "open":
            valid_password = password == ""
        else:
            valid_password = (8 <= len(password) <= 63 and all(32 <= ord(char) <= 126 for char in password)) or bool(
                re.fullmatch(r"[0-9a-fA-F]{64}", password))
        if not valid_password:
            raise NetworkSetupError("invalid_network_request")
        state = self._state()
        if state == 100:
            current = self.status()
            if current["ssid"] == ssid:
                return current
            _fail()
        if state != 30:
            _fail()
        candidates = [entry for entry in self._aps() if entry["ssid"] == ssid]
        if not candidates or any(entry["security"] != security for entry in candidates):
            _fail()
        selected = max(candidates, key=lambda entry: entry["signal"])
        identifier = str(uuid.uuid4())
        arguments = ["connection", "add", "type", "wifi", "ifname", self.interface,
                     "con-name", "lantern-setup-" + identifier, "connection.uuid", identifier,
                     "connection.autoconnect", "no", "ssid", ssid]
        if security == "wpa-psk":
            arguments += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk-flags", "0"]
        self._run(arguments, mutation=True)
        if self._state() != 30:
            _fail()
        self._run([*(["--ask"] if security == "wpa-psk" else []), "connection", "up", "uuid", identifier,
                   "ifname", self.interface, "ap", selected["bssid"]],
                  password=password if security == "wpa-psk" else None, mutation=True, activation=True)
        current = self.status()
        if current != {"connected": True, "ssid": ssid}:
            _fail()
        self._enable_persistence(identifier)
        return current
