# Conversation recall latency qualification

A now runs the qualified conversation-only prompt compactor. Across 48 fresh
synthetic requests through the actual adapter and active-history code, cached
follow-ups began paired medians 5.11 s and 5.20 s sooner in two repeats. Every
matched answer pair was byte-for-byte identical, and all twelve resets passed.

The formatter preserves every selected record and field, including correction
text, attribution, links and omission counts. It may reference exact text already
in same-role active history. Knowledge and photo request bodies stay unchanged;
no retrieval selection, storage, history limit, system policy or model setting
changes. The broader knowledge table was rejected after an over-refusal.

These are model HTTP timings, not speech-to-screen measurements. Both versions
still failed an identical initial planning question; that quality issue remains.
Unstructured long retrieval and empty-context requests receive no token reduction.

The deterministic 107-module wheel is
`cf963bd87e9a838ec926f2da80fc4aad3b0946b2ebdc1827a6228734ac0b214a`.
Installation and independent checks confirmed normal services, locked auth,
startup bindings, exact source hashes and unchanged model/native process identities.
The applied workspace passed 160 tests and 37 subtests.

[Full qualification, limitations and artifacts](../../work/model-trial-20260910/context-latency/CONVERSATION-ONLY-RESULTS.md)
