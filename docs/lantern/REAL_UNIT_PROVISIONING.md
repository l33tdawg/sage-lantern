# Provisioning the production units (Orange Pi 5 Plus 16 GB / 256 GB NVMe)

Test units A and B are ROCK 5B 8 GB; the production units are **Orange Pi 5 Plus
16 GB with 256 GB NVMe** (hardware on site, waiting on coolers). Everything the
test units taught us about the answer path, embeddings and packs transfers, but
the budgets change enough to revisit three decisions before provisioning.

## What 16 GB / 256 GB changes

| | test unit (8 GB) | production (16 GB) |
|---|---|---|
| primary chat model | 2B on NPU (4B only for photos) | **4B fits** — product plan already pairs the 16 GB tier with Qwen3.5-4B |
| verifier | 4B on CPU, ~7 s, RAM-tight | CPU 4B + a 1–2B judge both fit |
| embeddings | nomic 0.3 GB, comfortable | same, no pressure |
| packs | 377 GB free (plenty) | 256 GB — sizing is a delivery question, not capacity |
| swap | nearly full under load | ~16 GB headroom |

## The two model decisions worth making before provisioning day

1. **Export the 4B to RKLLM W8A8 and run it on the NPU.** RK3588 RKLLM
   documents up to ~7B W8A8 on 16 GB. A 4B W8A8 on the NPU would give 4B
   quality at 2B-class latency — which is the actual product answer to "the
   2B is terse and the 4B is slow", and it frees the CPU for the judge. Needs
   the export pipeline (training workspace) plus a device acceptance run on
   the same 60-case set and the factual set.
2. **Judge model.** With 16 GB, a 0.8–2B judge (Gemma-class or Qwen3.5-0.8B)
   fits. Decide only after measuring the single-token calibrated verifier on
   the 4B (free RAM, no new model); qualify any candidate on the factual set
   with a hunch-style protocol (accuracy at threshold, ECE, flip rate).

## Provisioning sequence (per unit)

1. **Base image + identity + commissioning** as today (device lock, vault,
   private listeners, fleet-admin UI).
2. **Models**: 2B (NPU) and 4B (CPU, photos) as today; add
   `nomic-embed-text-v1.5.Q8_0.gguf` (146 MB). If decision 1 lands, the 4B
   W8A8 `.rkllm` replaces the CPU chat role and the CPU build stays for vision.
3. **Answer runtime**: install the runtime wheel (complete answers + follow-up
   parsing + echo guard + verifier); set `llm.experimental_answer_suggestions`
   and `llm.verify_answers_background` in `lantern.toml`.
4. **Embeddings**: `scripts/lantern/install-embed-service.sh` (llama.cpp
   `--embedding` on :8083) and `install-nomic-shim.sh` (:8084), then
   `patch-node-embedding.sh` (`provider: ollama`, nomic, 768).
   *Node restart locks the vault — hold an owner session before the cold
   start, and archive a stale `/var/lib/sage-lantern-mesh-supervisor` if the
   runtime refuses with `storage_supervisor_unavailable`.*
5. **Packs**: `scripts/lantern/ingest-knowledge-pack.py <pack.jsonl>`; verify
   that the probe questions rank the exact fact #1.
6. **Audio commission** after every runtime byte change (the gate hashes all
   `sage_lantern/*.py` plus `lantern.toml` and the voice-loop script), then
   cold start and **first release pin** (`release.py` two-pass, verify all six
   baseline fields).
7. **Acceptance**: the 23-case factual set, one streamed question through the
   device (first text, total time, follow-up bubbles), and the cross-unit
   message exchange.

## Pack shape for torrent-style delivery (decide before the library grows)

Content-addressed chunks with a signed manifest: each chunk carries
`{id, title, text, tags, source, date, sha256}` and its embedding; the manifest
carries pack id, version, chunk list, total size and provenance. Devices seed
what they hold; updates ship as new versions plus per-chunk deltas, not full
re-downloads. Suggested sizes: practical/regional packs 10–100 MB, a dense
encyclopedia subset 1–10 GB — trivial for 256 GB, and the test units have room
to prototype the same shape.

## Also open

* **Public domain for packs** is a node-policy change (the vendored agent gets
  403 on `register_domain`/`grant_access` today); until then packs live in the
  home domain, tagged `pack:<name>-v<version>`.
* Cores/thermal budget for the embedding server plus judge on the same board.
