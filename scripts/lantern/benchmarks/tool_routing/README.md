# FunctionGemma action specialist and model router study

This directory contains local experiments, synthetic data builders, strict tool
decoders, and a candidate controller for Lantern. The deployed Lantern answer
path does not import this directory. Action candidates remain disabled and no
experiment changes a device service. `auth_preflight.py` is an explicit read-only
exception: it uses an existing pinned signer for live SAGE profile/domain GETs
and authentication rejection checks. It cannot retrieve or save memories, issue
a Lantern owner session, attest private-node policy or activate actions.

The original comparison and fixed small LoRA are recorded in
`work/functiongemma-20261002/STUDY.json`. The expanded comparison is recorded in
`work/functiongemma-20261002/study-v2/`. Model weights, adapters, frozen requests,
source snapshots, registrations, predictions, and receipts stay under ignored
`work/`. They are local evidence, not committed production assets.

The consolidated follow-up is `work/functiongemma-20261002/OPTIMIZATION_STUDY.json`.
Four new training configurations were run. On the same new 76 requests, the
previous classifier head scored 59 and the contrast-trained head scored 68
(89.5%), with roughly 17 ms median for two description orders. Reloading the
saved classifier reproduced every logit exactly. This is classification, not
complete action success: its calibrated controller passed 56/76 action contracts,
accepted 19 plans of which 17 were correct, and admitted no saves. Two accepted
status reads were wrong. The models and controller remain unqualified.

The responsiveness pass is recorded in `work/responsiveness-20261002/RESULT.json`
and its adjacent report. Local compiled inference reduced median two-order
classifier latency from 18.2 ms to 13.4 ms with identical logits and controller
decisions across three replays of the same 76 inspected requests. Batch-two
inference reached 9.4 ms with the same chosen actions, but changed probabilities
by up to 0.031; it needs separate calibration before use. Accuracy stays 68/76.
Static-prefix caching was less useful on this host. The Qwen answer cache trial
preserved all generated tokens across 24 pairs but gave no persuasive TTFT gain,
and preserved incorrect Malay answers. Parity does not establish answer quality.

`bench_latency.py`, `bench_answer_latency.py`, `bench_stream_proxy.py` and
`bench_publication.py` measure these boundaries separately. The proxy and
publication replays also cover two repository fixes: forwarding photo SSE before
upstream completion and reducing pending-text publication delay. Those transport
results preserve exact response bytes; they are not live device latency results.

The native tool interface scored 50/76 for the previous small LoRA and 56/76 for
the selected expanded LoRA on a separate shared set. Fused BF16 scored 57, 8-bit
MLX scored 56 with 285 MB of weights and 0.47 GiB peak MLX memory, and 4-bit scored
38 with 151 MB of weights. These results support further 8-bit investigation and
reject the tested 4-bit candidate. The existing Qwen answer model also failed to
qualify as a one-token fallback judge (59/76 calibration, 33 order disagreements).
Increasing adapter capacity and selecting a larger model did not reliably help.

## What we took from Jev and Hunch

[Jev's API](https://api.typesafe.ai/redoc) accepts structured state and typed
questions. The useful design is to split a workflow into narrow judgments and
let code combine them. Type safety restricts possible outputs; it does not prove
that a semantic decision is correct.

[Hunch](https://github.com/ihubanov/hunch/tree/27407dc7561071f8a2bcf1983e5b50603bc6deb2)
reads probabilities from constrained label-token logprobs. It supports
classification, binary judgments and scales, option-order checks, and model
qualification using near-miss cases and calibration. It uses vLLM; it is not a
drop-in implementation of a Lantern router. We inspected the source without
installing or running the package.

Our candidate applies those ideas locally: a five-option intent judgment, then
a deterministic controller. The specialist proposes private-memory reads,
literal private-note writes, current-device status reads, or parallel reads.
The general answer model handles explanation and clarification. Image requests
go to the vision lane. Availability and context limits also affect the route.

## Measurement boundaries

- `run.py` measures native tool selection and argument fidelity. Its executor is
  an in-memory fake. Abstaining on an action case is a failure.
- `run_gate.py` reads the full next-token distribution using MLX. It verifies
  that each label is one token, evaluates both option-label mappings, records
  probability mass on the allowed labels before normalization, and records
  disagreement. It does not generate a paragraph or use self-reported confidence.
- `calibrate_gate.py` selects a temperature by calibration NLL, then fits
  conservative per-intent thresholds. Saving has a higher minimum threshold.
  Zero observed calibration errors is not a correctness guarantee.
- `evaluate_router.py` replays frozen predictions through the controller. It
  reports complete action-contract pass count, accepted-plan precision, answer
  handoffs, rejected plans, and unrequested write proposals separately. A handoff
  does not count as completing a requested action. Final answer quality and
  successful real execution are outside this measurement.
- `literal_note.py` copies fields from a few fully matched, explicit note-command
  formats after the gate and generated plan agree on a save. It preserves exact
  punctuation. It cannot authorize a save, invent missing fields, repair a
  rejected plan, or override an answer intent.
- `bounded_actions.py` is a second controller variant registered before fresh
  predictions. It constructs status/read proposals directly from the accepted
  intent and original request, without generating tool arguments. Saves require
  a recognized literal format. Its search-term score is mechanical; real
  retrieval relevance remains unmeasured.

The gate is calibrated for its adapter. `router.py` validates model-selected
plans against the facade before admitting a proposal. Models cannot select an
owner, token, endpoint, or SAGE domain. Real execution still requires the
existing authenticated device/owner boundary and a bound receipt. A generated
success message cannot substitute for that receipt.

The experiment uses authored English, Malay, and Arabic requests. The expanded
training set has 1,904 unique requests and 3,808 action/classifier rows. Two
predeclared variants use the same data: rank-8 q/v LoRA for two epochs and
rank-16 q/k/v/o LoRA for three epochs. This compares candidate configurations;
it does not isolate the causal effect of rank, projections, or epochs.

Calibration and confirmation each contain 76 requests. They have no exact
question overlap with each other or training. The first unused freeze had two
overlapping selective-negation requests; `evaluation-v2b` corrected that before
any evaluation predictions. Previously inspected 60- and 32-case sets are
regression material, not untouched acceptance sets. All authored sets share
one author and a narrow facade, so performance can overstate real-world quality.

## Running the study

Use a Python environment containing the pinned MLX/MLX-LM/Transformers versions
recorded in each registration. The local study used the training workspace's
existing virtual environment; it did not change that environment or its models.
Metal inference/training needs access to the Mac GPU.

```sh
python build_specialist.py --output <training-dir> \
  --inspected <old-60-freeze> --inspected <old-32-freeze>
python build_confirmation.py --output <evaluation-dir> \
  --training <training-dir>/train.jsonl
python train.py --data <training-dir>/qv8 --model <base-model> --output <adapter>
python run.py --freeze <evaluation-dir>/calibration --model <base-model> \
  --adapter <adapter> --backend functiongemma --output <tools-output>
python run_gate.py --freeze <evaluation-dir>/calibration --model <base-model> \
  --adapter <adapter> --output <gate-output>
python calibrate_gate.py --calibration <gate-output> --output <policy.json>
```

`experiment_v2.py` records candidate selection before calibration inference and
runs the complete fixed comparison. It expects the first adapter to be complete
and starts the second training job itself. Its paths describe this particular
local study. Output directories cannot be reused or overwritten.

`quantize_specialist.py` merges a local adapter and exports 4- and 8-bit MLX
weight-only candidates with group size 64. These are robustness experiments,
not Rockchip W8A8 artifacts. Replaying an already inspected confirmation set
after quantization measures sensitivity to the backend change; it is not a new
untouched acceptance result.

## RK3588 integration investigation

The current `software/npu/rkllm_engine.cpp` bridge emits generated text and
performance counters. It does not expose logits to the HTTP server. Rockchip's
[public header](https://github.com/airockchip/rknn-llm/blob/f7390530443bf84f0394255a449d7cbe81e69d1c/rkllm-runtime/Linux/librkllm_api/include/rkllm.h)
provides `RKLLM_INFER_GET_LOGITS` and a logits result structure. The local
September SDK checkout also contains these interfaces.

`rkllm_intent_probe.cpp` is an isolated qualification probe. It reads a fully
rendered prompt, disables automatic chat wrapping, extracts the last position's
five logits, computes full-vocabulary label mass, and rejects invalid dimensions,
nonfinite values, duplicate callbacks, and wrong token IDs. It releases the SDK
output buffer after copying. It does not select or execute a tool.

The probe passed host syntax checks against both pinned headers and six native
boundary tests using a fake SDK. This proves neither on-board ABI compatibility
nor semantic fidelity. Before service integration, qualify the exact installed
runtime, tokenizer IDs, function control tokens, fused/exported weights,
quantized logits, latency, memory, power, and thermal behavior on the RK3588.

Next experiments should separate classifier and argument-generator training,
balance loss per example, give each request counterfactual label-order examples,
broaden native-language phrases and selective negation, and collect authorized
realistic speech transcripts. Compare their coverage and complete actions on a
new freeze; do not improve a headline score by counting every fallback as success.

## Dedicated classifier-head trial

`head_trial.py` tests a different interface on the same 270M backbone: the last
valid prefix hidden state feeds a learned five-class linear head. The language
model's vocabulary projection is bypassed. Class probabilities come from the
five-class logits, rather than letter-token probabilities. This is an additional
architecture candidate, not a claim that the native FunctionGemma tool format
became more accurate.

The trial was registered before any confirmation outputs were inspected. It
uses only the same 1,904 training requests, both description orders for every
request, an inverse-frequency class loss averaged per example, and a rank-16
q/k/v/o adapter for three epochs. Temperature and thresholds are selected from
calibration before its confirmation inference. Its source, weights, configuration,
predictions, and controller replay are saved under `head-trial/`.

The saved trainable parameters include the transformer adapter and the linear
head. `load_classifier` reconstructs them on the checked base weights. A future
Rockchip implementation would need to qualify last-hidden-state extraction and
the head computation; the current vocabulary-logit probe does not implement that
path. Implicit personal recall, real speech, dialogue history, broader languages,
retrieval relevance, completed answers, and authenticated execution still need
separate evaluation.

`build_contrasts.py` adds boundary pairs without copying inspected evaluation
questions into training. Its 3,031 requests were used for the later two-epoch
head trial with a new calibration/confirmation freeze. This changes both data
and training duration, so the difference is a candidate comparison rather than
an isolated estimate of the data's causal effect. Some negative-case language
annotations in this freeze default to English despite Malay/Arabic text; overall
scores are unaffected and no language subgroup accuracy is claimed.
