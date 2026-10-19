# Lantern and Lamplighter work — consolidated 28 September 2026

Historical snapshot. The [6 October 2026 task map](SAGE_TASKS_20261006.md) records the current consolidated backlog; the requirements and evidence below remain historical references.

**Definitive owner allocation:** C/D/E/F are real user Lanterns on **Orange Pi
5 Plus with dual LoRa, GPS and WiFi via the Pi**. A/B have **dashboard screens**, and each has **one LoRa chip**
and serve **Lamplighter, admin, public knowledge delivery and repeater testing**.
[Device roles](DEVICE_ROLES.md) controls current allocation.
A/B use a separate Lamplighter dashboard on both existing screens and remotely,
with SSH administration as a fallback. Personal-device display/touch acceptance belongs to C–F.
Their former endpoint UI, voice and model acceptance no longer blocks C–F.
Carrying verified public content grants no publishing, personal-Root or public
validator authority.

The assigned SAGE backlog was fully paged: 21 open tasks before consolidation,
18 afterward. Eight successors initially replaced eleven older entries; subsequent role clarifications replaced the fleet and support tasks one-for-one;
the latest 28 September screen/dashboard clarification supersedes the earlier headless interpretation.
Each successor was committed before its predecessor was dropped as superseded. Their requirements and evidence remain linked; unfinished
acceptance was not marked done. Other agents' assignments were not changed and
no coordination messages were sent.

| Workstream | Current SAGE task | Next boundary |
| --- | --- | --- |
| C–F hardware and matched release | `196bd3dc-fac9-44f2-a626-af39ce598d90` | Active: app startup/owner onboarding and hardware acceptance; NVMe/base/identity complete |
| A/B Lamplighter lab | `74faea3a-9dd8-4e83-9650-b633b0ffcd43` | WiFi seeds, screen/web dashboard and SSH admin ready; LoRa/repeater integration and managed C–F receivers remain |
| C–F model quality/performance | `9b9f43d6-07fa-4c7f-a8df-d2211640a2b8` | Active: 4B runs on C, arithmetic smoke failed; diagnose and qualify |
| C–F security/lifecycle | `abae4151-438c-48a5-88b5-54d5036e276a` | At-rest protection, lock/sleep/wake/reboot and image provenance |
| C–F desktop/touch/voice UX | `b46041c5-4c35-43b9-8db9-c9abd3bf6baf` | Portrait/landscape desktop, keyboard/mouse, actual touch, I2S and live transcription |
| C–F messaging/public sharing | `52021ca2-d295-48f1-85a9-4679bf33c865` | Authenticated delivery, truthful receipts, consent and offline public reuse |
| C–F local WiFi lane | `1f0c38a2-94d5-486a-886e-9cb217821d9f` | Acquire separate WiFi hardware; then actual interface/topology and transport |
| Curated packs/retrieval scale | `c07d1f46-c2a4-408f-9f6a-a2f0e4bc705c` | Public-domain isolation, bounded retrieval and large packs |

Maps/GNSS, trust-root rotation, Commons cache, node public-domain policy,
message-status context, White Pages, optional online research, fleet operations
and launch preparation retain distinct tasks. SAGE is the complete backlog;
this document is the technical work map.

## Evidence carried forward

C–F r3 already contains the corrected SDK, approved portrait UI, native A–F
initializer and earlier startup fixes. Five newer runtime modules differ from
r3; their exact patch and reproducible candidate wheel are preserved under
`work/cf-handoff-20260927`. These are now incorporated in the verified r4 base
candidate with the explicit Ubuntu profile; the frozen r3 export is preserved.
C r5 base and independent identity/config are installed; full app and hardware acceptance remain open.

Both A/B Heltec r7 driver experiments passed 500 contact inventories each. XIAO uses the
same old HWCDC framework family but needs its own reviewed backport, image,
version, host admission and physical tests. A Heltec image is not a XIAO image.
The failed A/B authenticated-message attempt and model qualification results
remain failures, separate from firmware and Lamplighter transport evidence.

## Test topology

A supplies the initial public pack seed. B downloads, verifies, resumes and
re-seeds it. C–F can later fetch from either, exchange verified chunks and serve
their own public cached objects. Test carrier failure, damaged content, stale
offers, quotas/priority, offline re-seeding and cancellation independently.

LoRa carries bounded discovery/offers/control and measured small payloads;
WiFi/local IP carries bulk chunks. Fast LoRa is a separately measured profile,
not assumed WiFi throughput. The initial HTTP chunk lab is not BitTorrent wire
protocol, a finished radio router, or a Commons Snapshot Pack implementation.
It has no internet tracker/DHT and never imports downloads into personal SAGE.

C–F are four independent personal SAGE devices, not four public validators.
The separate Lightbearer validator lab retains its own identities, governance
and fault assumptions. A/B Lamplighters do not acquire that role.

## Completed A/B preparation

Both public WiFi pack seeds are enabled, active and restart-verified after the
approved deletion of each native personal SAGE store, including test memories,
vault keys and agent identities. Legacy endpoint services are stopped/inhibited.
Signed 5,686-chunk resume, multi-source fetch and seed-loss fallback passed; eight
focused tests pass. [Exact evidence](../../work/ab-support-lab-20260927/README.md).
Scoped SSH administration and controlled A/B reboot checks are now complete.
The A/B pack and monitoring dashboard is installed; broader managed C–F administration,
LoRa pack delivery and RF repeater commissioning remain open. User acceptance proceeds on C–F; no further A/B endpoint work blocks it.

Preparation is recorded separately as completed SAGE milestone
`60de3f8d-7931-42af-ad3e-71bca89c3e5f`. The two current role tasks and final
18-task open count were verified after the local SAGE upgrade; no role update
remains pending. The interrupted submission was reconciled without duplication.

## A/B closeout for C on 28 September

Both seeds auto-started after controlled device reboots. Full cache verification,
peer manifest/sample-chunk reads, retired-service inhibition and current native
store absence passed on both. Scoped SSH operator commands are installed and
ordinary-user restart denial was verified. [Morning runbook](AB_OPERATOR_RUNBOOK.md)
and [closeout receipts](../../work/ab-closeout-20260927/README.md).

Completed SAGE closeout milestone: `030c25f8-1f8c-40fa-a9f0-1ab2b556065c`.

## A/B dashboard and existing screens — 28 September

Owner clarified that both A/B screens should be used. The separate Lamplighter
web dashboard is now installed: approved-pack distribution, verified progress,
pause/resume, A/B health, USB hardware, cached LAN devices and screen connectivity.
Both-direction transfers, pause/interrupted resume, ordinary full-cache/peer
checks and automatic startup of seeds/dashboard/screens after reboot passed.
26 focused regressions pass. [Dashboard runbook](AB_DASHBOARD.md) and
[live evidence](../../work/ab-dashboard-20260928/README.md).

Completed milestone: `f1490ee9-66e3-482c-8210-6f591e6753e5`. The remaining support
integration task is `74faea3a-9dd8-4e83-9650-b633b0ffcd43`; the obsolete headless
record was superseded and three incorrect no-screen memories deprecated.
LoRa/repeater and C–F receiver onboarding stay open; C can proceed next.

## Next session handoff

Start at [NEXT_SESSION.md](NEXT_SESSION.md). C is first; D/E/F follow after C is
qualified. A/B support preparation and dashboard are complete. The remaining
A/B integration task is planned, not an overnight active deployment.

Three stale descriptions were replaced with committed successors before dropping
their predecessors: A/B remaining integration, C–F bring-up and curated-pack
consumer work. This corrects the now-completed dashboard/reboot checks and the
approved deletion of A/B native private-home pack copies. All acceptance scope
and evidence remain linked. The backlog retains 18 distinct open tasks; completing
preparation did not complete radio, consumer, security or product acceptance.

## C storage and current work — 28 September, after cold boot

*(Address note, 14 Oct 2026: this line stands as the 28 September record. The
unit now answers at `192.168.50.2` / `lantern-c.local`; nothing was listening on
`.104` when checked, while lantern-a and lantern-b still replied.)*

C is reachable as `lantern-c` at `192.168.50.104`. True SD-free NVMe cold boot
is verified, with 234 GiB usable filesystem and 190 GiB available. The completed
SAGE storage milestone is `60ea7f91-9e61-4b9d-9e7c-ff364b47e9b0`;
[storage evidence and bootstrap procedure](CF_SD_NVME_SETUP.md). D/E/F images
have unique identities and guarded migration tools, but physical boots remain
untested. No SAGE install or C inference result is claimed by this milestone.

After discussing Ubuntu image maintenance and custom desktop requirements,
proceed on the working Armbian/Ubuntu base for now. Resolve Python 3.11 and
Ubuntu compatibility explicitly before provisioning; system Python remains
unchanged. Install fresh C identity/software and measure the existing verified
fine-tuned stage-5 4B candidate. Preserve r3 and assemble a matched successor
with the saved runtime changes. Bare-board inference is separate from complete
product acceptance.

The owner requires portrait Lantern and a custom landscape desktop when used
with keyboard and mouse. Mode switching and orientation-sensor support remain
unimplemented; see [desktop requirement](CF_DISPLAY_LAYOUT.md). Orange Pi 5 Plus
has no onboard WiFi. Separate modules/adapters must be procured and tested;
Flint has been informed. Battery direction is external rear Velcro mounting,
with unused/external ports exposed and internally occupied ports handled inside.
Sustained power/cooling and antenna placement still require physical checks.

All 18 assigned open tasks were read. Four stale bring-up/model/UX/WiFi records
were replaced with committed, linked successors before retiring predecessors.
The open total remains 18; C setup and model work are active, other distinct
requirements remain planned. Eight old messages held by other sessions were
not reclaimed. Historical acceptance failures are preserved.

## C software progress after consolidation

The isolated Python 3.11.16 runtime built on C and the explicit Ubuntu platform
check passed. A new matched r4 base candidate passed manifest/selection, all
four unit base plans, and 385 focused regressions plus 11 subtests; transfer
to C is in progress. [Candidate evidence](../../work/cf-package-20260928-r4/README.md).

The exact fine-tuned stage-5 4B now runs on C's NPU: 4.52 GiB peak cgroup,
zero swap, about 5.61 tokens/sec on the longer smoke answer, and 66.538°C peak.
It failed the arithmetic smoke (`17 + 26` → `39`). Engine execution success
is not semantic acceptance; no model promotion.
[Smoke review](../../work/unit-c-boot-20260928/4B_SMOKE_REVIEW.md).

## C base/identity closeout — current handoff

Completed SAGE milestone `3f7a7804-3310-44df-a18a-72248567e0fa` records
C's successful r5 base installation, native builds, 113 installed module checks,
SDK/dependency verification and independent native identity/protected config.
The completion receipt and actual private-listener check passed. No private
keys were copied off C. r4 stopped before installation on a native version-output
parser mismatch; r5's narrow fix preserves all hash/version gates.

C is no longer fresh. Do not rerun base installation/key creation or clone its
NVMe. Full app is not installed; SAGE services remain disabled/stopped. Actual
owner PIN/recovery, encrypted unlock/local enrollment, full hardware/lifecycle
acceptance, and the new desktop mode remain open. A truthful bare-board app
startup path needs work because current app inputs require real audio/radio
bindings and its kiosk flow needs GNOME/Wayland qualification.

The backlog remains 18 open tasks, with base and storage tracked as completed
linked milestones. Resume from [the updated handoff](NEXT_SESSION.md).
