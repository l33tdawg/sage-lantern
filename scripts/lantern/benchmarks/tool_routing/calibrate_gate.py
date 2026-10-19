#!/usr/bin/env python3
"""Fit temperature and abstention thresholds on calibration only, never test."""
import argparse
import json
import math
from pathlib import Path
from intent_gate import INTENTS, statistics
from run_gate import probabilities
from run import digest,write


def metrics(rows):
    n=len(rows)
    bins=[]
    for i in range(10):
        group=[r for r in rows if i/10<=r["probability"]<(i+1)/10 or i==9 and r["probability"]==1]
        if group:
            bins.append({"n":len(group),"confidence":sum(r["probability"] for r in group)/len(group),
                         "accuracy":sum(r["correct"] for r in group)/len(group)})
    return {"accuracy":sum(r["correct"] for r in rows)/n,
        "nll":-sum(math.log(max(1e-12,r["probabilities"][r["expected_intent"]])) for r in rows)/n,
        "brier":sum(sum((p-(k==r["expected_intent"]))**2 for k,p in r["probabilities"].items()) for r in rows)/n,
        "ece10":sum(b["n"]/n*abs(b["confidence"]-b["accuracy"]) for b in bins),"bins":bins}


def apply_temperature(rows,t):
    result=[]
    for r in rows:
        probs=probabilities(r["orders"],t)
        s=statistics(probs)
        result.append({**r,"probabilities":probs,**s,"correct":s["intent"]==r["expected_intent"]})
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--calibration",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    rows=json.loads((args.calibration/"scores.json").read_text())
    if not all(r["id"].startswith("calibration-") for r in rows):
        raise ValueError("calibration_only_required")
    temperatures=[0.5,0.75,1.0,1.5,2.0,3.0]
    trials=[{"temperature":t,**metrics(apply_temperature(rows,t))} for t in temperatures]
    chosen=min(trials,key=lambda x:x["nll"])
    calibrated=apply_temperature(rows,chosen["temperature"])
    # Zero observed calibration errors is a policy for this lab, not a guarantee.
    thresholds={}
    support={}
    for intent in INTENTS:
        floor=0.995 if intent=="save" else 0.9
        group=[r for r in calibrated if r["intent"]==intent and r["order_agrees"] and r["label_mass"]>=0.9]
        choices=[]
        for t in [0.9,0.95,0.98,0.99,0.995,0.999,1.000001]:
            if t<floor:
                continue
            admitted=[r for r in group if r["probability"]>=t]
            if all(r["correct"] for r in admitted):
                choices.append((len(admitted),t))
        count,t=max(choices,key=lambda x:(x[0],-x[1]))
        thresholds[intent]=t
        support[intent]={"accepted":count,"calibration_candidates":len(group),"errors":0}
    policy={"schema":"lantern.intent-gate.lab.v1","temperature":chosen["temperature"],"thresholds":thresholds,
        "minimum_label_mass":0.9,"context_limit":2048,"option_order_agreement_required":True,
        "calibration_support":support,"calibration_sha256":digest(args.calibration/"scores.json"),
        "adapter_sha256":json.loads((args.calibration/"registration.json").read_text())["adapter_sha256"],
        "calibration_metrics":chosen,"temperature_trials":trials,
        "limits":"Small authored synthetic calibration. Probabilities are not user authorization or correctness guarantees."}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.output.exists():
        raise ValueError("policy_already_exists")
    write(args.output,policy)
    print(json.dumps({k:policy[k] for k in ("temperature","thresholds","calibration_support","calibration_metrics")},indent=2))


if __name__=="__main__":
    main()
