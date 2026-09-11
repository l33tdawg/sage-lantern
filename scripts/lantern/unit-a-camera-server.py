#!/usr/bin/env python3
"""Loopback-only, on-demand preview; no vision inference or retained images.

The voice guard is best-effort: unreadable/malformed voice state skips the guard.
The UI must coordinate voice/camera use; do not grant identity/key access to this
service to read voice state. GET never opens or starts the camera.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import selectors
import struct
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


CAMERA_PATH = Path("/dev/v4l/by-id/usb-046d_0825_5E4BEE10-video-index0")
VOICE_STATE = Path("/run/sage-lantern-voice/state.json")
ORIGINS = {"http://127.0.0.1:3100", "http://localhost:3100"}
HOSTS = {"127.0.0.1:3198", "localhost:3198"}
MAX_JPEG_BYTES = 2 * 1024 * 1024
PREVIEW_SECONDS = 60


class PreviewStream:
    """Length-framed JPEG source with a strict 60-second session lifetime."""

    def __init__(self, process, selector):
        self.process = process
        self.selector = selector
        self.buffer = bytearray()
        self.expires = time.monotonic() + PREVIEW_SECONDS
        self.first = True

    def frame(self) -> bytes:
        deadline = min(self.expires, time.monotonic() + (10 if self.first else 5))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CaptureError(504, "preview_timeout")
            if len(self.buffer) >= 2 and self.buffer[:2] != b"\xff\xd8":
                raise CaptureError(502, "invalid_jpeg")
            end = self.buffer.find(b"\xff\xd9", 2)
            if end >= 0:
                size = end + 2
                if size > MAX_JPEG_BYTES:
                    raise CaptureError(502, "invalid_jpeg")
                frame = bytes(self.buffer[:size])
                del self.buffer[:size]
                self.first = False
                return frame
            if len(self.buffer) >= MAX_JPEG_BYTES:
                raise CaptureError(502, "invalid_jpeg")
            if not self.selector.select(remaining):
                raise CaptureError(504, "preview_timeout")
            chunk = os.read(self.process.stdout.fileno(), min(65536, MAX_JPEG_BYTES - len(self.buffer)))
            if not chunk:
                raise CaptureError(502, "preview_ended")
            self.buffer.extend(chunk)


class CaptureError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code


class Camera:
    def __init__(self, device: Path, voice_state: Path = VOICE_STATE):
        self.device = device
        self.voice_state = voice_state
        self.lock = threading.Lock()

    def voice_active(self) -> bool:
        try:
            with self.voice_state.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                return False
            state = json.loads(raw)
        except (OSError, ValueError):
            return False
        return isinstance(state, dict) and state.get("phase") in ("listening", "processing")

    def capture(self) -> bytes:
        if not self.lock.acquire(blocking=False):
            raise CaptureError(409, "camera_busy")
        try:
            if self.voice_active():
                raise CaptureError(409, "voice_active")
            if not self.device.exists():
                raise CaptureError(503, "camera_unavailable")
            with tempfile.TemporaryDirectory(prefix="sage-lantern-camera-") as directory:
                output = Path(directory) / "frame.jpg"
                output.touch(mode=0o600)
                try:
                    subprocess.run(
                        [
                            "/usr/bin/v4l2-ctl",
                            f"--device={self.device}",
                            "--set-fmt-video=width=1280,height=720,pixelformat=MJPG",
                            "--stream-mmap=3",
                            "--stream-skip=15",
                            "--stream-count=1",
                            f"--stream-to={output}",
                        ],
                        check=True, timeout=15,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except subprocess.TimeoutExpired:
                    raise CaptureError(504, "capture_timeout") from None
                except (OSError, subprocess.CalledProcessError):
                    raise CaptureError(503, "capture_failed") from None
                with output.open("rb") as stream:
                    data = stream.read(MAX_JPEG_BYTES + 1)
                if not 4 <= len(data) <= MAX_JPEG_BYTES or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
                    raise CaptureError(502, "invalid_jpeg")
                return data
        except OSError:
            raise CaptureError(503, "capture_failed") from None
        finally:
            self.lock.release()

    @contextmanager
    def preview(self):
        if not self.lock.acquire(blocking=False):
            raise CaptureError(409, "camera_busy")
        process = None
        try:
            if self.voice_active():
                raise CaptureError(409, "voice_active")
            if not self.device.exists():
                raise CaptureError(503, "camera_unavailable")
            try:
                subprocess.run(
                    [
                        "/usr/bin/v4l2-ctl", f"--device={self.device}",
                        "--set-fmt-video=width=1280,height=720,pixelformat=MJPG",
                        "--set-parm=15",
                    ],
                    check=True, timeout=5,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                raise CaptureError(504, "preview_config_timeout") from None
            except subprocess.CalledProcessError:
                raise CaptureError(503, "preview_config_failed") from None
            process = subprocess.Popen(
                [
                    "/usr/bin/v4l2-ctl", f"--device={self.device}",
                    "--stream-mmap=3", "--stream-count=0", "--stream-to=-",
                ],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
            )
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                yield PreviewStream(process, selector)
        except OSError:
            raise CaptureError(503, "preview_failed") from None
        finally:
            try:
                if process is not None:
                    try:
                        if process.poll() is None:
                            process.terminate()
                        process.wait(timeout=1)
                    except (OSError, subprocess.TimeoutExpired):
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=1)
                    finally:
                        if process.stdout is not None:
                            process.stdout.close()
            finally:
                self.lock.release()


class CameraServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, camera: Camera):
        if address != ("127.0.0.1", 3198):
            raise ValueError("camera server must bind to 127.0.0.1:3198")
        self.camera = camera
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, CameraHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(20)
        return connection, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass


def _session_authorized(token: str | None) -> bool:
    try:
        from sage_lantern.device_access import authorized
        return authorized(token) is True
    except Exception:
        return False


class CameraHandler(BaseHTTPRequestHandler):
    server_version = "SAGELanternCamera/1"

    def single_header(self, name: str) -> str | None:
        values = self.headers.get_all(name, [])
        return values[0] if len(values) == 1 else None

    def respond(self, status: int, payload: bytes | dict, *, preflight: bool = False):
        jpeg = isinstance(payload, bytes)
        if jpeg and not self.session_authorized():
            self.respond(403, {"error": "device_locked"})
            return
        body = payload if jpeg else json.dumps(payload).encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "image/jpeg" if jpeg else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.send_header("Vary", "Origin")
        origin = self.single_header("Origin")
        if origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            if preflight:
                self.send_header("Access-Control-Allow-Methods", "POST")
                requested = self.single_header("Access-Control-Request-Headers") or ""
                self.send_header("Access-Control-Allow-Headers", "X-Lantern-Camera, X-Lantern-Session" if "x-lantern-session" in requested.lower() else "X-Lantern-Camera")
        self.end_headers()
        if jpeg and not self.session_authorized():
            return
        self.wfile.write(body)

    def session_authorized(self):
        values = self.headers.get_all("X-Lantern-Session", [])
        return len(values) <= 1 and _session_authorized(values[0] if values else None)

    def validate(self, *, capture: bool = False, preflight: bool = False) -> bool:
        if len(self.headers.get_all("X-Lantern-Session", [])) > 1:
            self.respond(400, {"error": "duplicate_session_header"})
            return False
        if self.single_header("Host") not in HOSTS:
            self.respond(403, {"error": "invalid_host"})
            return False
        if self.headers.get_all("Transfer-Encoding") or (
            self.headers.get_all("Content-Length") and self.single_header("Content-Length") != "0"
        ):
            self.respond(400, {"error": "body_not_allowed"})
            return False
        if capture or preflight:
            if self.single_header("Origin") not in ORIGINS:
                self.respond(403, {"error": "invalid_origin"})
                return False
        action = "preview" if self.path == "/preview" else "capture"
        if capture and (self.single_header("X-Lantern-Camera") != action or self.single_header("Content-Length") != "0"):
            self.respond(400, {"error": "capture_headers_required"})
            return False
        if preflight and (
            self.single_header("Access-Control-Request-Method") != "POST"
            or sorted(part.strip().lower() for part in (self.single_header("Access-Control-Request-Headers") or "").split(",")) not in (["x-lantern-camera"], ["x-lantern-camera", "x-lantern-session"])
        ):
            self.respond(403, {"error": "invalid_preflight"})
            return False
        return True

    def do_GET(self):
        if not self.validate():
            return
        if self.path != "/health":
            self.respond(404, {"error": "not_found"})
            return
        self.respond(200, {"camera_present": self.server.camera.device.exists(), "vision_ready": False})

    def do_POST(self):
        if not self.validate(capture=True):
            return
        if self.path == "/preview":
            self.preview_response()
            return
        if self.path != "/capture":
            self.respond(404, {"error": "not_found"})
            return
        if not self.session_authorized():
            self.respond(403, {"error": "device_locked"})
            return
        try:
            frame = self.server.camera.capture()
        except CaptureError as error:
            self.respond(error.status, {"error": error.code})
            return
        self.respond(200, frame)

    def preview_response(self):
        started = False
        try:
            if not self.session_authorized():
                raise CaptureError(403, "device_locked")
            with self.server.camera.preview() as stream:
                frame = stream.frame()
                if not self.session_authorized():
                    raise CaptureError(403, "device_locked")
                self.connection.settimeout(1)
                self.close_connection = True
                started = True
                self.send_response(200)
                self.send_header("Content-Type", "application/x-lantern-jpeg-stream")
                self.send_header("Connection", "close")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Origin", self.single_header("Origin"))
                self.end_headers()
                while True:
                    if not self.session_authorized():
                        raise CaptureError(403, "device_locked")
                    if time.monotonic() >= stream.expires:
                        raise CaptureError(504, "preview_timeout")
                    self.wfile.write(struct.pack("!I", len(frame)) + frame)
                    self.wfile.flush()
                    frame = stream.frame()
        except CaptureError as error:
            if not started:
                self.respond(error.status, {"error": error.code})
        except (OSError, subprocess.TimeoutExpired):
            if not started:
                self.respond(503, {"error": "preview_failed"})

    def do_OPTIONS(self):
        if not self.validate(preflight=True):
            return
        if self.path not in {"/capture", "/preview"}:
            self.respond(404, {"error": "not_found"})
            return
        self.respond(200, {}, preflight=True)

    def log_message(self, format, *args):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=Path, default=CAMERA_PATH)
    args = parser.parse_args()
    if not args.device.is_absolute() or ".." in args.device.parts or not args.device.is_relative_to("/dev"):
        parser.error("camera device must be an absolute path under /dev")
    CameraServer(("127.0.0.1", 3198), Camera(args.device)).serve_forever()


if __name__ == "__main__":
    main()
