#!/usr/bin/env python3
"""Reload a saved classifier head and evaluate immutable requests."""
import argparse
import json
import time
from pathlib import Path
from head_trial import messages,load_classifier
from intent_gate import INTENTS,statistics,expected_intent
from run_gate import probabilities
from run import digest,write,verify


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model",type=Path,required=True)
    p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--freeze",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    verify(args.freeze)
    args.output.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((args.bundle/"training-complete.json").read_text())
    if digest(args.bundle/"classifier.safetensors")!=manifest["classifier_sha256"]:raise ValueError("classifier_bundle_changed")
    write(args.output/"registration.json",{"adapter_sha256":manifest["classifier_sha256"],
        "manifest_sha256":digest(args.freeze/"manifest.json"),"source_sha256":digest(Path(__file__)),
        "model_sha256":json.loads((args.bundle/"registration.json").read_text())["base_model_sha256"]})
    import mlx.core as mx
    mx.set_memory_limit(24*1024**3);mx.set_cache_limit(512*1024**2)
    net,tok=load_classifier(args.model,args.bundle)
    mx.eval(net.parameters())
    mx.reset_peak_memory()
    rows=[]
    for r in map(json.loads,(args.freeze/"requests.jsonl").read_text().splitlines()):
        start=time.perf_counter();orders=[]
        for reverse in (False,True):
            tokens=tok.apply_chat_template(messages(r["question"],reverse),tools=[],tokenize=True,add_generation_prompt=True)
            if len(tokens)>2048:raise ValueError("context_limit")
            logits=net(mx.array([tokens]),mx.array([len(tokens)]))[0];mx.eval(logits)
            raw=dict(zip(INTENTS,logits.tolist()))
            orders.append({"reverse":reverse,"logits":raw,"label_mass":1.0,"prompt_tokens":len(tokens),"top_intent":max(raw,key=raw.get)})
            mx.clear_cache()
        probs=probabilities(orders)
        row={**r,"orders":orders,"probabilities":probs,**statistics(probs),"label_mass":1.0,
            "order_agrees":orders[0]["top_intent"]==orders[1]["top_intent"],"seconds":time.perf_counter()-start}
        rows.append(row)
        with (args.output/"outputs.jsonl").open("a") as f:f.write(json.dumps(row,ensure_ascii=False)+"\n")
    cases=list(map(json.loads,(args.freeze/"cases.jsonl").read_text().splitlines()))
    if [r["id"] for r in rows]!=[c["id"] for c in cases]:raise ValueError("case_mismatch")
    scored=[{**r,"expected_intent":expected_intent(c["expected"]),"correct":r["intent"]==expected_intent(c["expected"])} for r,c in zip(rows,cases)]
    write(args.output/"scores.json",scored)
    write(args.output/"summary.json",{"total":len(rows),"correct":sum(r["correct"] for r in scored),
        "order_disagreements":sum(not r["order_agrees"] for r in rows),"request_seconds_p50":sorted(r["seconds"] for r in rows)[len(rows)//2],
        "peak_mlx_gb":mx.get_peak_memory()/1024**3,"model_bytes":sum(p.stat().st_size for p in args.model.glob("*.safetensors"))})
    print((args.output/"summary.json").read_text())


if __name__=="__main__":main()
