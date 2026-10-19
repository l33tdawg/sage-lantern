#!/usr/bin/env python3
"""Fixed local study stages. Choose on calibration, then run fresh confirmation."""
import json
import subprocess
import sys
from pathlib import Path
from run import digest,write


def main():
    root=Path("work/functiongemma-20261002").resolve()
    here=Path(__file__).resolve().parent
    study=root/"study-v2"
    study.mkdir(exist_ok=False)
    frozen=root/"evaluation-v2b"
    model=root/"models/functiongemma-bf16"
    variants=("qv8","qkvo16")
    write(study/"registration.json",{"variants":list(variants),
        "selection":"lexicographic: fewer raw unrequested-write attempts, then more tool-contract passes, then more intent-gate correct; calibration only",
        "calibration_manifest_sha256":digest(frozen/"calibration/manifest.json"),
        "confirmation_manifest_sha256":digest(frozen/"confirmation/manifest.json"),
        "training_manifest_sha256":digest(root/"training-v2/manifest.json"),
        "baseline":"previous fixed v1 LoRA; same fresh confirmation requests",
        "sources":{p.name:digest(p) for p in sorted(here.glob("*.py"))},
        "scope":"Synthetic proposal/routing study. No live execution or deployment."})
    src=study/"source";src.mkdir()
    for p in here.glob("*.py"):
        (src/p.name).write_bytes(p.read_bytes())
    def run(script,*args):
        print("STAGE",script,*map(str,args),flush=True)
        subprocess.run([sys.executable,str(here/script),*map(str,args)],check=True)
    candidates=[]
    for name in variants:
        adapter=root/("adapter-v2-"+name)
        if name=="qkvo16":
            run("train.py","--data",root/"training-v2"/name,"--model",model,"--output",adapter)
        elif not (adapter/"complete.json").is_file():
            raise ValueError("first_adapter_must_finish_before_study")
        tool=study/(name+"-calibration-tools")
        gate=study/(name+"-calibration-gate")
        run("run.py","--freeze",frozen/"calibration","--model",model,"--adapter",adapter,"--backend","functiongemma","--output",tool)
        run("run_gate.py","--freeze",frozen/"calibration","--model",model,"--adapter",adapter,"--output",gate)
        scores=json.loads((tool/"scores.json").read_text())
        summary=json.loads((gate/"summary.json").read_text())
        candidates.append({"name":name,"attempted_unrequested_writes":sum(r["attempted_unrequested_write"] for r in scores),
                           "tool_passed":sum(r["pass"] for r in scores),"gate_correct":summary["correct"]})
    chosen=min(candidates,key=lambda c:(c["attempted_unrequested_writes"],-c["tool_passed"],-c["gate_correct"]))
    write(study/"selection.json",{"chosen":chosen,"all":candidates,"confirmation_predictions_seen":False})
    adapter=root/("adapter-v2-"+chosen["name"])
    policy=study/"gate-policy.json"
    run("calibrate_gate.py","--calibration",study/(chosen["name"]+"-calibration-gate"),"--output",policy)
    run("evaluate_router.py","--freeze",frozen/"calibration","--gate",study/(chosen["name"]+"-calibration-gate"),
        "--tools",study/(chosen["name"]+"-calibration-tools"),"--policy",policy,"--output",study/"calibration-router")
    for name,path,backend,adapt in [("selected",model,"functiongemma",adapter),
        ("previous-v1",model,"functiongemma",root/"adapter-v1"),
        ("qwen",Path("/Users/l33tdawg/ai-workspaces/lantern-training/exports/lantern-native5-2b-mlx"),"qwen",None)]:
        params=["--freeze",frozen/"confirmation","--model",path,"--backend",backend,"--output",study/(name+"-confirmation-tools")]
        if adapt:
            params.extend(["--adapter",adapt])
        run("run.py",*params)
    run("run_gate.py","--freeze",frozen/"confirmation","--model",model,"--adapter",adapter,"--output",study/"selected-confirmation-gate")
    run("evaluate_router.py","--freeze",frozen/"confirmation","--gate",study/"selected-confirmation-gate",
        "--tools",study/"selected-confirmation-tools","--policy",policy,"--output",study/"confirmation-router")
    write(study/"complete.json",{"chosen":chosen,"router":json.loads((study/"confirmation-router/summary.json").read_text()),
        "tools":{name:json.loads((study/(name+"-confirmation-tools")/"summary.json").read_text()) for name in ("selected","previous-v1","qwen")}})
    print("STUDY COMPLETE",study,flush=True)


if __name__=="__main__":main()
