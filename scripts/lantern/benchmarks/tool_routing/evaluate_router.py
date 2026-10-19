#!/usr/bin/env python3
"""Replay immutable predictions through a controller. Routing/action scores only."""
import argparse
import json
from pathlib import Path
from collections import Counter
from contract import score
from intent_gate import statistics
from run_gate import probabilities
from calibrate_gate import apply_temperature,metrics
from router import Router,Lane
from run import digest,write


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--freeze",type=Path,required=True)
    p.add_argument("--gate",type=Path,required=True)
    p.add_argument("--tools",type=Path,required=True)
    p.add_argument("--policy",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    policy=json.loads(args.policy.read_text())
    gate=json.loads((args.gate/"scores.json").read_text())
    tools=json.loads((args.tools/"scores.json").read_text())
    cases=[json.loads(s) for s in (args.freeze/"cases.jsonl").read_text().splitlines()]
    gate_reg=json.loads((args.gate/"registration.json").read_text())
    tool_reg=json.loads((args.tools/"registration.json").read_text())
    if gate_reg["adapter_sha256"] != tool_reg["adapter_files"]["adapters.safetensors"]:
        raise ValueError("gate_tool_adapter_mismatch")
    if any([r["id"] for r in rows]!=[c["id"] for c in cases] for rows in (gate,tools)):
        raise ValueError("replay_case_order_mismatch")
    if any(r["question"]!=c["question"] for rows in (gate,tools) for r,c in zip(rows,cases)):
        raise ValueError("replay_request_mismatch")
    router=Router(policy,[Lane("answer",frozenset({"text"}),2048),Lane("specialist",frozenset({"tool_proposal"}),2048),
                          Lane("vision",frozenset({"image"}),2048)],gate_reg["adapter_sha256"])
    calibrated=apply_temperature(gate,policy["temperature"])
    rows=[]
    for case,g,t in zip(cases,calibrated,tools):
        decision=router.route(g)
        admitted=router.admit(decision,t["calls"],parse_error=t["parse_error"],question=case["question"])
        # An answer-lane fallback does NOT count as correct on an action case.
        scored=score(case,admitted["calls"],None,t["raw"] if admitted["calls"] else "")
        rows.append({"id":case["id"],"question":case["question"],"family":case["family"],"decision":admitted,
                     "specialist_tool_pass":t["pass"],"specialist_raw_unrequested_write":t["attempted_unrequested_write"],
                     "gate_correct":g["correct"],**scored})
    write(args.output/"scores.json",rows)
    accepted=[r for r in rows if r["decision"]["calls"]]
    summary={"total":len(rows),"action_contract_pass":sum(r["pass"] for r in rows),
        "routed_specialist":sum(r["decision"]["lane"]=="specialist" for r in rows),"accepted_tool_plans":len(accepted),
        "accepted_tool_plan_correct":sum(r["pass"] for r in accepted),
        "proposed_writes":sum(r["decision"]["effect"]=="write_proposal" for r in rows),
        "literal_fields_copied":sum(r["decision"].get("literal_fields_copied",False) for r in rows),
        "literal_fields_changed":sum(r["decision"].get("literal_fields_changed",False) for r in rows),
        "unrequested_write_proposals":sum(r["unexpected_write"] for r in rows),
        "specialist_raw_unrequested_writes":sum(r["specialist_raw_unrequested_write"] for r in rows),
        "routes":dict(Counter(r["decision"]["lane"] for r in rows)),
        "reasons":dict(Counter(r["decision"]["reason"] for r in rows)),
        "gate_metrics":metrics(calibrated),"policy_sha256":digest(args.policy),
        "sources":{p.name:digest(p) for p in [Path(__file__),Path(__file__).with_name("router.py"),Path(__file__).with_name("intent_gate.py"),Path(__file__).with_name("literal_note.py")]},
        "limits":"Replay measures routing and validated action proposals. No final-answer accuracy, authorized SAGE execution, real device latency, or deployment qualification."}
    write(args.output/"summary.json",summary)
    print(json.dumps(summary,indent=2))


if __name__=="__main__":
    main()
