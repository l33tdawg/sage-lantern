#!/usr/bin/env python3
"""Preserve raw inference and apply an explicitly recorded decoder correction."""
import argparse
import json
from pathlib import Path
from contract import parse, score, InvalidPlan, SyntheticExecutor
from run import digest, verify, write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--freeze", type=Path, required=True)
    p.add_argument("--original", type=Path, required=True)
    p.add_argument("--backend", choices=["functiongemma", "qwen"], required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    verify(args.freeze)
    cases = [json.loads(line) for line in (args.freeze / "cases.jsonl").read_text().splitlines()]
    rows = [json.loads(line) for line in (args.original / "outputs.jsonl").read_text().splitlines()]
    if [(r["id"], r["question"]) for r in rows] != [(c["id"], c["question"]) for c in cases]:
        raise ValueError("original_request_mismatch")
    args.output.mkdir(parents=True, exist_ok=False)
    scored = []
    for c, r in zip(cases, rows):
        calls, error = [], None
        try:
            if r["finish_reason"] != "stop":
                raise InvalidPlan("generation_not_complete")
            calls = parse(r["raw"], args.backend)
        except InvalidPlan as exc:
            error = str(exc)
        scored.append({**r, "calls": calls, "parse_error": error,
            "synthetic_receipts": SyntheticExecutor().execute(calls) if calls else [],
            "family": c["family"], "language": c["language"], **score(c, calls, error, r["raw"])})
    write(args.output / "scores.json", scored)
    summary = json.loads((args.original / "summary.json").read_text())
    summary["passed"] = sum(r["pass"] for r in scored)
    for family in summary["families"]:
        group = [r for r in scored if r["family"] == family]
        summary["families"][family] = {"total": len(group), **{key: sum(r[key] for r in group)
            for key in ("syntax_valid", "selection_correct", "arguments_correct", "pass", "unexpected_write", "attempted_unrequested_write")}}
    summary["correction"] = "Added Qwen 3.5 native XML decoder; same raw inference, requests and author gold. Also report rejected write attempts. Original results retained."
    summary["original_outputs_sha256"] = digest(args.original / "outputs.jsonl")
    summary["manifest_sha256"] = digest(args.freeze / "manifest.json")
    summary["rescore_source_sha256"] = digest(Path(__file__))
    write(args.output / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
