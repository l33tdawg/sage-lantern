# Unit A separate maintenance environment

Completed only the reviewed sealed-environment preparation, while A remained
locked. No maintenance driver apply, capture, service action, installed runtime
replacement, or new-runtime import into an active service occurred.

- Boot: `39f90681-0b5b-4a84-bdd1-0298deefba28`.
- Builder SHA: `05b496ff69d4017faef914816028ecd9b61a10b421f9cfc0638937c1e325454f`.
- System Python SHA: `304aa87a76ebb13fd22d253ac157f14980ff2cdb23e6274f3b045571405e07dc`.
- Expected SHA: `d92e2cac79a1df502b2aedd4961cf2ce1144a0d8d5f471d85456687763c336b7`.
- Inputs: `/var/lib/sage-lantern-maintenance-inputs-20260908-v1`.
- Environment: `/var/lib/sage-lantern-maintenance-envs/d17a1920-10bf-4d59-a70e-80266ee6bdab/venv`.
- Completion receipt SHA: `92d8ea675ef956db8c6e951c658e0054586d80ebd25d28ce9cefcd4a6f3b5158`.
- Inventory SHA: `a294aa27dfc16fe286bad655042d6e7479dc351c1060cf2957963a9083e82166`.

Read-only inspection passed before the one-shot environment build. The builder
installed 18 exact offline wheels without dependency resolution, ran pip check,
and sealed 2,440 inventory entries. A separate process revalidated the entire
sealed inventory and completion receipt afterward. The environment contains
R7, not the retrieval candidate. Dependencies were verified against the frozen
R6 canonical manifest; the runtime wheel is pinned to R7 SHA
`6a8e99c7e62ba5b767e1a23258fa25d97567364d08bddeccbabbfc3053754440`.
The independent installed-runtime verifier also confirmed A's installed bytes
still match R7. The maintenance attempt directory remains absent.

Final read-only observation: auth locked, startup PID 1304 active with no
current-boot startup attempt journal, and model/broker/voice inactive PID 0.
This matches the coordinator's pre-attempt unlock wait, not proof of completed
recovery. Let the existing startup owner handle unlock; do not start producers
directly or race it with maintenance. Verify its successful exit, new durable
lifetime, active producers and operational readiness before collecting the
maintenance expected pins.

Owner steps: unlock A, allow existing startup to finish, and explicitly confirm
the one two-second local non-retained capture for maintenance. Maintenance will
later restart auth locked and require another owner unlock within its reviewed
bounded wait. A fresh pre-change benchmark, if desired, must be measured on this
boot after startup and model warm-up. The prior boot's zero-run observer is not
a matched baseline and has not been restarted.
