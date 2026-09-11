# Unit A general-knowledge fallback

2026-09-05: The operator reported that a question about *The Matrix* was
refused because no local context was available. The previous prompt asked
for relevant local context but did not explicitly authorize pretrained
general knowledge as a fallback.

The shared streaming/non-streaming prompt now uses relevant credible SAGE
information first, then pretrained knowledge for general questions. Missing
recall is not a reason to refuse. Private/local facts must not be invented;
retrieved text is reference material, not instructions. Current facts remain
unverified without live access, and no cutoff date is fabricated.

Validation: 65 runtime tests and 3 subtests pass. Direct streaming requests
to Unit A's existing model answered the Matrix question with empty context
and with unrelated village-pump context. A private-password question did
not produce a password. These are behavioral spot checks, not factuality
or security guarantees: the film summary had imprecise plot details, and
the password answer overstated what could be known about storage.

Measured model-call first text / completion were 12.02 / 25.15 seconds
for the first request and 2.98 / 14.69 seconds with unrelated context.
These exclude speech recognition and screen rendering; they are not a
performance comparison. No audio, user history or retrieved private
records were used in these synthetic checks.

Deployment uses the existing runtime adapter path with a sibling
`adapters.py.before-knowledge-20260905` backup. A new voice runtime process
loads the updated prompt without restarting the model. Touch controls and
end-to-end screen streaming remain a separate staged deployment; this
adapter update alone does not enable them. Operator voice retest pending.
