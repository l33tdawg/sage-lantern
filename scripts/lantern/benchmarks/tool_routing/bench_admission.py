#!/usr/bin/env python3
"""Paired local admission regression/timing; all requests already inspected.

Compare unchanged calibrated FunctionGemma, whole-request validation of that
proposal, and a separately disabled exact-grammar candidate. No fresh accuracy
or RK3588 claim. Persist raw predictions before loading gold for scoring.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import statistics
import time

from action_boundary import admit_current
from bounded_actions import propose
from contract import score
from english_qualification import SOURCES, backend, fingerprint, require_backend
from head_trial import load_classifier, messages
from intent_gate import INTENTS, statistics as intent_statistics
from qualify_admission import summarize_actions
from router import Lane, Router
from run import digest, verify, write
from run_gate import probabilities


VARIANTS = ("retention_classifier", "retention_with_boundary", "exact_grammar_candidate")
NEW_SOURCES = ("bench_admission.py", "action_boundary.py", "action_session.py", "qualify_admission.py", "voice_path_metrics.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("work/functiongemma-english-20261002"))
    parser.add_argument("--model", type=Path, default=Path("work/functiongemma-20261002/models/functiongemma-bf16"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        raise ValueError("bounded_repeats_required")
    bundle = args.root / "retention-head"
    splits = {"inspected_100": (args.root / "freeze/confirmation", args.root / "retention-comparison"),
              "inspected_60": (args.root / "new-check/confirmation", args.root / "retention-new-check")}
    args.output.mkdir(parents=True, exist_ok=False)
    actual = backend(args.model, bundle)
    policies, requests, references, routers = {}, {}, {}, {}
    for name, (freeze, previous) in splits.items():
        verify(freeze)
        policies[name] = json.loads((previous / "policy.json").read_text())
        require_backend(policies[name], actual)
        requests[name] = [json.loads(s) for s in (freeze / "requests.jsonl").read_text().splitlines()]
        # Inspected logit references are for replay parity, never recalibration.
        references[name] = {r["id"]: r for r in json.loads((previous / "confirmation/scores.json").read_text())}
        routers[name] = Router(policies[name], [Lane("answer", frozenset({"text"}), 2048),
            Lane("specialist", frozenset({"tool_proposal"}), 2048), Lane("vision", frozenset({"image"}), 2048)],
            policies[name]["adapter_sha256"])
    source_hashes = {n: digest(Path(__file__).with_name(n)) for n in set(SOURCES + NEW_SOURCES + ("qualify_admission.py", "build_english_qualification.py"))}
    source = args.output / "source"
    source.mkdir()
    for name in source_hashes:
        (source / name).write_bytes(Path(__file__).with_name(name).read_bytes())
    write(args.output / "registration.json", {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "backend_sha256": fingerprint(actual), "backend": actual, "sources": source_hashes,
        "variants": VARIANTS, "repeats": args.repeats,
        "inputs": {n: {"manifest": digest(f / "manifest.json"), "requests": digest(f / "requests.jsonl"),
                       "policy": digest(p / "policy.json"), "reference_scores": digest(p / "confirmation/scores.json")} for n, (f, p) in splits.items()},
        "scope": "Inspected same-author regression; no threshold fitting/training. Exact grammar is a separate policy, not model accuracy.",
        "timing": "Alternate variant order per request/repetition; include tokenization/two compiled forwards/proposals where used, grammar has no model call; exclude load/warmup/auth/ASR/answers.",
        "device_access": False, "tools_executed": False, "qualified": False})
    import mlx.core as mx
    mx.set_memory_limit(24 * 1024**3)
    mx.set_cache_limit(512 * 1024**2)
    if mx.default_device() != mx.Device(mx.gpu, 0):
        raise ValueError("registered_metal_backend_required")
    loaded = time.perf_counter()
    net, tokenizer = load_classifier(args.model, bundle)
    mx.eval(net.parameters())
    load_ms = (time.perf_counter() - loaded) * 1000
    compiled = mx.compile(lambda tokens, lengths: net(tokens, lengths))

    def infer(question):
        orders = []
        for reverse in (False, True):
            tokens = tokenizer.apply_chat_template(messages(question, reverse), tools=[], tokenize=True, add_generation_prompt=True)
            if not 0 < len(tokens) <= 2048:
                raise ValueError("context_limit")
            logits = compiled(mx.array([tokens]), mx.array([len(tokens)]))[0]
            mx.eval(logits)
            values = dict(zip(INTENTS, logits.tolist()))
            orders.append({"reverse": reverse, "logits": values, "label_mass": 1.0, "prompt_tokens": len(tokens)})
        probs = probabilities(orders)
        return {"orders": orders, "probabilities": probs, **intent_statistics(probs), "label_mass": 1.0,
                "order_agrees": max(orders[0]["logits"], key=orders[0]["logits"].get) == max(orders[1]["logits"], key=orders[1]["logits"].get)}

    for _ in range(3):
        infer("Explain the physical meaning of voltage.")
    outputs = []
    with (args.output / "outputs.jsonl").open("x") as handle:
        for repeat in range(args.repeats):
            for split, reqs in requests.items():
                for i, request in enumerate(reqs):
                    variants = VARIANTS if (i + repeat) % 2 == 0 else tuple(reversed(VARIANTS))
                    for variant in variants:
                        start = time.perf_counter_ns()
                        logits = None
                        if variant == "exact_grammar_candidate":
                            decision = admit_current(request["question"], allow_exact_grammar=True)
                        else:
                            raw = infer(request["question"])
                            logits = [o["logits"] for o in raw["orders"]]
                            calibrated = probabilities(raw["orders"], policies[split]["temperature"])
                            judgment = {**raw, "probabilities": calibrated, **intent_statistics(calibrated)}
                            decision = propose(routers[split].route(judgment), request["question"])
                            if variant == "retention_with_boundary":
                                decision = admit_current(request["question"], proposal=decision)
                        elapsed = (time.perf_counter_ns() - start) / 1e6
                        difference = None if logits is None else max(abs(logits[j][k] - references[split][request["id"]]["orders"][j]["logits"][k]) for j in (0, 1) for k in INTENTS)
                        output = {**request, "split": split, "variant": variant, "repeat": repeat,
                                  "decision": decision, "milliseconds": elapsed,
                                  "logits": logits, "max_reference_logit_difference": difference}
                        outputs.append(output)
                        handle.write(json.dumps(output, ensure_ascii=False, allow_nan=False) + "\n")
                    handle.flush()
    if any(digest(Path(__file__).with_name(n)) != sha for n, sha in source_hashes.items()) or backend(args.model, bundle) != actual:
        raise ValueError("registered_source_or_backend_changed")
    result = {}
    actions = []
    for split, (freeze, _) in splits.items():
        verify(freeze)
        cases = {c["id"]: c for c in (json.loads(s) for s in (freeze / "cases.jsonl").read_text().splitlines())}
        result[split] = {}
        for variant in VARIANTS:
            group = [r for r in outputs if r["split"] == split and r["variant"] == variant]
            first = [r for r in group if r["repeat"] == 0]
            scored = [{**cases[r["id"]], **r, **score(cases[r["id"]], r["decision"]["calls"], None)} for r in first]
            actions.extend(scored)
            times = sorted(r["milliseconds"] for r in group)
            stable = all(r["decision"] == next(v["decision"] for v in first if v["id"] == r["id"]) for r in group)
            result[split][variant] = {**summarize_actions(scored), "timing_n": len(times),
                "p50_ms": statistics.median(times), "p95_ms": times[int(.95 * (len(times) - 1))],
                "repeat_decisions_identical": stable,
                "max_reference_logit_difference": max((r["max_reference_logit_difference"] for r in group if r["logits"] is not None), default=None)}
    write(args.output / "actions.json", actions)
    write(args.output / "RESULT.json", {"splits": result, "load_ms": load_ms,
        "qualified": False, "independent_accuracy": False, "device_access": False,
        "limits": "No real SAGE actions, retrieval/answer qualification, fresh independent commands, real speech or RK3588 measurements."})
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
