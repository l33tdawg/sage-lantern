# Explicit static startup helper phase

Isolated source candidate only. A new canonical release must be assembled and
reviewed; this patch does not modify any frozen release or installed appliance.

V2 includes exact-five destination tests, retained review-only service/installer
assertions, explicit app-no-implicit-static-install tests and all 24 previously
missing unchanged canonical source fixtures. The three release metadata documents
use the existing stable 32 MiB metadata reader with root ownership checks; helper,
configuration and receipt reads retain the strict 2 MiB bound. A regression uses
10,004,530-byte destinations and 4,150,137-byte selection documents.

After the matched app install, invoke the existing fresh provision runner with
its required output, manifest SHA and unit ID plus:

    --phase startup-helpers --execute

This executes read-only inspection. Save and independently review its JSON plan
in a root-controlled file. Apply the same phase with:

    --execute --apply-postcommission --startup-expected /reviewed/plan.json
    --startup-expected-sha256 EXACT_SHA --startup-attempt-id NEW_UUID

Without --execute the runner only verifies release closure and prints the command.
No caller-supplied destination or executable list is accepted.

The canonical destinations table explicitly maps five manifest-pinned sources to
root-owned 0644 files in /opt/sage-lantern/libexec. This phase alone installs them.
It requires the independent completed app receipt, matching release/unit, installed
unit metadata and appliance binding. Startup must remain absent, inactive, jobless
and independently cgroup-empty. It does not claim arbitrary root processes are
excluded; trusted root must serialize appliance administration.

Installation uses nofollow/exclusive writes, fsync, a once-only root journal at
/var/lib/sage-lantern-startup-helpers and before/after release verification.
Between writes, relevant source, manifest, destination, installed identity and
service fences are rechecked without repeatedly hashing the entire bundle.
Partial failure retains all evidence/files and refuses automatic retry.

No systemctl mutation, config, key, wheel, readiness marker, lifetime or service
is installed or changed. The retained reviewed wheel is checked by the later
handoff/startup phase. Actual owner setup, commissioning and guarded first
bootstrap remain necessary before those existing phases; they then install and
enable recurring startup without starting it in the commissioning boot.
