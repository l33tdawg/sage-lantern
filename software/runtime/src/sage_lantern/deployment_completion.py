"""Explicit warm completion using the trusted installed suspend adapter.

This is not cold bootstrap or recovery. It never submits sleep, starts voice
directly, unlocks the vault, enables services or rolls configuration back.
The idle controller stays stopped for separate reviewed re-arming.
"""

import json
import os
import time

from .deployment_drain import require, resume_root_broker, verify_root_broker_active
from .mesh_voice_ipc import managed
from .suspend_io import publish, read_json
from .suspend_transition import Phase, Snapshot


class DeploymentCompletion:
    def __init__(self, runner):
        self.runner = runner

    def finish(self, owner):
        runner = self.runner
        require(managed())
        owner.assert_current(candidate_sha256=owner._candidate, rollback_sha256=owner._rollback)
        require(owner.appliance.aborted() is True)
        path = owner.journal.directory / ("deployment-completion-" + owner._snapshot.transition_id + ".json")
        record = dict(schema="sage.lantern.deployment-completion.v1", binding=owner._binding, steps=[])
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

        def checkpoint(name, phase):
            owner._identity()
            require(managed() and owner.appliance.aborted() is True)
            require(read_json(path, owner_uid=owner._uid) == record)
            record["steps"].append(dict(step=name, phase=phase))
            publish(path, record, owner_uid=owner._uid)

        def step(name, action):
            checkpoint(name, "intent")
            require(action() is True)
            checkpoint(name, "done")

        def model():
            runner.command(["/usr/bin/systemctl", "start", *runner.resident_models()], timeout=30)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    if runner.model_baseline_ready() is True and runner.models_idle() is True:
                        return True
                except Exception:
                    pass
                time.sleep(0.2)
            return False

        def auth():
            runner.command(["/usr/bin/systemctl", "start", runner.AUTH], timeout=10)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    if runner.locked() is True:
                        return True
                except Exception:
                    pass
                time.sleep(0.2)
            return False

        active = None
        def broker():
            nonlocal active
            require(runner.model_baseline_ready() is True and runner.locked() is True)
            active = resume_root_broker(owner._proof)
            return True

        def surfaces():
            require(runner.locked() is True and verify_root_broker_active() == active)
            runner.command(["/usr/bin/systemctl", "start", runner.CAMERA, runner.UI, runner.DISPLAY], timeout=30)
            for service in (runner.CAMERA, runner.UI, runner.DISPLAY):
                runner.service_identity(service)
            require(owner.appliance.kiosk.resume() is True)
            runner.display_power("on")
            return True

        try:
            step("release_sleep_fence", owner.appliance.release_fence)
            step("model_baseline", model)
            step("auth_locked", auth)
            step("broker_resume", broker)
            step("restore_surfaces", surfaces)
            require(verify_root_broker_active() == active and runner.model_baseline_ready() is True)
            terminal = Snapshot(owner._snapshot.transition_id, phase=Phase.ABORTED_VERIFIED,
                revision=owner._snapshot.revision + 1, completed=True)
            owner.journal.checkpoint(terminal)
            owner.journal.clear(terminal, verified=True)
            checkpoint("complete", "done")
            require(read_json(owner._marker, owner_uid=owner._uid) == owner._binding)
            owner._marker.unlink()
            directory = os.open(owner._marker.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return dict(completed=True, broker=active, idle_rearmed=False,
                capture_verified=False, inference_verified=False)
        except BaseException:
            if not os.path.lexists(owner._marker):
                publish(owner._marker, owner._binding, owner_uid=owner._uid)
            owner.journal.retain_fence(owner._snapshot.transition_id)
            require(owner.appliance.retain_fence() is True)
            raise
