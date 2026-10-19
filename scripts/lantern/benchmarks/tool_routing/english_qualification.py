#!/usr/bin/env python3
"""Backend-bound calibration and a fixed English challenge; proposals only."""
import argparse
from collections import Counter
import datetime as dt
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import statistics as stats
import time

from bounded_actions import propose
from calibrate_gate import apply_temperature, metrics
from contract import score
from head_trial import load_classifier, messages
from intent_gate import INTENTS, expected_intent, statistics
from router import Lane, Router
from run import digest, verify, write
from run_gate import probabilities

SOURCES = ("english_qualification.py", "head_trial.py", "intent_gate.py", "run.py", "run_gate.py",
           "calibrate_gate.py", "bounded_actions.py", "literal_note.py", "router.py", "contract.py")
PACKAGES = ("mlx", "mlx-lm", "transformers", "tokenizers")
INFERENCE = {"interface": "five-class-hidden-state-head", "execution": "full_compiled",
             "description_orders": [False, True], "context_limit": 2048,
             "device": "Metal GPU index 0",
             "weights": "existing BF16 base plus saved adapter/head; no conversion",
             "request_cache": "none", "tools": [], "add_generation_prompt": True}
CALIBRATION = {"temperatures": [0.5, 0.75, 1.0, 1.5, 2.0, 3.0],
               "thresholds": [0.9, 0.95, 0.98, 0.99, 0.995, 0.999, 1.000001],
               "minimum_support": 5, "read_floor": 0.9, "save_floor": 0.995,
               "selection": "minimum NLL temperature; maximum error-free calibration coverage per intent"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def artifacts(model, bundle):
    files = {p.name: digest(p) for p in sorted(model.iterdir())
             if p.suffix in (".safetensors", ".json", ".jinja")}
    if "tokenizer.json" not in files or "model.safetensors" not in files:
        raise ValueError("local_model_and_tokenizer_required")
    return {"model": files, "bundle": {name: digest(bundle / name) for name in
             ("classifier.safetensors", "registration.json", "training-complete.json")}}


def backend(model, bundle):
    runtime = {}
    for name in PACKAGES:
        distribution = importlib.metadata.distribution(name)
        runtime[name] = {str(p): digest(Path(distribution.locate_file(p))) for p in distribution.files or []
                         if Path(p).suffix in (".py", ".so", ".dylib", ".metallib")}
    return {"artifacts": artifacts(model, bundle), "inference": INFERENCE,
            "packages": {name: importlib.metadata.version(name) for name in PACKAGES},
            "runtime_files": runtime,
            "host": platform.platform(), "machine": platform.machine(),
            "sources": {name: digest(Path(__file__).with_name(name)) for name in SOURCES}}


def require_backend(policy, actual):
    """A matching adapter alone cannot authorize a different tokenizer/backend."""
    if policy.get("backend_sha256") != fingerprint(actual) or policy.get("backend") != actual:
        raise ValueError("calibration_backend_mismatch")


def validate_outputs(rows, requests):
    if len(rows) != len(requests) or [r["id"] for r in rows] != [r["id"] for r in requests]:
        raise ValueError("prediction_request_order_mismatch")
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("duplicate_prediction")
    for row, request in zip(rows, requests):
        if row["question"] != request["question"]:
            raise ValueError("prediction_question_mismatch")
        orders = row["orders"]
        if len(orders) != 2 or [o["reverse"] for o in orders] != [False, True]:
            raise ValueError("description_order_mismatch")
        for order in orders:
            if type(order["reverse"]) is not bool:
                raise ValueError("description_order_type")
            if type(order["prompt_tokens"]) is not int or not 0 < order["prompt_tokens"] <= 2048:
                raise ValueError("prompt_context_invalid")
            values = order["logits"]
            if set(values) != set(INTENTS) or any(type(v) not in (int, float) or not math.isfinite(v) for v in values.values()):
                raise ValueError("invalid_backend_logits")
        if type(row["order_agrees"]) is not bool or row["order_agrees"] != (
                max(orders[0]["logits"], key=orders[0]["logits"].get) == max(orders[1]["logits"], key=orders[1]["logits"].get)):
            raise ValueError("order_agreement_invalid")


def fit_policy(rows, actual, calibration_sha):
    if not rows or any(not r["id"].startswith("calibration-") for r in rows):
        raise ValueError("calibration_only_required")
    trials = [{"temperature": t, **metrics(apply_temperature(rows, t))} for t in CALIBRATION["temperatures"]]
    chosen = min(trials, key=lambda r: r["nll"])
    calibrated = apply_temperature(rows, chosen["temperature"])
    thresholds, support = {}, {}
    for intent in INTENTS:
        group = [r for r in calibrated if r["intent"] == intent and r["order_agrees"]]
        floor = CALIBRATION["save_floor"] if intent == "save" else CALIBRATION["read_floor"]
        choices = []
        for threshold in CALIBRATION["thresholds"]:
            if threshold < floor:
                continue
            admitted = [r for r in group if r["probability"] >= threshold]
            if len(admitted) >= CALIBRATION["minimum_support"] and all(r["correct"] for r in admitted):
                choices.append((len(admitted), threshold))
        count, threshold = max(choices, key=lambda v: (v[0], -v[1])) if choices else (0, 1.000001)
        thresholds[intent] = threshold
        support[intent] = {"accepted": count, "candidates": len(group), "disabled": count == 0}
    return {"schema": "lantern.english-action-policy.lab.v1", "backend": actual,
            "backend_sha256": fingerprint(actual), "adapter_sha256": actual["artifacts"]["bundle"]["classifier.safetensors"],
            "temperature": chosen["temperature"], "thresholds": thresholds,
            "minimum_label_mass": 0.9, "context_limit": 2048,
            "option_order_agreement_required": True, "calibration_support": support,
            "calibration_sha256": calibration_sha, "calibration_metrics": chosen,
            "temperature_trials": trials, "rule": CALIBRATION,
            "qualified": False, "limits": "Synthetic same-author calibration; precision is not authorization or correctness assurance."}


def action_scores(rows, cases, policy, actual):
    require_backend(policy, actual)
    router = Router(policy, [Lane("answer", frozenset({"text"}), 2048),
                            Lane("specialist", frozenset({"tool_proposal"}), 2048),
                            Lane("vision", frozenset({"image"}), 2048)], policy["adapter_sha256"])
    calibrated = apply_temperature(rows, policy["temperature"])
    return [{"id": c["id"], "question": c["question"], "family": c["family"],
             "expected_intent": expected_intent(c["expected"]), "intent": r["intent"],
             "intent_correct": r["intent"] == expected_intent(c["expected"]),
             "decision": (decision := propose(router.route(r), c["question"])),
             **score(c, decision["calls"], None)} for r, c in zip(calibrated, cases)]


def summarize(rows, actions):
    accepted = [r for r in actions if r["decision"]["calls"]]
    positives = [r for r in actions if r["expected_intent"] != "answer"]
    negatives = [r for r in actions if r["expected_intent"] == "answer"]
    timings = sorted(r["milliseconds"] for r in rows)
    return {"total": len(rows), "intent_correct": sum(r["intent_correct"] for r in actions),
        "action_contract_pass": sum(r["pass"] for r in actions),
        "positive_actions": len(positives), "positive_action_pass": sum(r["pass"] for r in positives),
        "negative_requests": len(negatives), "negative_requests_with_action": sum(bool(r["decision"]["calls"]) for r in negatives),
        "accepted_plans": len(accepted), "accepted_plan_correct": sum(r["pass"] for r in accepted),
        "unrequested_write_proposals": sum(r["unexpected_write"] for r in actions),
        "proposed_writes": sum(r["decision"]["effect"] == "write_proposal" for r in actions),
        "order_disagreements": sum(not r["order_agrees"] for r in rows),
        "latency_ms_p50": stats.median(timings), "latency_ms_p95": timings[int((len(timings)-1)*0.95)],
        "families": {family: {"n": len(group), "intent_correct": sum(r["intent_correct"] for r in group),
                              "action_pass": sum(r["pass"] for r in group)}
                     for family in sorted({r["family"] for r in actions})
                     for group in [[r for r in actions if r["family"] == family]]},
        "reasons": dict(Counter(r["decision"]["reason"] for r in actions)),
        "limitations": "Action proposals only; negative abstention is not answer success. Original request as search query is not retrieval relevance. Timings are one Mac pass, include first new shapes, exclude load/warmup. Same-author synthetic test, no real owner/action/device acceptance."}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("model", "bundle", "freeze", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    # Both split bindings and the fixed calibration rule precede all predictions.
    for split in ("calibration", "confirmation"):
        verify(args.freeze / split)
    actual = backend(args.model, args.bundle)
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output / "registration.json", {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "backend": actual, "backend_sha256": fingerprint(actual), "calibration_rule": CALIBRATION,
        "freeze_sha256": digest(args.freeze / "freeze.json"),
        "splits": {s: digest(args.freeze / s / "manifest.json") for s in ("calibration", "confirmation")},
        "confirmation_predictions_seen": False, "model_calls": "local classifier only; no tools executed"})
    source = args.output / "source"
    source.mkdir()
    for name in SOURCES:
        (source / name).write_bytes(Path(__file__).with_name(name).read_bytes())
    import mlx.core as mx
    mx.set_memory_limit(24 * 1024**3)
    mx.set_cache_limit(512 * 1024**2)
    start = time.perf_counter()
    net, tok = load_classifier(args.model, args.bundle)
    mx.eval(net.parameters())
    load_ms = (time.perf_counter() - start) * 1000
    if mx.default_device() != mx.Device(mx.gpu, 0):
        raise ValueError("registered_metal_backend_required")
    call = mx.compile(lambda tokens, lengths: net(tokens, lengths))

    def infer(question):
        start = time.perf_counter()
        orders = []
        for reverse in (False, True):
            tokens = tok.apply_chat_template(messages(question, reverse), tools=[], tokenize=True, add_generation_prompt=True)
            if not 0 < len(tokens) <= INFERENCE["context_limit"]:
                raise ValueError("context_limit")
            logits = call(mx.array([tokens]), mx.array([len(tokens)]))[0]
            mx.eval(logits)
            values = dict(zip(INTENTS, logits.tolist()))
            orders.append({"reverse": reverse, "logits": values, "label_mass": 1.0,
                           "prompt_tokens": len(tokens), "top_intent": max(values, key=values.get)})
        probs = probabilities(orders)
        return {"orders": orders, "probabilities": probs, **statistics(probs), "label_mass": 1.0,
                "order_agrees": orders[0]["top_intent"] == orders[1]["top_intent"],
                "milliseconds": (time.perf_counter() - start) * 1000}

    start = time.perf_counter()
    for _ in range(3):
        infer("Explain the physical meaning of voltage.")
    warmup_ms = (time.perf_counter() - start) * 1000
    mx.reset_peak_memory()
    summaries = {}
    for split in ("calibration", "confirmation"):
        # Check the sources/artifacts again before crossing each split boundary.
        if backend(args.model, args.bundle) != actual:
            raise ValueError("registered_backend_changed")
        verify(args.freeze / split)
        out = args.output / split
        out.mkdir()
        requests = [json.loads(line) for line in (args.freeze / split / "requests.jsonl").read_text().splitlines()]
        rows = []
        with (out / "outputs.jsonl").open("x") as handle:
            for request in requests:
                row = {**request, **infer(request["question"])}
                rows.append(row)
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                handle.flush()
        # No expected label read until this split's predictions are durable.
        validate_outputs(rows, requests)
        cases = [json.loads(line) for line in (args.freeze / split / "cases.jsonl").read_text().splitlines()]
        if [{"id": c["id"], "question": c["question"]} for c in cases] != requests:
            raise ValueError("case_request_mismatch")
        scored = [{**r, "expected_intent": expected_intent(c["expected"]),
                   "correct": r["intent"] == expected_intent(c["expected"])} for r, c in zip(rows, cases)]
        write(out / "scores.json", scored)
        if split == "calibration":
            policy = fit_policy(scored, actual, digest(out / "scores.json"))
            write(args.output / "policy.json", policy)
            policy_sha = digest(args.output / "policy.json")
        else:
            if digest(args.output / "policy.json") != policy_sha:
                raise ValueError("calibration_policy_changed")
            policy = json.loads((args.output / "policy.json").read_text())
        actions = action_scores(scored, cases, policy, actual)
        write(out / "actions.json", actions)
        summaries[split] = summarize(rows, actions)
        write(out / "summary.json", summaries[split])
        print(split, json.dumps(summaries[split]), flush=True)
    write(args.output / "RESULT.json", {"summaries": summaries, "load_ms": load_ms, "warmup_ms": warmup_ms,
        "peak_mlx_gib": mx.get_peak_memory() / 1024**3, "policy_sha256": policy_sha,
        "backend_sha256": fingerprint(actual), "qualified": False, "device_access": False})


if __name__ == "__main__":
    main()
