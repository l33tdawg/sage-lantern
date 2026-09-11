# Reviewed CLI promotion

Lovelace accepted revision 2 with 268 independent tests. The only production
change in revision 3 is the requested CLI predicate:

```
require(not args.apply or (args.approve_microphone_capture and 1 <= args.wait_owner_seconds <= 300))
```

Default dispatch remains read-only. Unapproved apply and approved apply with
wait outside 1–300 refuse before loading any target dependency. CLI regression
tests exercise both default and approved dispatch, without actual mutation.
The revision 2 review document is historical where it says all apply is blocked.
Availability of this dispatch is not permission to capture: fresh owner unlock
and confirmation of the one-capture approval scope remain operational gates.

Revision 3 is frozen separately under
`/private/tmp/lantern-runtime-maintenance-review-20260908-v3`; revision 2 is
unchanged. Reproduce with `sh review-tests.sh`. Full suite: 272 passed, one
existing SDK warning. No direct agent messaging tool is available; the parent
must relay the exact promotion hash for final tiny-diff review.

Read-only A prerequisite observation on 2026-09-08:
boot `39f90681-0b5b-4a84-bdd1-0298deefba28`, auth locked and active PID 1294,
startup active PID 1304, model/broker/voice inactive PID 0. Maintenance attempt
and sealed environment roots absent. This is not a successful maintenance
preflight: the current route needs a settled active lifetime and model,
fresh access/signed inventory, reviewed expected pins, and a sealed independent
environment. No service start/stop, capture, or maintenance apply is authorized
by staging the candidate. Do not race or replace the active startup owner.
