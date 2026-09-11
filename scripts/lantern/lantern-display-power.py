#!/usr/bin/env python3
"""Foreground X11 power helper: python3 lantern-display-power.py.

Run one instance as the kiosk X11 user, after launchers finish changing DPMS.
Requires /usr/bin/xset, plus either /usr/bin/xprintidle or native libX11.so.6
and libXss.so.1 with the X server MIT-SCREEN-SAVER extension. No Python packages
are required. When xprintidle is absent, a timeout-bounded same-script
--query-idle child reads idle milliseconds through ctypes. This child mode
only queries X11, never auth or DPMS, and has a two-second SIGALRM deadline
even when invoked directly. Missing capability exits nonzero without output.
DISPLAY must be a local X display selected by the trusted session launcher;
XAUTHORITY is inherited, never read by this helper. Missing or nonlocal DISPLAY
prohibits X11 operations; this helper does not discover or authorize sessions.
Only fixed GET /state and /power-policy on 127.0.0.1:3196 are used;
no tokens or private state. Validated display policy defaults at the service to
30 seconds; missing/invalid policy prohibits blanking and attempts wake.
Policy revision changes reset eligibility and wake an owned blanked display.
Deep-sleep preference is ignored: this helper never executes suspend.
The local daemon and X11 session user are trusted, not an authentication boundary.

Thirty seconds of sampled positive lock state AND X11 input idle permits one
blank attempt per observed input cycle. Missing/invalid idle observations prohibit blanking.
These thirty-second eligibility descriptions refer to the default; both
thresholds use the validated display_off_seconds setting, independently of the
auth daemon's 120-second idle lock. Preference reads are not DPMS observations.
This input grace protects PIN entry, without configuring global idle timers.
Unknown state or a polling gap resets the countdown and idle comparison.
A decrease between consecutive valid idle observations rearms blanking, with
a fresh thirty-second grace plus thirty seconds X11 idle before another blank.
No decrease means no repeated force-off, including after an uncertain command.
Positive unlock also rearms; daemon outages alone do not. Restarting the helper
starts a new countdown. Configuration failures permit at most three attempts
per input cycle, separated by thirty then sixty seconds; no force-off retries
without new input or unlock. Input between the final query and force-off remains
an unavoidable race without an X11 input grab (which this helper never uses).
X11 input wake and an intact kiosk lock screen must be verified on hardware.
Software DPMS off is not evidence of backlight state or measured power savings.
Polling cannot detect unlock/relock transitions entirely between observations.

DPMS automatic timers are zeroed BEFORE enabling DPMS. No suspend, screensaver
timer, unlock, activity, or input-grab operations are performed. Failures never
authorize blanking; uncertain force-off outcomes are treated as possibly off.
Unknown/unlocked state and clean shutdown attempt to restore an owned display.
Commands are bounded, but a failed X server cannot guarantee physical wake.
"""

import ctypes
import http.client
import json
import os
import re
import signal
import subprocess
import sys
import time


LOCK_SECONDS = 30.0
POLL_SECONDS = 1.0
MAX_SAMPLE_GAP = 3.0
HTTP_TIMEOUT = 0.5
MAX_RESPONSE_BYTES = 256
COMMAND_TIMEOUT = 2.0
CONFIG_RETRY_SECONDS = 30.0
MAX_CONFIG_ATTEMPTS = 3
COMMANDS = {
    "timers_off": ("/usr/bin/xset", "dpms", "0", "0", "0"),
    "enable": ("/usr/bin/xset", "+dpms"),
    "off": ("/usr/bin/xset", "dpms", "force", "off"),
    "on": ("/usr/bin/xset", "dpms", "force", "on"),
}


def unique_object(pairs):
    value = {}
    for name, member in pairs:
        if name in value:
            raise ValueError("invalid_state")
        value[name] = member
    return value


def _read_public(path):
    connection = http.client.HTTPConnection("127.0.0.1", 3196, timeout=HTTP_TIMEOUT)
    deadline = time.monotonic() + HTTP_TIMEOUT
    try:
        connection.connect()
        transport_socket = connection.sock
        connection.request("GET", path, headers={"Connection": "close"})
        transport_socket.settimeout(max(0.001, deadline - time.monotonic()))
        response = connection.getresponse()
        if response.status != 200 or response.getheader("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return None
        body = bytearray()
        while len(body) <= MAX_RESPONSE_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            transport_socket.settimeout(remaining)
            chunk = response.read1(MAX_RESPONSE_BYTES + 1 - len(body))
            if not chunk:
                break
            body.extend(chunk)
            if response.isclosed() is True:
                break
        if len(body) > MAX_RESPONSE_BYTES or time.monotonic() > deadline:
            return None
        value = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
        return value
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()
    return None


def read_locked():
    """Return True/False for an exact public state, None for every failure."""
    value = _read_public("/state")
    if type(value) is dict and set(value) == {"locked"} and type(value["locked"]) is bool:
        return value["locked"]
    return None


def validated_policy(value):
    if (type(value) is dict
            and set(value) == {"revision", "display_off_seconds", "deep_sleep_seconds", "deep_sleep_available"}
            and type(value["revision"]) is int and 0 <= value["revision"] <= (1 << 53) - 1
            and type(value["display_off_seconds"]) is int and value["display_off_seconds"] in (30, 60, 120, 300)
            and type(value["deep_sleep_seconds"]) is int and value["deep_sleep_seconds"] in (300, 600, 900, 1800)
            and value["deep_sleep_seconds"] > value["display_off_seconds"]
            and type(value["deep_sleep_available"]) is bool):
        return dict(value)
    return None


def read_power_policy():
    return validated_policy(_read_public("/power-policy"))


def local_display():
    value = os.environ.get("DISPLAY", "")
    return value if re.fullmatch(r":[0-9]{1,4}(?:\.[0-9]{1,2})?", value) else None


def run_xset(action):
    """Only fixed commands; neither shell commands nor user arguments."""
    command = COMMANDS[action]
    display_name = local_display()
    if display_name is None:
        return False
    environment = os.environ.copy()
    environment["DISPLAY"] = display_name
    try:
        result = subprocess.run(
            command, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT, check=False, shell=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def read_idle_seconds():
    """Query X11 input idle without changing timers; unavailable means unknown."""
    display_name = local_display()
    if display_name is None:
        return None
    environment = os.environ.copy()
    environment["DISPLAY"] = display_name
    try:
        options = dict(
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=COMMAND_TIMEOUT,
            check=False, shell=False,
        )
        try:
            result = subprocess.run(("/usr/bin/xprintidle",), **options)
        except FileNotFoundError:
            result = subprocess.run(
                (sys.executable, os.path.abspath(__file__), "--query-idle"), **options,
            )
        raw = result.stdout.strip()
        if result.returncode != 0 or not 1 <= len(raw) <= 10 or not raw.isdigit():
            return None
        milliseconds = int(raw)
        if milliseconds > 4294967295:
            return None
        return milliseconds / 1000.0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


class XScreenSaverInfo(ctypes.Structure):
    _fields_ = [
        ("window", ctypes.c_ulong),
        ("state", ctypes.c_int),
        ("kind", ctypes.c_int),
        ("til_or_since", ctypes.c_ulong),
        ("idle", ctypes.c_ulong),
        ("event_mask", ctypes.c_ulong),
    ]


def query_native_idle():
    """Child-only Xlib query; parent timeout also contains native hangs/crashes."""
    display_name = local_display()
    if display_name is None:
        return None
    display = None
    try:
        x11 = ctypes.CDLL("libX11.so.6")
        xss = ctypes.CDLL("libXss.so.1")
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.restype = ctypes.c_int
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        xss.XScreenSaverQueryExtension.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ]
        xss.XScreenSaverQueryExtension.restype = ctypes.c_int
        xss.XScreenSaverQueryInfo.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XScreenSaverInfo),
        ]
        xss.XScreenSaverQueryInfo.restype = ctypes.c_int
        display = x11.XOpenDisplay(display_name.encode("ascii"))
        if not display:
            return None
        event_base = ctypes.c_int()
        error_base = ctypes.c_int()
        if not xss.XScreenSaverQueryExtension(display, ctypes.byref(event_base), ctypes.byref(error_base)):
            return None
        info = XScreenSaverInfo()
        root = x11.XDefaultRootWindow(display)
        if not root or not xss.XScreenSaverQueryInfo(display, root, ctypes.byref(info)):
            return None
        return info.idle if info.idle <= 4294967295 else None
    except (OSError, AttributeError, ValueError):
        return None
    finally:
        if display:
            x11.XCloseDisplay(display)


class DisplayPower:
    """Dependencies are trusted local test injections, not external inputs."""

    def __init__(self, state=read_locked, command=run_xset, clock=time.monotonic,
                 idle=read_idle_seconds, policy=read_power_policy):
        self.state = state
        self.command = command
        self.clock = clock
        self.idle = idle
        self.policy = policy
        self.last_policy = None
        self.locked_since = None
        self.last_sample = None
        self.attempted = False
        self.maybe_off = False
        self.last_idle = None
        self.config_failures = 0
        self.retry_at = float("-inf")

    def wake(self):
        if self.maybe_off and self.command("on"):
            self.maybe_off = False

    def step(self):
        try:
            policy = validated_policy(self.policy())
        except Exception:
            policy = None
        if policy is None:
            self.locked_since = None
            self.last_idle = None
            self.last_sample = None
            self.wake()
            return
        if policy != self.last_policy:
            self.last_policy = policy
            self.locked_since = None
            self.last_idle = None
            self.last_sample = None
            self.attempted = False
            self.config_failures = 0
            self.retry_at = float("-inf")
            self.wake()
        delay = policy["display_off_seconds"]
        locked = self.state()
        now = self.clock()
        if self.last_sample is not None and not 0 <= now - self.last_sample <= MAX_SAMPLE_GAP:
            self.locked_since = None
            self.last_idle = None
        self.last_sample = now
        if locked is not True:
            self.locked_since = None
            self.last_idle = None
            if locked is False:
                self.attempted = False
                self.config_failures = 0
                self.retry_at = float("-inf")
            self.wake()
            return
        if self.locked_since is None:
            self.locked_since = now
        idle = self.observe_idle(now)
        if self.attempted or now - self.locked_since < delay or now < self.retry_at:
            return
        if idle is None or idle < delay:
            return
        if not self.command("timers_off") or not self.command("enable"):
            self.config_failures += 1
            self.retry_at = self.clock() + CONFIG_RETRY_SECONDS * self.config_failures
            self.attempted = self.config_failures >= MAX_CONFIG_ATTEMPTS
            return
        now = self.clock()
        idle = self.observe_idle(now)
        if idle is None or idle < delay or now - self.locked_since < delay:
            return
        if self.state() is not True:
            self.locked_since = None
            self.last_idle = None
            return
        try:
            final_policy = validated_policy(self.policy())
        except Exception:
            final_policy = None
        if final_policy != policy:
            self.locked_since = None
            self.last_idle = None
            self.wake()
            return
        self.attempted = True
        self.maybe_off = True
        if not self.command("off"):
            self.wake()

    def observe_idle(self, now):
        idle = self.idle()
        if type(idle) not in (int, float) or not 0 <= idle <= 4294967.295:
            self.last_idle = None
            return None
        if self.last_idle is not None and idle < self.last_idle:
            self.attempted = False
            self.config_failures = 0
            self.retry_at = float("-inf")
            self.locked_since = now
        self.last_idle = idle
        return idle


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--query-idle"]:
        previous_handler = signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(int(COMMAND_TIMEOUT))
        try:
            milliseconds = query_native_idle()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)
        if milliseconds is None:
            return 1
        print(milliseconds)
        return 0
    if arguments:
        return 2
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    controller = DisplayPower()
    try:
        while not stopped:
            controller.step()
            time.sleep(POLL_SECONDS)
    finally:
        controller.wake()


if __name__ == "__main__":
    sys.exit(main())
