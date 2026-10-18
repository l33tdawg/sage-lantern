#!/usr/bin/env python3
"""Register one retention/English-contrast correction, on inspected checks."""
import argparse
from collections import Counter
import datetime as dt
import json
from pathlib import Path
import random

from build_english_qualification import canonical
from intent_gate import expected_intent
from run import digest, write
from train_english_head import PARAMETERS, TOPICS, SAVE


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("work/functiongemma-english-20261002"))
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    old = Path("work/functiongemma-20261002/contrast-v3")
    initial = old / "head"
    data = {}
    def add(question, intent):
        key = canonical(question)
        if key in data and data[key]["intent"] != intent:
            raise ValueError("retention_label_conflict")
        data[key] = {"question": question, "intent": intent}
    for line in (old / "train.jsonl").read_text().splitlines():
        row = json.loads(line)
        if not row["id"].startswith("action-"):
            continue
        calls = [c["function"] for c in row["messages"][-1].get("tool_calls", [])]
        add(row["messages"][1]["content"], expected_intent(calls))
    retained = len(data)
    for line in (args.root / "training-v2" / "train.jsonl").read_text().splitlines():
        row = json.loads(line)
        add(row["question"], row["intent"])
    # Vary the literal content while keeping the requested intent constant.
    for i, topic in enumerate(TOPICS):
        bodies = [f"Meet Dana at {9+i%8:02d}:15 on {3+i%20} June. Bring the green folder.",
                  ["Pat said: \"Keep the original wording.\"", "Measure 48.5 cm; tolerance ±0.2 cm.",
                   "Leave it beside the side door, not the main entrance.", "Order four, total price €16.80.",
                   "Take the 07:40 bus; get off after the bridge.", "Use half a teaspoon; stir gently.",
                   "The label reads: café / crème / piñata.", "Keep the old one until its replacement arrives."][i%8]]
        for body in bodies:
            for pattern in SAVE:
                add(pattern.format(t=topic.title(), b=body), "save")
    excluded = set()
    for suite in ("freeze", "new-check"):
        for split in ("calibration", "confirmation"):
            excluded.update(canonical(json.loads(line)["question"]) for line in
                            (args.root / suite / split / "requests.jsonl").read_text().splitlines())
    if set(data) & excluded:
        raise ValueError("inspected_question_in_training")
    args.output.mkdir(parents=True, exist_ok=False)
    rows = list(data.values())
    random.Random(PARAMETERS["seed"]).shuffle(rows)
    (args.output / "train.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    write(args.output / "registration.json", {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "parameters": {**PARAMETERS, "epochs": 1, "learning_rate": 2e-5},
        "training_sha256": digest(args.output / "train.jsonl"), "unique_requests": len(rows),
        "counts": dict(Counter(r["intent"] for r in rows)), "retained_old_requests": retained,
        "initial_bundle": {"path": str(initial.resolve()), "files": {name: digest(initial / name) for name in
            ("classifier.safetensors", "registration.json", "training-complete.json")}},
        "base_model_sha256": {p.name: digest(p) for p in args.model.glob("*.safetensors")},
        "model_files": {p.name: digest(p) for p in args.model.iterdir() if p.suffix in (".safetensors", ".json", ".jinja")},
        "sources": {name: digest(Path(__file__).with_name(name)) for name in
            ("train_english_head.py", "build_english_retention.py", "head_trial.py", "intent_gate.py", "build_english_qualification.py")},
        "overlap_with_checks": 0,
        "selection": "One corrective candidate, warm start from prior contrast head, 1 epoch, LR 2e-5; architecture unchanged.",
        "evaluation_status": "Both checks are now inspected. No fresh qualification claim. No model selection from a new hidden test.",
        "limits": "Prior mixed-language curriculum retained to avoid losing previous coverage; new work targets English. Shared author/templates; no live actions."})
    print(json.dumps({"rows": len(rows), "retained_old": retained, "counts": Counter(r["intent"] for r in rows)}))


if __name__ == "__main__":
    main()
