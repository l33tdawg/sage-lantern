#!/usr/bin/env python3
"""One registered MLX arm; synthetic requests only, no live tools or devices."""
import argparse
import datetime as dt
import hashlib
import importlib.metadata
import json
import platform
import statistics
import time
from pathlib import Path
from contract import parse, score, InvalidPlan, SyntheticExecutor


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def verify(root):
    manifest = json.loads((root / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        if digest(root / name) != expected:
            raise ValueError("freeze_file_changed: " + name)
    for name, expected in manifest["source"].items():
        if digest(Path(__file__).with_name(name)) != expected:
            raise ValueError("freeze_source_changed: " + name)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--freeze", type=Path, required=True)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--adapter", type=Path)
    p.add_argument("--backend", choices=["functiongemma", "qwen"], required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    manifest = verify(args.freeze)
    args.output.mkdir(parents=True, exist_ok=False)
    weights = sorted(args.model.glob("*.safetensors"))
    if not weights:
        raise ValueError("local_weights_required")
    registration = {"backend": args.backend, "model": str(args.model.resolve()),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "manifest_sha256": digest(args.freeze / "manifest.json"),
        "model_files": {p.name: digest(p) for p in weights + sorted(args.model.glob("*.json")) + sorted(args.model.glob("*.jinja"))},
        "host": platform.platform(), "machine": platform.machine(),
        "packages": {n: importlib.metadata.version(n) for n in ("mlx", "mlx-lm", "transformers")},
        "generation": manifest["generation"], "tools_executed": "in-memory synthetic executor only"}
    if args.adapter:
        registration["adapter"] = str(args.adapter.resolve())
        registration["adapter_files"] = {p.name: digest(p) for p in sorted(args.adapter.glob("*.safetensors")) + sorted(args.adapter.glob("*.json"))}
        if not registration["adapter_files"]:
            raise ValueError("local_adapter_required")
    write(args.output / "registration.json", registration)
    import mlx.core as mx
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler
    mx.set_memory_limit(24 * 1024 ** 3)
    mx.set_cache_limit(512 * 1024 ** 2)
    loaded = time.perf_counter()
    model, tokenizer = load(str(args.model), adapter_path=str(args.adapter) if args.adapter else None,
                            tokenizer_config={"trust_remote_code": False})
    mx.eval(model.parameters())
    load_seconds = time.perf_counter() - loaded
    if args.backend == "functiongemma":
        tokenizer.add_eos_token("<end_of_turn>")
        tokenizer.add_eos_token("<start_function_response>")
    tools = json.loads((args.freeze / "tools.json").read_text())
    policy = (args.freeze / "policy.txt").read_text()
    requests = [json.loads(line) for line in (args.freeze / "requests.jsonl").read_text().splitlines()]
    rows = []
    # The inference loop never loads expected answers or scoring rules.
    for request in requests:
        messages = [{"role": "system", "content": policy}, {"role": "user", "content": request["question"]}]
        options = {"tools": tools, "tokenize": True, "add_generation_prompt": True}
        if args.backend == "qwen":
            options["enable_thinking"] = False
        prompt = tokenizer.apply_chat_template(messages, **options)
        if len(prompt) > 2048:
            raise ValueError("context_budget_exceeded")
        start, first, text, final = time.perf_counter(), None, "", None
        for chunk in stream_generate(model, tokenizer, prompt=prompt,
                                     max_tokens=manifest["generation"]["max_tokens"],
                                     sampler=make_sampler(temp=0)):
            if chunk.text and first is None:
                first = time.perf_counter() - start
            text += chunk.text
            final = chunk
        error, calls = None, []
        finish = getattr(final, "finish_reason", None)
        try:
            if finish != "stop":
                raise InvalidPlan("generation_not_complete")
            calls = parse(text, args.backend)
        except InvalidPlan as exc:
            error = str(exc)
        receipts = SyntheticExecutor().execute(calls) if calls else []
        row = {**request, "raw": text, "calls": calls, "parse_error": error,
            "synthetic_receipts": receipts, "seconds": time.perf_counter() - start,
            "first_token_seconds": first, "prompt_tokens": len(prompt),
            "generation_tokens": getattr(final, "generation_tokens", None), "finish_reason": finish}
        rows.append(row)
        with (args.output / "outputs.jsonl").open("a") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{args.backend} {request['id']} {row['seconds']:.3f}s {error or 'decoded'}", flush=True)
        mx.clear_cache()
    # Only after all outputs are durable do we read the author gold.
    cases = [json.loads(line) for line in (args.freeze / "cases.jsonl").read_text().splitlines()]
    if [r["id"] for r in rows] != [c["id"] for c in cases]:
        raise ValueError("case_order_mismatch")
    scored = [{**row, "family": c["family"], "language": c["language"],
               **score(c, row["calls"], row["parse_error"], row["raw"])} for c, row in zip(cases, rows)]
    write(args.output / "scores.json", scored)
    families = {}
    for family in sorted({c["family"] for c in cases}):
        group = [r for r in scored if r["family"] == family]
        families[family] = {"total": len(group), **{key: sum(r[key] for r in group)
            for key in ("syntax_valid", "selection_correct", "arguments_correct", "pass", "unexpected_write", "attempted_unrequested_write")}}
    ordered = sorted(r["seconds"] for r in rows)
    summary = {"backend": args.backend, "total": len(rows), "passed": sum(r["pass"] for r in scored),
        "families": families, "load_seconds": load_seconds,
        "first_request_seconds": rows[0]["seconds"], "request_seconds_p50": statistics.median(ordered),
        "request_seconds_p95": ordered[int(.95 * (len(ordered) - 1))],
        "peak_mlx_gb": mx.get_peak_memory() / 1024 ** 3,
        "measurement": "single pass, M5 Max MLX, fresh request caches; not RK3588 or complete task latency",
        "qualification": "unqualified; development counts require raw semantic review and actual backend/device acceptance"}
    write(args.output / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
