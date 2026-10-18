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

The base serving profile is RKLLM text on loopback port 8081 with NPU vision on
8082 (`sage-lantern-vlm.service`, which the text server already targets with
`--fallback-url`). Unit C's production chat now uses the 3B token lane described
below; the 2B service remains its rollback. The superseded CPU 4B vision fallback
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

## 3B token lane on Unit C (18 October 2026)

Unit C's production chat port 8081 runs the V79.6 3B model through the RKLLM
token engine, with `response_guards.py` enabled. The full deployment record is
[`docs/lantern/3B_MODEL_PRODUCTION.md`](../../docs/lantern/3B_MODEL_PRODUCTION.md).

| Component | Durable path |
|---|---|
| Model | `/opt/sage-lantern/npu/models/v79_6-fused-w8a8-opt1-ctx16384-rk3588.rkllm` (`41172d78…`) |
| Token engine | `/opt/sage-lantern/npu/token-engine/bin/rkllm_engine_token` (`c4cf4fda…`, 16k/8192 limits) |
| Runtime | `/opt/sage-lantern/npu/token-engine/lib/librkllmrt.so` (`f25e9b09…`) |
| Tokenizer | `/opt/sage-lantern/npu/token-engine/assets/tokenizer.json` (`c0382117…`), `tokenizers` 0.23.3 |
| Server + guards | `/opt/sage-lantern/npu/token-engine/libexec/rkllm_server.py` (`b568af7d…`), `response_guards.py` (`07d64238…`) |

The production prompt-string engine cannot initialize the 3B model: it fails the
3,085,697,024-byte NPU IOVA mapping. The token engine loads the same artifact;
the server sends `prompt_utf8_hex` plus `input_ids` encoded by the model
tokenizer. The 2B service remains the rollback. The 16k 3B profile was verified
with the NPU vision lane active; the 2B chat lane is not resident at the same
time.

Measured on Unit C, W8A8, 48 requests at the historical 2048 context:
625.1 ms median TTFT (stock 669.7),
7.82 tok/s decode (8.03), 3,393 MB SDK memory (3,250), 28/28 wrapped action
prompts. The guarded checks pass E13_A/E13_B and S03_B; E12_A/B pass. The model
is compiled at 16k context by the RKLLM toolkit cap; the training host model is
32k. The live 8081 service is configured `--context 16384
--max-new-tokens 8192`; explicit long-form requests are buffered and paragraph
counts are normalized. The model may stop well before the output cap.

The token engine source is tracked as
`software/npu/rkllm_engine_token.cpp` with `token_protocol.hpp` and
`build_binding.hpp`. Build it against the pinned RKLLM 1.3.1 header/runtime with
the target's `/usr/bin/c++`:

```sh
/usr/bin/c++ -O2 -std=c++17 -I <pinned-header-dir> \
  software/npu/rkllm_engine_token.cpp \
  -L /opt/sage-lantern/npu/token-engine/lib -lrkllmrt \
  -Wl,-rpath,/opt/sage-lantern/npu/token-engine/lib \
  -o rkllm_engine_token
```

The deployed budget is 16,384 context / 8,192 default output. The source
binding is `adad98ce…`; the built Unit C binary is `c4cf4fda…`.

The guards short-circuit deterministic interval answers before generation,
correlate write receipts by tool identity and the preceding action, refuse an
ungranted private owner's memory, and repair receipt-bound status prose. They
buffer only the first assistant turn after a recall receipt; later chat turns
remain streamed. They do not authorize tool calls; save actions still require
the SAGE/OpenClaw dispatcher. Do not disable the guards without re-running the
guarded checks: the raw model retains the E13 interval drift and the S03_B
absence clause.

The systemd drop-in
`/etc/systemd/system/sage-lantern-llm.service.d/50-v79.6-token.conf`
(`298b5b50…`) overrides only `ExecStart` and `LD_LIBRARY_PATH`; the 2B unit
backup is `sage-lantern-llm.service.pre-v79.6-token.bak` (`1c5888ae…`).
