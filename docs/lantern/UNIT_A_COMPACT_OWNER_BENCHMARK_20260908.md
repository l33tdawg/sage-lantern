# Unit A compact runtime: owner Ask measurements

Observer 14708 completed successfully with three normal owner recordings after
compact audio recovery 9693. No synthetic inference or service restart was used.

| Run | STT seconds | Context seconds | Model first text seconds | Runtime first text seconds | Pipeline total seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 2.282 | 1.879 | 35.569 | 39.731 | 49.723 |
| 2 | 2.218 | 1.467 | 27.837 | 31.522 | 35.629 |
| 3 | 2.134 | 2.141 | 27.298 | 31.574 | 34.094 |

Numeric artifact: `/private/tmp/lantern-compact-owner-benchmark-14708-complete-20260908.jsonl`.
SHA256: `a460b5bac29c6da25769112fa1fb5526b10645a47c86ed08d63e7f16bdd74e48`.

All exact-question-byte comparisons were false. The owner reports the first
recording was misheard and the second and third displayed the correct question.
Capitalization or punctuation can also change the byte comparison. Private
transcripts were not exported. Token and cache counts are unknown on the
installed compact runtime. These are not identical-work speedup measurements.

First text is the runtime's first non-whitespace model delta, not measured screen
paint. Pipeline totals exclude recording time. The model wait dominates these
observations; transcription is around two seconds. Inspect actual model options,
prompt size, cache reuse and CPU contention before choosing another optimization.

## Follow-up requirements

- Preserve this baseline and obtain token/cache metrics through the reviewed
  matched runtime deployment, not by changing the model during observation.
- Remove repeated processing headings from the footer; show elapsed waiting time
  without a fabricated completion percentage. Source change has targeted tests;
  it is not deployed or physically accepted yet.
- Provide offline predictive touch correction and edited-question resubmission,
  retaining the photo. No swipe typing is required. Confirm authenticated backend
  admission and cancellation semantics before exposing a correction action.
- These measurements do not close reboot, vision, messaging, public acceptance,
  sleep or Unit B deployment gates.
