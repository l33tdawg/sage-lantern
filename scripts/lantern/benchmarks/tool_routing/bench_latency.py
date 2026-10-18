#!/usr/bin/env python3
"""Paired latency/decision parity trial of the saved five-class specialist.

No training, new accuracy claim, SAGE calls or device access. These already
inspected requests measure implementation sensitivity, not fresh qualification.
"""
import argparse
import copy
import datetime as dt
import hashlib
import importlib.metadata
import json
import statistics as stats
import time
from pathlib import Path
from head_trial import load_classifier, messages
from intent_gate import INTENTS, expected_intent, statistics
from run_gate import probabilities
from run import digest, verify, write
from router import Router, Lane
from bounded_actions import propose

MODES = ("full_clear", "full_resident", "full_batch2", "static_prefix", "full_compiled")


def static_tokens(tokenizer, reverse):
    # Compile only the fixed system instructions. No user query/history/recall
    # contributes to a reusable prefix, even during cache construction.
    return tokenizer.apply_chat_template([messages("", reverse)[0]], tools=[],
                                        tokenize=True, add_generation_prompt=False)


def suffix(tokens, prefix):
    if not prefix or len(tokens) <= len(prefix) or tokens[:len(prefix)] != prefix:
        raise ValueError("static_prefix_mismatch")
    return tokens[len(prefix):]


def percentile(values, quantile):
    values = sorted(values)
    return values[min(len(values)-1, int((len(values)-1)*quantile))]


def cache_fingerprint(caches):
    h = hashlib.sha256()
    for cache in caches:
        h.update(repr(cache.meta_state).encode())
        for array in cache.state:
            h.update(str((array.shape, array.dtype)).encode())
            h.update(array.astype(__import__('mlx.core', fromlist=['float32']).float32).tolist().__repr__().encode())
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("model", "bundle", "freeze", "policy", "output"):
        p.add_argument("--"+name, type=Path, required=True)
    p.add_argument("--repeats", type=int, default=3)
    args = p.parse_args()
    if not 2 <= args.repeats <= 10:
        raise ValueError("repeat_bounds")
    verify(args.freeze)
    args.output.mkdir(parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent
    snapshot = args.output/"source"
    snapshot.mkdir()
    for path in here.glob("*.py"):
        (snapshot/path.name).write_bytes(path.read_bytes())
    write(args.output/"registration.json", {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sources": {f.name: digest(f) for f in snapshot.glob("*.py")},
        "manifest_sha256": digest(args.freeze/"manifest.json"),
        "classifier_sha256": digest(args.bundle/"classifier.safetensors"),
        "policy_sha256": digest(args.policy),
        "model_files": {f.name:digest(f) for f in args.model.iterdir()
                        if f.suffix in (".safetensors", ".json", ".jinja")},
        "variants": MODES, "repeats": args.repeats,
        "order": "rotate variant blocks each repeat; identical immutable requests",
        "heldout_gold_read_before_outputs": False,
        "accuracy_status": "inspected set; decision/numerical parity only",
        "packages": {name:importlib.metadata.version(name) for name in ("mlx", "mlx-lm", "transformers")},
    })
    import mlx.core as mx
    from mlx_lm.models.cache import make_prompt_cache
    mx.set_memory_limit(24*1024**3)
    mx.set_cache_limit(512*1024**2)
    start = time.perf_counter()
    net, tok = load_classifier(args.model, args.bundle)
    mx.eval(net.parameters())
    load_ms = (time.perf_counter()-start)*1000
    compiled = mx.compile(lambda tokens, lengths:net(tokens, lengths))
    prefixes = [static_tokens(tok, reverse) for reverse in (False, True)]
    start = time.perf_counter()
    caches = []
    for prefix in prefixes:
        cache = make_prompt_cache(net.backbone)
        hidden = net.backbone.model(mx.array([prefix]), cache=cache)
        mx.eval(hidden, [c.state for c in cache])
        caches.append(cache)
    build_ms = (time.perf_counter()-start)*1000
    cache_sha = [cache_fingerprint(c) for c in caches]
    cache_bytes = sum(c.nbytes for cs in caches for c in cs)
    requests = list(map(json.loads, (args.freeze/"requests.jsonl").read_text().splitlines()))
    policy = json.loads(args.policy.read_text())
    router = Router(policy, [Lane("answer", frozenset({"text"}), 2048),
                            Lane("specialist", frozenset({"tool_proposal"}), 2048),
                            Lane("vision", frozenset({"image"}), 2048)],
                    digest(args.bundle/"classifier.safetensors"))
    rows = []
    def infer(question, mode):
        start = time.perf_counter()
        tokens = [tok.apply_chat_template(messages(question, reverse), tools=[],
                  tokenize=True, add_generation_prompt=True) for reverse in (False, True)]
        if any(not 0 < len(t) <= 2048 for t in tokens):
            raise ValueError("context_limit")
        raw = []
        if mode == "full_batch2":
            size = max(map(len, tokens))
            batch = mx.array([t+[0]*(size-len(t)) for t in tokens])
            logits = net(batch, mx.array(list(map(len, tokens))))
            mx.eval(logits)
            raw = logits.tolist()
        else:
            for i, t in enumerate(tokens):
                if mode == "static_prefix":
                    rest = suffix(t, prefixes[i])
                    # Mutable per-request state is forked, then discarded.
                    # Never trim/reuse a request cache as the global prefix.
                    fork = copy.deepcopy(caches[i])
                    hidden = net.backbone.model(mx.array([rest]), cache=fork)
                    logits = net.classifier(hidden[0, -1]).astype(mx.float32)
                else:
                    call = compiled if mode == "full_compiled" else net
                    logits = call(mx.array([t]), mx.array([len(t)]))[0]
                mx.eval(logits)
                raw.append(logits.tolist())
                if mode == "full_clear":
                    mx.clear_cache()
        orders = [{"reverse":bool(i), "logits":dict(zip(INTENTS, values)),
                   "label_mass":1.0, "prompt_tokens":len(tokens[i]),
                   "top_intent":INTENTS[max(range(len(values)), key=values.__getitem__)]}
                  for i, values in enumerate(raw)]
        probs = probabilities(orders)
        row = {"orders":orders, "probabilities":probs, **statistics(probs),
               "label_mass":1.0, "order_agrees":orders[0]["top_intent"] == orders[1]["top_intent"]}
        calibrated_probs = probabilities(orders, policy["temperature"])
        calibrated = {**row, "probabilities":calibrated_probs, **statistics(calibrated_probs)}
        row["decision"] = propose(router.route(calibrated), question)
        row["milliseconds"] = (time.perf_counter()-start)*1000
        return row
    # Equal warm-up count. Separate from measured requests/cold-load cost.
    for mode in MODES:
        infer("Explain the meaning of a battery.", mode)
    mx.reset_peak_memory()
    for repeat in range(args.repeats):
        variants = MODES[repeat % len(MODES):] + MODES[:repeat % len(MODES)]
        for mode in variants:
            for request in requests:
                row = {"id":request["id"], "repeat":repeat, "mode":mode,
                       **infer(request["question"], mode)}
                rows.append(row)
                with (args.output/"outputs.jsonl").open("a") as f:
                    f.write(json.dumps(row, ensure_ascii=False)+"\n")
            print("LATENCY", repeat, mode, flush=True)
    # Inference is durable before reading any expected labels.
    cases = list(map(json.loads, (args.freeze/"cases.jsonl").read_text().splitlines()))
    if [r["id"] for r in requests] != [c["id"] for c in cases]:
        raise ValueError("request_order_changed")
    gold = {c["id"]:expected_intent(c["expected"]) for c in cases}
    baselines = {(r["repeat"],r["id"]):r for r in rows if r["mode"] == "full_clear"}
    summaries = {}
    for mode in MODES:
        selected = [r for r in rows if r["mode"] == mode]
        pairs = [(r, baselines[(r["repeat"],r["id"])]) for r in selected]
        summaries[mode] = {
            "n":len(selected), "median_ms":stats.median(r["milliseconds"] for r in selected),
            "p95_ms":percentile([r["milliseconds"] for r in selected], .95),
            "correct_per_repeat":[sum(r["intent"] == gold[r["id"]] for r in selected if r["repeat"]==repeat) for repeat in range(args.repeats)],
            "intent_changes":sum(r["intent"] != b["intent"] for r,b in pairs),
            "order_agreement_changes":sum(r["order_agrees"] != b["order_agrees"] for r,b in pairs),
            "controller_decision_changes":sum(r["decision"] != b["decision"] for r,b in pairs),
            "action_and_route_changes":sum(any(r["decision"].get(k) != b["decision"].get(k)
                                           for k in ("lane", "intent", "reason", "effect", "calls")) for r,b in pairs),
            "maximum_logit_delta":max(abs(x["logits"][k]-y["logits"][k]) for r,b in pairs for x,y in zip(r["orders"], b["orders"]) for k in INTENTS),
            "maximum_probability_delta":max(abs(r["probabilities"][k]-b["probabilities"][k]) for r,b in pairs for k in INTENTS),
        }
    write(args.output/"summary.json", {
        "model_load_ms":load_ms, "prefix_build_ms":build_ms,
        "static_prefix_tokens":list(map(len,prefixes)), "static_cache_bytes":cache_bytes,
        "static_cache_unchanged":cache_sha == [cache_fingerprint(c) for c in caches],
        "peak_mlx_gib":mx.get_peak_memory()/1024**3, "variants":summaries,
        "limitations":"Mac MLX resident classifier, not generated-answer TTFT, RK3588 or end-to-end UX. Inspected synthetic requests; numerical parity is not fresh semantic qualification. Prefix caches remain in RAM; no owner data cached.",
    })
    print((args.output/"summary.json").read_text(), flush=True)


if __name__ == "__main__":
    main()
