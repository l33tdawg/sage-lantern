# Generic postcommission startup: source review snapshot

Source-only, 2026-09-08. No target staging, mutation, service start or reboot.
Historical `handoff-first-audio-lifetime.py` is unchanged (SHA256
`61d4b353797c3f56fa1405e99374ad84d71a2b6d68a107c281ad9973c670ab59`).
Independent review and per-unit live evidence are still required. No canonical
release inclusion or A/B physical acceptance is claimed.

## New entry points

- `scripts/lantern/handoff-fresh-audio-lifetime.py`:
  `108d9c4ed2fd07888d417af631ab0faa583b7d2c68adba3177182c5ad7e2439a`.
- `scripts/lantern/provision-postcommission-startup.py`:
  `9afe7be6ee6f9e355439136af30e125a3a47a57a9dcc3ef33d32cc14bb010056`.

Handoff tools use schema `sage.lantern.fresh-audio-tools.v1`, exactly:
`schema, unit_id, coordinator, adapter, helper, collector, runtime_wheel,
identity, setup`. Five executable/wheel fields are `{path, sha256}` pins.
`identity` is `{unit_id, operator_account, chain_id, agent_id}` from the unit's
own installed configuration; explicit unit-a/unit-b is mandatory, never inferred.
`setup` is `{observer, auth_process, auth_dropins, auth_source}` using the reviewed
display activator's actual authenticated setup observer (SHA256
`81142b2354e07c61962d291bd79e3d81571451d8749fd41e8d1fb4d71c3aa542`).
This invokes only its owned-listener/process-bound setup check, not display code.
Its existing process/dropin schemas and installed auth source are required.

The runner's dependencies schema is `sage.lantern.postcommission-dependencies.v1`,
exactly `schema, unit_id, handoff, tools, installer, service`; the last four are
file pins. The installer must match the existing reviewed SHA256
`d3fd22d0f3d00d2032078fbc832882197c697d495ac4f8107e3f000ad86c488f`.
Coordinator path must be `/opt/sage-lantern/libexec/startup-coordinator.py`.
No key copying, identity initialization or unverified commissioning is provided.

## Explicit phase contract

Invoke with the installed venv Python `-I -B`, supplying
`--handoff PATH --handoff-sha256 SHA --dependencies PATH
--dependencies-sha256 SHA --phase handoff|install-startup`.
Default invocation is read-only inspection. Apply additionally requires
`--apply --expected PATH --expected-sha256 SHA --attempt-id NEW-UUID`.
These are interface templates, not commands authorized for a current target.

1. Handoff requires completed setup, matching installed identity, current unlocked
   epoch, actual commissioned/active broker lifetime and model/runtime pins.
2. Exact inspected handoff applies once into fresh boot-specific directories.
   Existing historical directories are refused rather than adopted or overwritten.
3. Separately inspect install-startup: validate real handoff completion and every
   output byte, then run the existing installer's real preflight. A fresh access
   epoch is allowed only with all other handoff bindings unchanged.
4. Apply installs/enables startup for a later boot; it never starts the coordinator
   in the current boot. Verify installed bytes, enable link and inactive empty
   coordinator cgroup. Runtime/transient/shadow units refuse before installation.

The runner records exclusive intent/completion/failure evidence beneath
`/var/lib/sage-lantern-postcommission-startup/NEW-UUID`. Partial failure does not
authorize retry, rollback or claim that an enable operation never occurred.
No changes to audio, display, sleep approval, existing power policy or inhibitors.

## Tests

```sh
PYTHONPATH=software/runtime/src PYTHONDONTWRITEBYTECODE=1 \
/private/tmp/lantern-r8-installed-validation-20260907/bin/python -B -m pytest -q -p no:cacheprovider \
scripts/tests/test_handoff_fresh_audio_lifetime.py \
scripts/tests/test_provision_postcommission_startup.py \
scripts/tests/test_handoff_first_audio_lifetime.py \
scripts/tests/test_install_startup_coordinator.py
```

Result: **57 passed**, one existing SDK schema-shadowing warning. Tests cover
independent A/B identity swaps, incomplete installation receipts, unchanged
historical helper, stale/partial handoff, real generated-candidate installer
preflight (filesystem ownership and wheel verification isolated), and real
systemd output parsing with synthetic manager/cgroup state. They do not prove
actual A hardware, live setup observer compatibility or reboot acceptance.

Test SHA256s:
- `test_handoff_fresh_audio_lifetime.py`:
  `bbf5d76db8f4d337030540fde942ce324f43387f1261ba2c9f6e3123a5e8a3a3`.
- `test_provision_postcommission_startup.py`:
  `e5e7f142a288e15a702a13f9c1334c819da24db1f1ede7b41fd65a5610c57119`.
