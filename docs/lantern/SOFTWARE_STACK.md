# SAGE Lantern Bench Software Stack

**Status:** ARM64 beta bundle prepared; hardware execution pending  
**Target:** Radxa ROCK 5B+, Debian Bookworm, Linux aarch64, Python 3.11  
**SAGE release:** `v11.19.10` (`2b94b7054e19771f4fb49aff853939c81818b0c7`)

## Purpose

This stack is the smallest useful local-first path for Unit A:

`WAV input → whisper.cpp → personal SAGE recall → llama.cpp/Qwen → Piper WAV output`

The scaffold already preserves one correlation/session ID through the loop, records state transitions and timings as JSONL, and provides a durable SQLite public-proposal outbox with PUBLIC-only structural validation, stable envelope hashes, idempotent retry and fail-closed receipt transitions. PTT, live capture/playback, touchscreen UI, rotary encoder, signed-receipt verification, and MeshCore serial transport remain hardware-dependent or SAGE-contract adapters.

## Frozen trust boundary

- Each Lantern owns one sovereign personal SAGE node.
- Each Lantern has a separate ordinary public proposer identity and may query committed public Commons knowledge.
- Lantern proposals can queue offline and are not described as published until an authoritative committed receipt is observed.
- Public Commons validation belongs only to governed stable always-on servers.
- The base bundle contains no Root/Admin, personal-agent, public-proposer, radio, federation, TLS, recovery, or validator secrets.
- Cloning the sterile bundle is allowed. Cloning a provisioned SAGE home or any identity material is forbidden.
- A long-lived public SAGE Observer process is `unsupported-do-not-use`; the beta uses an authenticated Commons query client plus replaceable Lantern Cache and never repurposes state sync or assumes first-class multi-instance packaging.

## Pinned components

| Component | Pin | Purpose |
|---|---:|---|
| SAGE | 11.19.10 | Sovereign personal memory node and Python SDK |
| Lantern runtime | 0.1.0 | State machine and voice-loop orchestration |
| llama.cpp | b9637 | Local OpenAI-compatible inference server; built statically from locked source on the target |
| Qwen 3.5 4B | Unsloth Q4_K_M at `e87f176` | Local language model; llama.cpp-compatible GGUF, LFS SHA-256 `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4` |
| whisper.cpp | 1.8.6 | Local speech recognition |
| Whisper model | multilingual base | Bench STT baseline |
| Piper | 1.4.2 | Local speech synthesis; NumPy pinned to 1.26.4 for ONNX Runtime ABI compatibility |
| Piper voice | en_US-lessac-medium | Bench TTS baseline |

`software/stack.lock.json` records every cached file's byte count and SHA-256 digest. Run `python3 scripts/verify-lantern-bundle.py` before transfer or installation.

Use the pinned standalone Unsloth GGUF above. An Ollama content-cache blob is not a portable substitute: metadata-only rewrites do not restore tensors that are absent from that container.

## Prepared layout

- `software/bundle/linux-arm64/` — SAGE and llama.cpp ARM64 executables.
- `software/bundle/python-wheels/` — complete Python 3.11 ARM64 offline wheelhouse.
- `software/cache/models/` — pinned STT, LLM, and TTS models.
- `software/cache/sources/` — exact SAGE, llama.cpp, and whisper.cpp source archives.
- `software/runtime/` — testable Lantern integration package.
- `software/systemd/` — local LLM and personal SAGE service templates.
- `scripts/install-lantern-unit.sh` — target-device installer.
- `scripts/bootstrap-lantern-os.sh` — Bookworm/ARM64 prerequisite checker and installer.
- `scripts/provision-lantern-unit.sh` — fail-closed per-unit public identity inventory and gate.
- `scripts/smoke-test-lantern-unit.sh` — staged or on-device installation/voice smoke test.
- [`UNIT_A_BOOTSTRAP.md`](UNIT_A_BOOTSTRAP.md) — complete Unit A operator runbook.

## Target installation

The target must already have `python3`, `python3-venv`, `cmake`, a C++ compiler, `tar`, and ordinary audio/device utilities. From a complete copy of this repository on the ROCK:

```bash
python3 scripts/verify-lantern-bundle.py
sudo scripts/install-lantern-unit.sh
```

The installer copies verified artifacts, installs Python entirely from the local wheelhouse, compiles llama.cpp and whisper.cpp statically from their exact locked sources on the ROCK, and installs the three systemd units plus the smoke-test command. Native static builds avoid the newer-glibc requirement of the upstream Ubuntu llama.cpp binary and missing shared-library failures from whisper.cpp. It deliberately leaves services disabled and refuses to manufacture reusable identities in the image.

## Per-unit provisioning rule

Provision Unit A and Unit B independently on their final storage. Each needs unique personal chain/genesis, Comet node and personal-validator keys, CEREBRUM Root/recovery material, ordinary voice identity, public proposer identity, TLS/federation identity, and radio identity. Record public fingerprints in inventory; never copy the underlying secrets or a live `SAGE_HOME`.

Create `/var/lib/sage-lantern/PROVISIONED` only after the unit-specific identity inventory and recovery evidence have been checked. Then enable the services explicitly:

```bash
sudo systemctl enable --now sage-lantern-llm.service
sudo systemctl enable --now sage-lantern-personal-sage.service
```

## What can be tested before hardware

- Python unit tests for envelope bounds, deduplication, state transitions, and correlated voice-loop behavior.
- Durable public-proposal queue behavior, duplicate suppression, immutable ID/content binding, and receipt transition ordering.
- Wheel and model integrity.
- ARM64 binary format and exact SAGE version provenance.
- Configuration parsing and fail-closed health behavior.

## First hardware acceptance

1. Verify all locked artifacts before installation.
2. Record OS, kernel, CPU, memory, storage, and thermal baseline.
3. Provision a fresh Unit A identity; prove it differs from every development and Unit B identity.
4. Start only the personal SAGE and LLM services.
5. Run five fully offline voice loops with one ID across PTT, audio, STT, SAGE, LLM, TTS, and logs.
6. Test restart, unplug/replug, low-power warning, thermal load, and malformed-input recovery.
7. Only then add the public proposal client and bounded A↔B MeshCore test.

No public Commons validator key or voting power is installed on a Lantern at any stage.
