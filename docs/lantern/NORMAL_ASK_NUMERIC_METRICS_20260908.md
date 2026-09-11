# Normal Ask numeric metrics — canonical integration

All nine existing-file BASE_SHA256SUMS entries from
`/private/tmp/lantern-numeric-metrics-review-20260908` matched canonical source
before applying the reviewed patch. New metrics module and tests were included.
The only difference from that candidate is voice-loop import ordering: standard
library imports precede local imports. No frozen combined release or device changed.

The normal admitted adapter publishes validated terminal usage/timings through
VoiceResult, CLI, completed voice state and the existing observer. Six numeric
fields are allowed: prompt_tokens, cached_tokens, output_tokens,
prompt_processed, prefill_ms and generation_ms. Missing or invalid values remain
null; counts exclude booleans/fractions, and all numbers are finite, nonnegative
and JSON-safe. No request options, prompts, admission or lifecycle changes.
Cancellation and unsuccessful completion publish no successful metrics.

The observer exports no question/answer contents. First text means runtime entry
to first non-whitespace delta, not UI paint; first_published_ui_ms remains null.
Model PID/invocation checks are sampled, not atomic per-request receipts. No
performance measurements or cache-hit/speedup claims follow from these tests.

## Validation and artifacts

Run from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=software/runtime/src /private/tmp/lantern-r8-installed-validation-20260907/bin/python -B -m pytest -q -p no:cacheprovider software/runtime/tests/test_inference_guard.py software/runtime/tests/test_inference_metrics.py software/runtime/tests/test_adapters.py software/runtime/tests/test_orchestrator.py software/runtime/tests/test_config_cli.py scripts/tests/test_observe_voice_benchmark.py scripts/tests/test_unit_a_voice_loop.py
```

Result: 150 tests and 42 subtests passed; existing SDK schema-shadow warning.
Two offline builds using `scripts/build-runtime-release.py --output` with distinct
`/private/tmp/lantern-numeric-metrics-canonical-20260908/build-a` and `build-b`
directories produced byte-identical `sage_lantern_runtime-0.2.0-py3-none-any.whl`:

- Wheel SHA256: `bbc7f20f7faaa505455906e5fc270ee97129076f1f5c1caa2c2a93d23a1fc443`.
- Both source snapshots: `c0e9d61bfb9b488fa1d18a9dea2c54f6c23221fdec02ece850330c3e78353115`.
- All six conversation modules compared byte-for-byte with the prior compact
  V3 wheel in `/private/tmp/lantern-public-share-matched-20260908-v3/build-a`.
- New inference_metrics.py is present in the wheel.
- Matched voice script SHA256: `e4924af93cf042a3afa6602786978dd2f11ce96078b8a9ef113a1865fcbc6a8a`.
- Matched observer SHA256: `222b6372d9593548e14ded4e50f1115e19b1326dc5ebedc56793aa0e593e1197`.

These are current canonical-source builds, not a replacement authorization for
any installed or frozen combined release. A future reviewed runtime/V4 must pair
the wheel with the voice and observer changes; scripts alone require the new
runtime module. Parent should relay this dependency to Lovelace before packaging.

Parent independently verified both wheel bytes and the stated hash, compared
every packaged Python module with canonical source, and confirmed all six
compact conversation modules match V3. The same 150 tests and 42 subtests also
passed with the produced wheel on PYTHONPATH; the adapter import resolves inside
that wheel. Curie's independent source review found no blocking stale-metric,
privacy-export or lifecycle change. Combined-release integration and device
measurement remain required.
