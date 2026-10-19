#!/usr/bin/env python3
"""Evaluate a preregistered bounded-action variant without generating arguments."""
import argparse
import json
from collections import Counter
from pathlib import Path
from intent_gate import expected_intent
from bounded_actions import propose
from router import Router,Lane
from calibrate_gate import apply_temperature
from contract import score
from run import digest,write


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--freeze",type=Path,required=True)
    p.add_argument("--gate",type=Path,required=True)
    p.add_argument("--policy",type=Path,required=True)
    p.add_argument("--registration",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    reg=json.loads(args.registration.read_text())
    for name,sha in reg["sources"].items():
        if digest(Path(__file__).with_name(name))!=sha:raise ValueError("bounded_source_changed")
    if reg["confirmation_manifest_sha256"]!=digest(args.freeze/"manifest.json"):raise ValueError("confirmation_freeze_changed")
    args.output.mkdir(parents=True,exist_ok=False)
    policy=json.loads(args.policy.read_text())
    gate=apply_temperature(json.loads((args.gate/"scores.json").read_text()),policy["temperature"])
    cases=[json.loads(s) for s in (args.freeze/"cases.jsonl").read_text().splitlines()]
    if [r["id"] for r in gate]!=[c["id"] for c in cases] or any(r["question"]!=c["question"] for r,c in zip(gate,cases)):
        raise ValueError("request_mismatch")
    sha=json.loads((args.gate/"registration.json").read_text())["adapter_sha256"]
    router=Router(policy,[Lane("answer",frozenset({"text"}),2048),Lane("specialist",frozenset({"tool_proposal"}),2048),
                          Lane("vision",frozenset({"image"}),2048)],sha)
    rows=[]
    for c,g in zip(cases,gate):
        decision=propose(router.route(g),c["question"])
        rows.append({"id":c["id"],"question":c["question"],"family":c["family"],"decision":decision,
                     **score(c,decision["calls"],None,"")})
    accepted=[r for r in rows if r["decision"]["calls"]]
    write(args.output/"scores.json",rows)
    write(args.output/"summary.json",{"total":len(rows),"action_contract_pass":sum(r["pass"] for r in rows),
        "accepted_tool_plans":len(accepted),"accepted_tool_plan_correct":sum(r["pass"] for r in accepted),
        "unrequested_write_proposals":sum(r["unexpected_write"] for r in rows),
        "proposed_writes":sum(r["decision"]["effect"]=="write_proposal" for r in rows),
        "reasons":dict(Counter(r["decision"]["reason"] for r in rows)),"registration_sha256":digest(args.registration),
        "policy_sha256":digest(args.policy),"limitations":"Read queries copy the full request. Term coverage is mechanical, not retrieval relevance. No answer/real-execution qualification."})
    print((args.output/"summary.json").read_text())


if __name__=="__main__":main()
