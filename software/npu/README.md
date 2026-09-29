# Lantern RKLLM payload

The engine and HTTP server are the exact sources measured on units A/B on
2026-09-19, promoted from `work/npu-llm-20260919/`. Their digests, the RKLLM 1.3.0
header/runtime digests and the stage-5 2B model digest are pinned in
`scripts/stage-appliance-release.py`. The artifact root supplies the library,
header and model under `software/bundle/linux-arm64/npu/`; all three must also
appear in the stack lock. Changing both an artifact and its lock cannot bypass
the measured-profile pins.

The fresh base installer calls `install-npu-payload.py` to build the engine
offline with the target's `/usr/bin/c++`, `-O2 -std=c++17`, the pinned header and
the pinned ARM64 runtime. It checks the resulting ELF architecture, installs
root-owned files under `/opt/sage-lantern/npu`, and records the compiler, command,
input hashes and output hash in `build-provenance.json`. It does not initialize
the NPU or start a service. An existing or interrupted destination is refused.
A unit that already carries the payload is served by successor mode
(`--predecessor-release-root <on-unit release tree>`): every file this install
would replace must match that release's recorded digest for the destination, this
payload's bytes, or the recorded on-unit build of the two engines; anything else
refuses unless the operator passes `--accept-replaced`, which is recorded in the
new provenance. The superseded `build-provenance.json` is kept beside the new one.

The payload also carries the NPU vision lane (decision D-016): `vlm_engine.cpp`
(persistent RKNN vision encoder + RKLLM engine) and `vlm_server.py` (OpenAI-shaped
photo front end) are built/installed the same way, with the runtime-matched
Qwen3.5-0.8B VL pair (`qwen3.5-0.8b_vision_rk3588.rknn` +
`qwen3.5-0.8b_w8a8_rk3588.rkllm`, rkllm-toolkit 1.3.0) and the librknnrt runtime
plus headers. The vision engine build additionally uses the target's OpenCV 4
development package (`libopencv-dev`, part of the base prerequisite set), because
it decodes and resizes photos. A model pair converted with a different toolkit
generation than the installed runtime is refused by the runtime by design — keep
the pins matched (see `work/unit-c-boot-20260928/VLM_NPU_LANE_20260929.md`).

The serving profile is RKLLM text on loopback port 8081 with NPU vision on 8082
(`sage-lantern-vlm.service`, which the text server already targets with
`--fallback-url`). The superseded CPU 4B vision fallback
(`sage-lantern-llm-cpu.service`, also drafted for 8082) remains in the payload for
bench comparison only and must never be enabled together with the vision unit on
the same unit; the text unit carries no dependency on either 8082 provider, so a
disabled retired unit cannot be pulled in by starting the chat unit. Nomic
embeddings on 8083 and the Ollama-compatible shim on 8084 are separate, locally
installed services. Base installation enables or starts none of these; fresh
commissioning owns activation, and the reviewed fresh-vision planner now plans the
NPU-vision profile (idle-suspend participation included).

The vision planner pins both model service definitions and proposes its CPU
override only for `sage-lantern-llm-cpu.service`. The older R7 activation helper is
a historical CPU-on-8081 procedure and is not the installer for this profile.
Native build success, file verification and unit definitions do not establish
board inference, latency, camera, audio, thermal, suspend or delivery acceptance.
