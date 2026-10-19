#!/usr/bin/env python3
"""Evaluate the existing Qwen model as the router's larger fallback judge."""
import argparse
import hashlib
import datetime as dt
import json
import math
import statistics as stats
import time
from pathlib import Path
from intent_gate import INTENTS, gate_messages, expected_intent, statistics
from run import digest, write, verify


def probabilities(orders, temperature=1.0):
    values = []
    for order in orders:
        logits = order["logits"]
        peak = max(logits.values())
        ex = {k: math.exp((v-peak)/temperature) for k,v in logits.items()}
        total = sum(ex.values())
        values.append({k:v/total for k,v in ex.items()})
    return {k:sum(v[k] for v in values)/len(values) for k in INTENTS}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--freeze",type=Path,required=True)
    p.add_argument("--model",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    verify(args.freeze)
    args.output.mkdir(parents=True,exist_ok=False)
    model_hashes={p.name:digest(p) for p in sorted(args.model.glob("*.safetensors"))}
    model_id=hashlib.sha256(json.dumps(model_hashes,sort_keys=True).encode()).hexdigest()
    write(args.output/"registration.json",{"created_at":dt.datetime.now(dt.timezone.utc).isoformat(),
        "manifest_sha256":digest(args.freeze/"manifest.json"),"source_sha256":digest(Path(__file__)),
        "gate_sha256":digest(Path(__file__).with_name("intent_gate.py")),
        "model_sha256":model_hashes,
        "adapter_sha256":model_id,"method":"unmodified Qwen; two opposite option-label mappings; native thinking disabled; full next-token logits; no generation",
        "evaluation_gold_loaded_during_inference":False})
    import mlx.core as mx
    from mlx_lm import load
    mx.set_memory_limit(24*1024**3)
    mx.set_cache_limit(512*1024**2)
    model,tok=load(str(args.model),tokenizer_config={"trust_remote_code":False})
    mx.eval(model.parameters())
    rows=[]
    for request in [json.loads(s) for s in (args.freeze/"requests.jsonl").read_text().splitlines()]:
        start=time.perf_counter()
        orders=[]
        for reverse in (False,True):
            messages,mapping=gate_messages(request["question"],reverse)
            prompt=tok.apply_chat_template(messages,tools=[],tokenize=True,add_generation_prompt=True,enable_thinking=False)
            if len(prompt)>2048:
                raise ValueError("gate_context_exceeded")
            ids={intent:tok.encode(label,add_special_tokens=False) for label,intent in mapping.items()}
            if any(len(v)!=1 for v in ids.values()):
                raise ValueError("label_not_one_token")
            logits=model(mx.array([prompt]))[0,-1,:].astype(mx.float32)
            selected=mx.stack([logits[ids[intent][0]] for intent in INTENTS])
            log_mass=mx.logsumexp(selected)-mx.logsumexp(logits)
            mx.eval(selected,log_mass)
            raw=dict(zip(INTENTS,selected.tolist()))
            orders.append({"reverse":reverse,"logits":raw,"label_mass":math.exp(float(log_mass.item())),
                           "prompt_tokens":len(prompt),"top_intent":max(raw,key=raw.get)})
            mx.clear_cache()
        probs=probabilities(orders)
        row={**request,"orders":orders,"probabilities":probs,**statistics(probs),
             "order_agrees":orders[0]["top_intent"]==orders[1]["top_intent"],
             "label_mass":min(o["label_mass"] for o in orders),"seconds":time.perf_counter()-start}
        rows.append(row)
        with (args.output/"outputs.jsonl").open("a") as f:
            f.write(json.dumps(row,ensure_ascii=False)+"\n")
        print(request["id"],row["intent"],round(row["probability"],4),round(row["seconds"],3),flush=True)
    cases=[json.loads(s) for s in (args.freeze/"cases.jsonl").read_text().splitlines()]
    if [r["id"] for r in rows]!=[c["id"] for c in cases]:
        raise ValueError("case_order_changed")
    scored=[{**r,"expected_intent":expected_intent(c["expected"]),"correct":r["intent"]==expected_intent(c["expected"])} for r,c in zip(rows,cases)]
    write(args.output/"scores.json",scored)
    write(args.output/"summary.json",{"total":len(rows),"correct":sum(r["correct"] for r in scored),
        "option_order_disagreements":sum(not r["order_agrees"] for r in rows),"minimum_label_mass":min(r["label_mass"] for r in rows),
        "request_seconds_p50":stats.median(r["seconds"] for r in rows),"peak_mlx_gb":mx.get_peak_memory()/1024**3})
    print((args.output/"summary.json").read_text())


if __name__=="__main__":
    main()
