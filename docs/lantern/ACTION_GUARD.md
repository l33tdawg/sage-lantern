# Lantern action guard and dispatcher boundary

**Status:** implemented and installed in Unit C's production runtime venv, 18
October 2026. The product UI/HTTP route that will call it is not yet wired.

The 3B model produces text. It does not own memory values, metadata, destination,
owner, execution or success. The runtime boundary is:

1. `canonical_save()` accepts a structured user request and trusted routing.
2. It builds the only permitted `sage_remember` call from request fields.
   Missing content, an unselected public destination, conflicting values, an
   invalid type or an ungranted private owner returns a clarification and no
   call.
3. `ActionDispatcher.dispatch_save()` ignores model-authored arguments and invokes
   the trusted executor exactly once with that canonical call.
4. `SageRememberExecutor` binds the current SAGE client, default owner domain,
   classification, provider and admission check, then calls the real
   `embed`/`propose` boundary and returns the receipt unchanged.
5. An executor exception or non-mapping result becomes `uncertain`; the
   dispatcher never retries or turns it into a success message.

`ConversationService.dispatch_save()` is the runtime entry point. It runs inside
the conversation service's admission/activity boundary and uses the same
admission-checked SAGE client as conversation memory.

A cross-owner grant can be recognized by `canonical_save()`, but the dispatcher
refuses to execute it until an owner-bound executor is supplied. The current
`SageRememberExecutor` is bound to the current owner only.

## Tests

```sh
cd software/runtime
PYTHONPATH=src ../../.venv/bin/python -m pytest tests/test_action_guard.py -q
```

The focused suite covers canonical private/public saves, missing and conflicting
values, unselected destinations, other-owner refusal, owner-bound execution,
admission refusal, malformed receipts, malformed requests, executor exceptions,
no automatic retry, and locked-device behavior through `ConversationService`.

The built runtime wheel (`1e4b8184…`) was staged and imported on Unit C before
installing it into `/opt/sage-lantern/venv`; the package-level verification
passed with `action_guard.py` SHA-256 `f844e2ed…`. The pre-install package tree
is retained at
`/var/tmp/lantern-focused-finetune-20261012/runtime-pre-action-guard-20261018`.

The action guard is not an authorization boundary by itself. SAGE identity,
domain grants, owner session, admission and receipts remain the authority.
