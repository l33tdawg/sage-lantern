#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$(pwd)/software/runtime/src:$(pwd)/test-support/sage_agent_sdk-11.19.10+lantern.20260906-py3-none-any.whl"
python3 -m pytest -q \
  scripts/tests/test_prepare_runtime_maintenance.py \
  scripts/tests/test_build_runtime_maintenance_env.py \
  scripts/tests/test_complete_runtime_maintenance_audio.py \
  scripts/tests/test_install_startup_coordinator.py \
  scripts/tests/test_startup_audio_adapter.py \
  scripts/tests/test_normal_shutdown_audio_startup.py \
  scripts/tests/test_startup_sleep_batch.py \
  scripts/tests/test_collect_cross_boot_audio_pins.py \
  scripts/tests/test_guarded_benchmark_report.py \
  scripts/tests/test_observe_voice_benchmark.py \
  scripts/tests/test_activate_fresh_vision.py \
  scripts/tests/test_maintenance_initial_exec.py \
  scripts/tests/test_maintenance_inactive_display.py \
  scripts/tests/test_collect_fresh_vision_inputs.py \
  scripts/tests/test_camera_vision_defaults.py \
  scripts/tests/test_install_camera_local.py
