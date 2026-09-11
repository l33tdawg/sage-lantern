#!/usr/bin/env bash
set -euo pipefail

output_dir="${1:-/home/radxa/sage-commons/evidence/unit-a-headless-ai-smoke}"
llama_bin=/opt/sage-lantern/llama.cpp/llama-server
llm_model=/opt/sage-lantern/models/qwen3.5-4b-q4_k_m.gguf
python_bin=/opt/sage-lantern/venv/bin/python

[[ ! -e "${output_dir}" ]] || {
  printf 'Refusing existing evidence directory: %s\n' "${output_dir}" >&2
  exit 1
}
[[ ! -e /var/lib/sage-lantern/PROVISIONED ]] || {
  printf 'This pre-provision smoke test requires PROVISIONED to remain absent.\n' >&2
  exit 1
}
for unit in sage-lantern-llm.service sage-lantern-personal-sage.service sage-lantern-runtime.service; do
  if systemctl is-active --quiet "${unit}"; then
    printf 'Refusing while systemd service is active: %s\n' "${unit}" >&2
    exit 1
  fi
done
if ss -ltn | grep -q ':8081 '; then
  printf 'Refusing because TCP port 8081 is already in use.\n' >&2
  exit 1
fi

mkdir -p "${output_dir}"
server_pid=''
cleanup() {
  if [[ -n "${server_pid}" ]] && kill -0 "${server_pid}" 2>/dev/null; then
    kill -TERM "${server_pid}"
    wait "${server_pid}" || true
  fi
}
trap cleanup EXIT

"${llama_bin}" \
  --model "${llm_model}" --host 127.0.0.1 --port 8081 \
  --ctx-size 2048 --threads 6 --parallel 1 --no-ui \
  >"${output_dir}/llama-server.log" 2>&1 &
server_pid=$!

ready=0
for _ in {1..180}; do
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    printf 'llama-server exited during startup.\n' >&2
    tail -80 "${output_dir}/llama-server.log" >&2
    exit 1
  fi
  if curl --fail --silent http://127.0.0.1:8081/health >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
[[ ${ready} -eq 1 ]] || {
  printf 'llama-server did not become healthy within 180 seconds.\n' >&2
  exit 1
}

"${python_bin}" - "${output_dir}" <<'PY'
from __future__ import annotations

import json
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

from sage_lantern.adapters import LlamaCppHTTP, PiperTTS, WhisperCppSTT


out = Path(sys.argv[1])
phrase = "The lantern remembers the river crossing."
wav = out / "piper-known-phrase.wav"

timings: dict[str, float] = {}
started = time.perf_counter()
PiperTTS(
    Path("/opt/sage-lantern/venv/bin/python"),
    Path("/opt/sage-lantern/models/en_US-lessac-medium.onnx"),
    Path("/opt/sage-lantern/models/en_US-lessac-medium.onnx.json"),
).synthesize(phrase, wav)
timings["piper_ms"] = round((time.perf_counter() - started) * 1000, 2)

started = time.perf_counter()
transcript = WhisperCppSTT(
    Path("/opt/sage-lantern/bin/whisper-cli"),
    Path("/opt/sage-lantern/models/ggml-base.bin"),
    6,
).transcribe(wav)
timings["whisper_ms"] = round((time.perf_counter() - started) * 1000, 2)

similarity = SequenceMatcher(None, phrase.lower(), transcript.lower()).ratio()
if not transcript or similarity < 0.55:
    raise RuntimeError(f"transcript mismatch: similarity={similarity:.3f}, transcript={transcript!r}")

started = time.perf_counter()
response = LlamaCppHTTP(
    "http://127.0.0.1:8081", "qwen3.5-4b-q4_k_m", timeout_seconds=180.0
).respond(
    "In one short sentence, confirm that this is a local headless smoke test.",
    ["Unit A is running offline local inference for this test."],
)
timings["llama_ms"] = round((time.perf_counter() - started) * 1000, 2)
if not response.strip():
    raise RuntimeError("llama.cpp returned an empty response")

result = {
    "schema": "sage.lantern.headless-ai-smoke.v1",
    "known_phrase": phrase,
    "transcript": transcript,
    "transcript_similarity": round(similarity, 4),
    "llm_response": response,
    "timings_ms": timings,
    "wav_bytes": wav.stat().st_size,
}
(out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(result, indent=2, sort_keys=True))
PY

cleanup
server_pid=''

{
  printf 'PROVISIONED=absent\n'
  for unit in sage-lantern-llm.service sage-lantern-personal-sage.service sage-lantern-runtime.service; do
    printf '%s enabled=%s active=%s\n' "${unit}" \
      "$(systemctl is-enabled "${unit}" 2>/dev/null || true)" \
      "$(systemctl is-active "${unit}" 2>/dev/null || true)"
  done
} >"${output_dir}/gate-and-services.txt"

(
  cd "${output_dir}"
  sha256sum gate-and-services.txt llama-server.log piper-known-phrase.wav result.json >SHA256SUMS
)
printf 'Headless AI smoke passed. Evidence: %s\n' "${output_dir}"
