# Vision CLI promotion

The reviewed vision bundle was accepted with 336 tests and 11 subtests. This
revision changes only the requested CLI admission predicate in production:
root remains mandatory; apply additionally requires both explicit approvals
and a 1–300 second owner-unlock wait. Default read-only dispatch is unchanged.
CLI tests cover missing approvals, invalid waits, default preflight and approved
dispatch. Earlier review documents remain historical where apply was disabled.

Snapshot: `/private/tmp/lantern-fresh-vision-review-20260908-v2`.
Run `sh review-tests.sh` within it. No vision activation or performance change
has occurred from this source promotion. The latest completed R7 baseline was
108.05 seconds total, including 84.93 seconds prefill for 1,449 tokens; vision
activation preserves existing performance parameters and is not a speed fix.
