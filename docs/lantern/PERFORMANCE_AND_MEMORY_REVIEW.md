# Unit A performance and memory — review sidecar

**Date / primary sources accessed:** 2026-09-05.  
**Status:** Historical review with a later measured implementation checkpoint. The original sidecar did not perform live operations; later parent-owned evidence supersedes its old no-projector/no-streaming descriptions.

## Latest implementation checkpoint — 2026-09-05

See [UX_VISION_ACCEPTANCE.md](UX_VISION_ACCEPTANCE.md) for three fixed synthetic
voice runs, real signed recall and photo inference, timing boundaries and
remaining physical acceptance. First runtime text was 15.37/5.31/5.17s for the
Matrix question; a 256×192 red photo question took 49.86s wall. Camera first
JPEG delivery was 2798/944/2856ms after 3/0.5/3s idle. These are small samples,
not a before/after speedup or browser paint measurements.

The optional matching projector, grouped streaming and short-answer guidance
are now deployed. Resident STT remains deferred pending a capacity-controlled
experiment; measured STT ~2.5s is smaller than cold/photo prefill. History remains
explicit opt-in text storage, not SAGE writes. No automatic memory/publication
behavior was added. The historical hypotheses below remain useful but are not
a current feature-status checklist.

## 1. Measured baseline and what it means

| Observation | Reported measurement | Interpretation / limit |
|---|---|---|
| One voice session | STT **2.326 s**, SAGE context **0.010 s**, LLM **3.315 s**; approximately **5.65 s** total | Single-session baseline supplied by the operator; not a distribution, SLA or guaranteed latency. Rounded stages sum to 5.651 s. |
| Camera preview | First three decoded frames at **3.51, 3.71, 3.91 s** from request start | Startup dominates this observation; subsequent 0.20 s intervals are consistent with configured **5 fps**. Not proof of sustained throughput or touchscreen paint latency. |
| Memory | **7,935 MB total**, **3,968 MB available at idle** | Operator measurement, not a reserved workload budget. Resident STT, projector and browser buffers must share this capacity. |

Camera evidence is recorded in [Unit A acceptance evidence](UNIT_A_ACCEPTANCE_EVIDENCE.md). Existing [vision spike](LOCAL_VISION_SPIKE.md) requires a build/model check and forbids concurrent text and vision model servers on this baseline.

### Parent-reported follow-up measurements

- Installed llama help advertises `--mmproj`, `--no-mmproj-offload`, `--image-min-tokens`, `--image-max-tokens`, and prompt caching enabled by default. This confirms advertised options, **not a loaded projector, successful image inference or verified cache isolation**. No projector is loaded. Do not propose simply enabling an already-enabled cache as a new optimization.
- Alternating camera trials, three per mode with **0.5-second gaps**: reported 720p timings **[3126, 1282, 1276] ms**; mode labelled “640p” **[1327, 1527, 1330] ms**. Record exact negotiated width/height and identical timing boundaries before reproducing; “640p” is not a complete mode specification.
- **No lower-resolution speedup is demonstrated.** The first 720p trial is substantially slower and cold-start/order effects confound aggregate comparisons. Remaining 720p trials are faster than the reported lower-resolution samples; that also does not establish a general resolution advantage from this small sequence.
- USB device `4-1.2` reports `power/control=auto`, `runtime_status=suspended`, and `autosuspend_delay_ms=2000`. Runtime suspend/resume is a plausible startup contributor, not a proven cause. The 0.5-second inter-trial gap is shorter than the nominal idle delay, but gap alone does not establish actual device power state.
- Parent's controlled-cold 720p follow-up reports two total timings per setting: **5 fps [3102, 3331] ms; 15 fps [2753, 2747] ms; 30 fps [2704, 2661] ms**, using **3-second idle gaps** with the **2-second autosuspend setting**. These samples favor 15/30 fps over 5 fps for the measured boundary, not a general latency guarantee or a resolution speedup. Three seconds permits autosuspend but does not by itself prove each pre-open runtime state; retain state, order, lighting and timing-boundary evidence.
- Parent selected **720p / 15 fps** as a compromise; **USB power mode is unchanged**. Actual sustained load/quality and browser paint timing remain separate checks. No further camera change is proposed by this sidecar.

These findings were supplied by the parent; this sidecar did not probe hardware or run the camera benchmark.

The current `VoiceOrchestrator.run_audio()` sums timed STT, context, LLM and optional TTS stages. Its `total` **excludes** physical recording duration, release handling, runtime subprocess startup, adapter construction, state-file publication and UI polling/rendering. Instrument release-to-first-visible-answer and release-to-final-answer separately; do not present stage-sum time as complete end-to-end latency. Current speech-to-screen use has no TTS stage.

Source: `software/runtime/src/sage_lantern/orchestrator.py:38`; `scripts/lantern/unit-a-voice-loop.py:99`.

## 2. Ranked hypotheses — no speedup promised

| Rank | Candidate / source evidence | Small experiment and constraints |
|---|---|---|
| 1 | **Resident Whisper:** `WhisperCppSTT.transcribe()` starts a fresh CLI with `-m` for every request; the voice loop also starts a fresh runtime process. This repeats process/model initialization, although warm OS file cache may reduce disk I/O. | Measure CLI initialization versus feature extraction/decode first. Compare the same audio/model/threads with a single resident worker or supported whisper-server. Retention may remove repeated initialization, not the whole 2.326 s STT stage. Budget idle/peak RAM alongside vision; clear previous-audio context between sessions. |
| 2 | **Stream first visible tokens:** `LlamaCppHTTP.respond()` requests a complete JSON response; orchestrator returns only the completed result and the outer loop buffers subprocess stdout. | Streaming must traverse adapter, orchestrator, IPC/state and UI; `stream: true` alone is insufficient. Measure first useful visible text and final completion separately. Preserve partial/final/error distinction, cancellation, session correlation and stable scrolling. Streaming may improve perceived responsiveness without shortening actual total time. |
| 3 | **Bound context/output before cache tuning:** four recalled records are concatenated without a byte/token budget; the request has no explicit output-token limit. Thinking is requested off already. | Set reviewed input/output budgets with measured tokenizer counts, reserving room for image tokens when applicable. Test answer completeness and truncation behavior; retain source identity/provenance. Do not assume the installed template honors thinking settings. Investigate same-session prompt-prefix reuse only after measurement. |
| 4 | **Profile camera opening rather than raise fps:** preview separately configures MJPG/5 fps, then launches a streaming subprocess. | Timestamp lock wait, device configuration, process startup, first JPEG boundaries, decode, HTTP delivery and browser paint. The still path skips 15 frames; preview does not, so do not attribute preview's 3.51 s delay to that skip. Evaluate exact supported modes/exposure behavior only after profiling. Do not leave capture running to hide startup cost or raise USB/CPU load blindly. |

Relevant locations: `software/runtime/src/sage_lantern/adapters.py:27`, `software/runtime/src/sage_lantern/adapters.py:63`, `software/runtime/src/sage_lantern/adapters.py:122`, `scripts/lantern/unit-a-camera-server.py:93`, `scripts/lantern/unit-a-camera-server.py:132`.

**Deprioritize recall optimization:** the observed 10 ms includes embedding plus hybrid retrieval and is not the dominant stage in this run. Avoid weakening ACLs/freshness or caching private results merely to optimize it. Thread count, language selection and decoding settings are controlled follow-up variables, not free speed improvements; compare transcription quality and thermal behavior.

### Upstream options, installed-version gate

- [whisper.cpp server documentation](https://github.com/ggml-org/whisper.cpp/tree/master/examples/server) describes WAV `/inference`, thread/language controls, `--no-context` and CPU selection. [Server source](https://github.com/ggml-org/whisper.cpp/blob/master/examples/server/server.cpp) is the reference for model/context lifetime. A future resident service needs loopback-only access, bounded input/concurrency, least privilege and no user-controlled model loading; do not expose the example's unrestricted management surface.
- [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) documents streaming, output limits and prompt/KV reuse. Exact parameters and endpoint behavior must be checked against the installed build. KV reuse is an inference optimization, not conversation history or persistent SAGE memory; never treat a server slot as a user identity.
- Local stack documentation records Whisper **1.8.6** and llama.cpp **b9637**. Parent has now confirmed the advertised llama options listed above; exact running binary/version, active service flags and successful multimodal loading still need provenance checks. Whisper's installed server capability remains unverified. No blind upgrade, copied GPU flags or assumed RK NPU/Mali acceleration.

**Cache isolation:** authorize retrieval on each request. Key any future application cache by authenticated user/private scope, session, model/template and source revision; invalidate on correction, deletion, ACL change and user switch. Never reuse private prompts or results across shared-unit users or personal/public planes. Verify slot/cache reset semantics before enabling persistent reuse.

## 3. What the current voice path remembers

- **SAGE recall only:** `SageSDKContext.recall()` calls `embed()` and `hybrid(..., status_filter="committed")`, returning record content. The inspected voice path contains no SAGE memory-submit/write operation. Speaking to Lantern does not currently save that conversation to SAGE. This does not establish what server-side request logging retains.
- **Raw audio is temporary by intent, not never written:** capture defaults to `/var/lib/sage-lantern/audio/input-<session>.wav`; the voice loop unlinks it in `finally` after success/error. Whisper's temporary transcript directory is also cleaned by its context manager. SIGKILL, power loss, an orphaned descendant or separate debug capture can leave artifacts; unlink is not secure erasure. Check permissions, crash cleanup and retention rather than claim “nothing stored.”
- **Latest conversation remains locally visible:** `/run/sage-lantern-voice/state.json` is atomically replaced, mode `0640`; a completed session contains transcript, response and timings until overwritten/restarted. It is not a conversation archive. Verify actual ownership, status-API exposure and `/run` lifecycle; a shared screen can expose the last user's answer.
- **JSONL is metadata, not history:** `JsonlEventLog` writes session ID, previous/current state, monotonic timestamp and detail. This caller supplies lifecycle labels or exception class names, not transcript/answer fields. Example configuration places it under `/var/lib/sage-lantern/logs/state.jsonl`; rotation/retention and actual deployed path need checking. The outer state error can contain subprocess stderr; sanitize it for private-content leakage.

Sources: `software/runtime/src/sage_lantern/state.py:53`; `scripts/lantern/unit-a-voice-loop.py:29`, `:115`; `software/config/lantern.example.toml:2`.

## 4. Proposed private history / episodic memory policy

**Separate optional history from model optimization.** Default remains no durable conversation write. Offer a clear private-history opt-in and a separate “Remember this” confirmation for a minimal user-approved fact/preference or episode. A model answer must never automatically become a verified fact.

Proposed minimal record: authenticated owner/private scope, session ID, event time, user-approved summary, provenance/type (user statement versus generated suggestion), retention choice and correction lineage. Raw audio/photos are excluded by default; full transcripts/answers require a distinct explicit choice. A remembered episode can say the user asked about a topic without asserting the answer was true.

If implemented later, enqueue only after consent through a **bounded asynchronous queue**, outside answer latency: item/byte/age limits, bounded retries/backoff, cancellation, and deduplication by owner/session/action ID. Revalidate authorization on write; ambiguous submission outcomes require reconciliation, not unlimited replay. “Queued” is not “saved.” Queue contents need private access control/encryption policy and must not reuse the public-proposal queue.

Shared-unit handling needs an explicit authenticated owner/session boundary, end-session clearing of screen/history/cache, and denial of access to another user's records. Possession of the device or a Fleet role is not permission to personal memory. Do not enable automatic storage where ownership is ambiguous.

Correction/deletion must address pending queue items, retrieval results/caches, retained history, backups and any canonical store. **Installed SAGE write, ACL, idempotency, correction/deletion and retention capabilities are unverified here**; do not promise physical erasure or append-only-chain rewriting. Confirm supported semantics with the SAGE owner before UI promises or integration. No automatic public publication, federation export or fleet telemetry of private conversations.

## 5. Bounded follow-up order

1. Complete the parent's vision safety/build/memory gate; do not add another resident workload in parallel without a measured budget.
2. Instrument stage boundaries and establish repeated cold/warm measurements with fixed audio/question/context and recorded output length. Report samples/range; one run cannot establish p95.
3. Change one variable at a time: resident STT, then streaming, then token budgets/cache, with quality/privacy/voice recovery checks and rollback. Profile camera startup independently.
4. Keep private-history design a separate consent/authority feature. No writes or model-response memorization are authorized by this review.

**Deliverable scope:** this document only. No optimization was implemented or tested; all performance gains and SAGE history integration remain hypotheses.
