#!/usr/bin/env python3
"""Consolidate evidence without conflating classification and action completion."""
import datetime as dt
import json
from pathlib import Path
from run import digest,write


def main():
    root=Path("work/functiongemma-20261002").resolve()
    def read(path):return json.loads((root/path).read_text())
    def arm(path):
        d=read(path+"/summary.json")
        scores=read(path+"/scores.json")
        return {"path":path,"scores_sha256":digest(root/path/"scores.json"),"summary":d,
                "failed_ids":[r["id"] for r in scores if not r.get("pass",r.get("correct",False))]}
    report={"created_at":dt.datetime.now(dt.timezone.utc).isoformat(),"scope":"Local synthetic Mac MLX experiments; unqualified for production",
        "research_sources":{"hunch":{"url":"https://github.com/ihubanov/hunch","revision":"27407dc7561071f8a2bcf1983e5b50603bc6deb2"},
            "jev":"https://api.typesafe.ai/redoc","rkllm_header_revision":"f7390530443bf84f0394255a449d7cbe81e69d1c"},
        "native_tools_v2_same_requests":{n:arm("study-v2/"+n+"-confirmation-tools") for n in ["previous-v1","selected","qwen"]},
        "native_tool_configuration_selection":read("study-v2/selection.json"),
        "native_tool_router":read("study-v2/confirmation-router/summary.json"),
        "bounded_token_router":read("study-v2/confirmation-bounded-router/summary.json"),
        "head_v2":read("head-trial/complete.json"),
        "head_v3_same_new_requests":{"previous":arm("contrast-v3/previous-head-confirmation"),
            "contrast":arm("contrast-v3/reloaded-head-confirmation"),"controller":read("contrast-v3/head/confirmation-router/summary.json"),
            "reload":read("contrast-v3/reload-verification.json"),"training":read("contrast-v3/head/training-complete.json")},
        "qwen_fallback_judge_calibration":read("contrast-v3/qwen-calibration-gate/summary.json"),
        "quantization_same_inspected_requests":{n:arm("quantized-v2/"+n+"-confirmation") for n in ["fused","q4","q8"]},
        "verification":read("verification-v2.json"),
        "limits":["Classification is not generated argument fidelity, retrieval relevance, answer correctness, or completed execution.",
            "All corpora are authored synthetic examples with many shared templates and one author; exact question separation is not distribution independence.",
            "The old 60/32 and v2 76-case sets have been inspected. Future tuning needs new confirmation and realistic authorized speech/dialogue examples.",
            "The new v3 confirmation has 76 requests; classifier is 68 correct. Conservative action controller accepts 19 plans, 17 correct, with 56/76 action-contract passes.",
            "The v3 controller proposed zero writes, including missing eight requested saves. Zero unrequested writes does not qualify saving.",
            "The controller admits two confidently wrong status reads: a camera-tripod lookup and a request to disable networking. Calibration is not authorization.",
            "Qwen fallback classification is unqualified: 59/76 calibration and 33 option-order disagreements.",
            "8-bit equality of aggregate pass counts does not imply identical outputs. Weight-only MLX quantization is not RKLLM W8A8 fidelity.",
            "No real SAGE data, authenticated tool execution, board/service changes, NPU conversion, power or thermal acceptance occurred.",
            "Some v3 negative-case language metadata defaults to English despite Malay/Arabic text. Overall scores are unaffected; no per-language results are claimed.",
            "Raw decoder failures and representative predictions were reviewed unblinded; this is not an independent blinded semantic acceptance review."],
        "next_work":["Collect diverse, authorized realistic speech and dialogue; include implicit personal recall and missing context.",
            "Expand contrast examples for scope, settings vs telemetry, ambiguous references, quoted instructions and native-language paraphrases.",
            "Qualify each fallback capability independently and measure complete answers/actions and latency, not only the route.",
            "Bind calibration to exact base/tokenizer/adapter/prompt/backend before runtime integration.",
            "Qualify native logit or hidden-state extraction, export/quantization fidelity and device performance before enabling a production route."]}
    path=root/"OPTIMIZATION_STUDY.json"
    if path.exists():raise ValueError("study_report_already_exists")
    write(path,report)
    print(path)


if __name__=="__main__":main()
