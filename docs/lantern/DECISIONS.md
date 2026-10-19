# SAGE Lantern Hardware Decision Log

This log records the decisions that define the beta. Add a dated entry when a choice changes; do not silently rewrite history.

## D-001 — Two complete beta units

**Decision:** Build Unit A to completion, then reproduce it as Unit B.  
**Reason:** The product must prove device-to-device communication, but debugging two changing builds simultaneously would multiply uncertainty.

## D-002 — ROCK 5B+ as beta compute

**Decision:** Use the two already-owned ROCK 5B+ boards.  
**Consequence:** The first enclosure will be larger and thicker than a Rabbit R1, and active cooling is mandatory.

## D-003 — Rabbit R1 as reference, not platform

**Decision:** Use one Rabbit R1 for teardown, ergonomics, mechanical study, and design inspiration.  
**Consequence:** The beta must not depend on successfully reusing the Rabbit display, wheel, camera, or mainboard.

## D-004 — Waveshare modular interaction stack

**Decision:** Use the Waveshare ESP32-S3 audio board, 3.5-inch HDMI touch display, and USB hub for the beta.  
**Reason:** Purchasable modules reduce custom electronics work during functional validation.

## D-005 — Heltec V4 as MeshCore companion

**Decision:** Use one Heltec WiFi LoRa 32 V4 per ROCK, connected as a companion rather than a raw LoRa HAT.  
**Consequence:** MeshCore firmware and the host companion interface must be validated; raw Raspberry Pi HAT integration is out of scope.

## D-006 — Off-the-shelf power for beta

**Decision:** Use 65 W USB-C PD supplies on the bench and 130 W/20,000 mAh USB-C PD power banks for portable tests.  
**Consequence:** No custom internal battery/BMS work occurs before real power measurements.

**Amendment 2026-09-29 — C–F power input:** PD supplies alone do **not** power the
Orange Pi 5 Plus units. The 5 Plus Type-C power port performs **no PD
negotiation** and accepts a **fixed 5 V** input (Orange Pi wiki), so a PD brick
supplies only its default 5 V rail — Lenovo 65 W: 5 V/2 A ≈ 10 W, Apple: ≈ 5 V/2.4 A
≈ 12 W — against the board's **5 V/4 A (20 W)** requirement. Uncapped CPU-4B model
loads reset the board; power-capped loads run. Measured contrast: the Radxa
ROCK 5B+ negotiates 12 V/3 A (36 W) from the same charger class and is unaffected.
**Pack corrected 2026-09-29 (owner-confirmed):** the portable pack is a **Nitecore
NB10000 Gen 2** (10,000 mAh; ~15–18 W class — 5 V/3 A on its USB-A port, USB-C PD
in the same class), **not** the CARBO 20 (65 W) this file previously recorded. At
~15–18 W it **cannot** deliver 5 V/4 A even through a buck, so it is light-duty
only (panel, chat) and not the 4B model path. The 130 W/20,000 mAh UGREEN packs
and a fixed 5 V/4 A supply are the heavy-load options.
C–F may be fed by: (a) a fixed 5 V/4 A USB-C supply; (b) a higher-voltage PD
source — a Lenovo/MacBook brick (add a PD trigger unless the buck negotiates) or
the UGREEN 130 W pack — stepped down by a 5 V/≥4 A buck converter (bucks
purchased 2026-09-29); or (c) a
bench PSU at 5.1 V/5 A into the power port or the 40-pin 5 V pins (vendor wiki
§2.17). Acceptance: the uncapped CPU 4B load completes with the board up. The
65 W PD rows in `HARDWARE_BOM.md` remain valid for A/B only. Evidence:
[C reset root-cause](../../work/unit-c-boot-20260928/C_RESETS_20260929.md).

## D-007 — Simple physical controls

**Decision:** Use one momentary PTT button and one KY-040 encoder with click per unit.  
**Consequence:** A custom R1-style wheel may be printed later, but is not a dependency for electrical bring-up.

## D-008 — Enclosure follows measurement

**Decision:** Do not start detailed enclosure CAD until the functioning stack is assembled and measured.  
**Reason:** Cable bends, connector access, airflow, antenna placement, acoustic volumes, and power-bank dimensions cannot be safely guessed.

## D-009 — Product naming architecture

**Decision:** Keep SAGE Commons as the overall mission, governed public knowledge network and ecosystem; name the portable device product SAGE Lantern; use Knowledge Point for a physical/institutional station, SAGE Lamplighter for the offline super-seed/field system, and SAGE Lightbearer for a stable public-service node/profile.  
**Consequence:** Device-specific requirements and artifacts use SAGE Lantern. Public-chain, governance, funding, pilot and ecosystem materials retain SAGE Commons. A Knowledge Point may host isolated Lightbearer profiles, but the facility name grants no validator authority. Every validator is a separately governed Lightbearer Validator. See [`../TERMINOLOGY.md`](../TERMINOLOGY.md).

**Superseded in part (2026-09-21):** the first clause — "SAGE Commons as the overall mission, governed public knowledge network and ecosystem" — is corrected by D-014. The remaining naming assignments (SAGE Lantern, Knowledge Point, SAGE Lamplighter, SAGE Lightbearer) stand.

## D-010 — One personal SAGE node per Lantern

**Decision:** Plan for each SAGE Lantern to run its own full local personal SAGE node. A Knowledge Point is an optional persistence and synchronisation peer, not a runtime dependency for the local voice loop.
**Qualification:** Confirm the decision with measured ROCK 5B+ latency, memory, thermal, and portable-power evidence. Client-only operation is a documented fallback if the full node cannot meet the beta's stability requirements.

## D-011 — Two-part end-to-end acceptance demo

**Decision:** The first end-to-end proof consists of five offline voice loops on Unit A followed by a bounded, attributable artifact exchange from Unit A to Unit B and back again over MeshCore.
**Consequence:** A speaking prototype or a raw radio ping alone is insufficient. The evidence must correlate application, SAGE, and transport states and include recovery, timing, size, retry, and duplicate behavior.

## D-012 — Separate per-unit identities and constrained transport

**Decision:** Each unit receives a unique ordinary Ed25519 SAGE identity and a separate unique radio identity after the base image is cloned. MeshCore is an untrusted constrained transport adapter, not an authority source.
**Consequence:** Never clone `~/.sage`, Root/Admin keys, receive tokens, idempotency state, or radio identity. Use canonical `/v1/messages` semantics at the SAGE boundary and validate every radio-carried envelope before agent exposure.

## D-013 — Lanterns propose; stable servers validate

**Decision:** Every Lantern may query the public SAGE Commons and submit signed hyperlocal-knowledge proposals, but Lanterns are not validators on the public Commons chain. Public consensus runs on a governed set of stable, always-on server validators.
**Reason:** Intermittent handheld connectivity must not reduce public-chain liveness or place consensus keys on portable devices. Proposing knowledge and validating consensus are separate roles.
**Consequence:** A Lantern queues proposals while offline and submits them when connected with provenance, locality, observation time, confidence, and freshness/expiry metadata. It receives no public validator key or voting power.

## D-014 — SAGE Commons is the fabric; the Lantern is its first deployment

**Decision:** SAGE Commons is the fabric: the core mesh technology and governed public network — MeshCore for transport plus SAGE running the public chain, with the contracts that make them one interoperable, community-owned commons — that community networks, services and devices join. SAGE Lantern is the first deployment on that fabric: each Lantern holds its own sovereign private SAGE (its own private chain) and connects to the Commons as a proposer/query client, never as a public validator.
**Reason:** Owner direction, 2026-09-21, resolving a live conflict in the written material: the repo described the Commons as the mission/programme surrounding the device, while the owner describes it as the fabric — core mesh technology aimed at a community-owned network layer of the "internet 2.0" kind — on which the Lantern is the first deployment. A statistic, a deck page or a public sentence built on the wrong one of those two shapes is wrong in public.
**Consequence:** The Commons is a technical and network object (transport, public chain, protocols, contracts), not a marketing umbrella. The mission belongs to the initiative and the proposed Foundation; the Foundation stewards the fabric but does not own it. "Shared knowledge network" describes what the fabric carries, not what it is. Device-specific requirements and artifacts continue to use SAGE Lantern.
**Affects:** [`../TERMINOLOGY.md`](../TERMINOLOGY.md) (SAGE Commons row and the new "Commons fabric" section), [`../MASTER_TECHNICAL_PRD.md`](../MASTER_TECHNICAL_PRD.md) §3 taxonomy row, `CONTRIBUTING.md`. Public website copy is owned by the marketing agent and is flagged to it, not edited here.

## D-015 — Bring-your-own power; ROCK 5B+ preferred going forward

**Decision:** Units ship as **hardware only and the user supplies the power** — no
supply and no battery pack is bundled, and the design stays flexible about what
the user plugs in (owner direction, 2026-09-29). For future units the **Radxa
ROCK family is the preferred board**: its USB-C input negotiates PD (common
chargers work), and the 5B+ additionally carries **onboard WiFi/Bluetooth**,
which the Orange Pi 5 Plus lacks. The next two units, **G and H, are to be at
least 16 GB** (planned as ROCK 5B+ 16 GB); sourcing research is with the hardware
workstream. C–F are already Orange Pi 5 Plus (commissioned and running) and stay
as they are.

**Reason:** measured on the fleet 2026-09-29 — the identical 4B artifact loads and
serves on the ROCK 5B (unit B) on a negotiated 12 V/3 A (36 W) contract, and resets the
Orange Pi 5 Plus, whose power port is a fixed 5 V input with no PD, so the same
charger only offers its default 5 V rail (~10–12 W) against a 20 W need. With
power bring-your-own, that input behaviour is a user-facing product property, not
a bench detail.

**Consequence:** the fleet is mixed until a later platform decision — C–F (Orange
Pi) require a 5 V/4 A path; G/H (ROCK) accept common PD chargers. No supply
appears in the BOM or the cost table, and each acceptance run must record which
supply was used. Two board profiles also mean two enclosure/port/thermal variants
and two replication paths, which the hardware workstream carries. The A/B units
(**Radxa ROCK 5B, 8 GB** — owner-confirmed 2026-09-29, the base model and not the
5B+) already demonstrate the ROCK PD behaviour.

**Amendment 2026-09-30 — ROCK 5B+ 12 GB committed as the main compute (owner
direction, after the Orange Pi's power path proved to be the recurring blocker):**
the owner commits to the **ROCK 5B+ 12 GB** as the main compute for the fleet,
with the 16 GB SKU taken instead wherever it is available at a comparable price.
The software side's measured determination supports it: the whole stack runs in
5.3 GB with both NPU model lanes resident, a future 4 B tier peaks at 5.7 GB and
still fits 12 GB; the 5B+ is the same RK3588-class SoC with the same 3-core
6 TOPS NPU, so the RKNN/RKLLM artifacts are the same family of artifacts; and it
keeps the PD power input and onboard WiFi/BT that the product relies on. 8 GB
fails (the 4 B tier alone needs 5.6–5.7 GB) and 12 GB is the floor, recorded in
`FINAL_COMPUTE_REQUIREMENT.md`. The one open hardware gate is the product DSI
panel (Waveshare SKU 35000) host support on the 5B+; it is tracked as a
first-article acceptance item. **The owner added the internal-wiring rule the
same day: display and camera must be internal ribbons inside the shell — no
external cabling.** HDMI/USB is bench-only, so the first-article gate is now
the deciding one for the remaining fleet units: prove an internal DSI panel and
an internal CSI camera path on the board that lands (connector, ribbon,
overlay, panel and sensor bring-up), then take the rest. Neither candidate has
that path verified today. C–F remain Orange Pi 5 Plus bench/acceptance units
and still require the 5 V/4 A fixed supply (adapters staged, unpaid —
2026-09-30); they are not the product board.

**Affects:** [DEVICE_ROLES.md](DEVICE_ROLES.md) (new G/H row), the D-006 amendment
and [HARDWARE_BOM.md](HARDWARE_BOM.md) power corrections in this repo, and the
hardware workstream's live `BOM.md` / `BUILD_STATUS.md` via Flint.

**Clarification 2026-09-29 (owner):** the two-platform fleet is deliberate, not
drift. C–F (Orange Pi 5 Plus) and G/H (ROCK 5B+ 16 GB) exist to **test both
boards and decide the official launch platform in January 2027**, alongside the
already-planned launch gates. Offering users **two options** is acceptable and is
also treated as **supply-chain resilience**: if one board becomes unavailable,
priced out or constrained, the other is a qualified alternative rather than a
rebuild. Consequences: (a) every acceptance measurement taken on C–F from now on
should be repeatable on G/H with the same model, tasks and power conditions, so
the January decision rests on paired evidence rather than impressions; (b) keep
both platform profiles alive in provisioning, enclosure and documentation — do
not collapse to one early; (c) the decision criteria are to be stated before the
data lands (power/charger behaviour under BYO power, sustained-load stability,
thermals, peripheral fit — dual LoRa/HAT + GNSS + WiFi + panel/touch + camera +
mic — enclosure fit, IO, toolchain/kernel support, price and availability);
(d) the current scoreboard has exactly one hard datapoint: with bring-your-own
power the ROCK handles the uncapped 4B load on a common PD charger (12 V/3 A)
while the Orange Pi resets without a 5 V/4 A source — the Orange Pi is not
eliminated on that axis if the supply requirement is acceptable to users.

**Clarification 2026-09-29 (owner, later):** **A and B are the 8 GB Radxa ROCK 5B
(the base model, not the 5B+); C–F are the Orange Pi 5 Plus 16 GB.** G/H need
**16 GB at minimum**. The ROCK is preferred over the Orange Pi not only for its PD
input but because it ships with the wireless the product needs — **WiFi and
Bluetooth onboard**; the Orange Pi 5 Plus has no onboard WiFi, so C–F carry an
open wireless-procurement task and an added module where the ROCK would need
none. Onboard wireless is therefore a scored criterion in the January bake-off,
alongside power behaviour, sustained-load stability, thermals, peripheral fit,
enclosure fit, IO, toolchain/kernel support, price and availability.

## D-016 — C–F photo questions run on the NPU (matched Qwen3.5-0.8B VL pair)

**Decision:** Vision/photo inference on the C–F profile runs on the **RK3588 NPU**
with the **runtime-matched Qwen3.5-0.8B pair** (FP16 vision `.rknn` + W8A8 `.rkllm`,
both converted with rkllm-toolkit **1.3.0**), served by a resident process on
`127.0.0.1:8082` in the same OpenAI-shaped contract the runtime already uses. The
8081 NPU text server's `--fallback-url` keeps pointing at 8082, so the runtime's
photo path is unchanged in shape. The CPU 4B + mmproj vision lane is retired for
C–F (kept available for bench comparison only) — the power-limited board cannot
carry its CPU load under the bring-your-own-power contract (D-015).

**Reason:** measured on C 2026-09-29 — **~5.0 s per photo question p50 over ten
consecutive runs** (vision encode 0.73–0.88 s, prefill 2.66–2.68 s incl. 196 image
tokens, decode ~42 ms/token), engine RSS 1.45 GiB, ten of ten grounded answers
(including the real Lantern panel screenshot and a verified request through the
production 8081 proxy path), streaming verified, and **zero board resets** (boot id
unchanged across the session). The hard requirement this pins down: the model
conversion's toolkit generation must match the installed runtime (1.3.0) — the
earlier community pairs (1.2.3) are rejected at runtime by design, which is what
"broken vision" turned out to be. Evidence:
`work/unit-c-boot-20260928/VLM_NPU_LANE_20260929.md`,
`vlm-npu-bench-20260929.json`, `VISION_MODEL_OPTIONS.md` (RESULT section).

**Consequences:** (a) reconverting with rkllm-toolkit 1.3.0 remains the route for
any future custom vision fine-tune (owner-approved); it was not required for this
selection. (b) Release plumbing landed 2026-09-29 (same day): the two vision
sources, the librknnrt runtime, the three RKNN headers and the two model files are
pinned in `scripts/stage-appliance-release.py`, `scripts/lantern/install-npu-payload.py`
builds and installs the vision engine (OpenCV 4 dev package added to the base
prerequisites), and `software/systemd/sage-lantern-vlm.service` (8082) ships in the
unit set. Still open before this is the shipped default: staging the pinned
artifacts into the release tree, reworking the reviewed fresh-vision activation
planner from its CPU-fallback profile to the NPU-vision profile, and the D/E/F +
G/H reruns — the runtime's own units stay gated on a legitimately produced
`LOCAL_VOICE_READY`, and nothing here fabricates it. (c) G/H (ROCK)
acceptance repeats the same measurements with the same pair and records the supply
used, per the D-015 clarification. (d) Quality is 0.8B-class: keep the
`VISION_TEXT_POLICY` constraints and do not present it as a 4B-quality replacement.
(e) These are bench measurements with images on disk — no CSI camera was fitted for
any of it (IMX519 modules not delivered), so no camera-pipeline claim follows from
this decision, and zero resets is a lane result, not a closed-envelope thermal result.

**Amendment 2026-09-29 (later the same day):** the reviewed fresh-vision activation
planner is reworked to this profile. `scripts/lantern/plan-fresh-vision.py` now
serves units A–F, pins `sage-lantern-vlm.service` as the 8082 provider (bytes and
evidence requirements included), proposes the vision unit as the enable intent,
keeps the retired CPU 4B explicitly disabled, and requires both resident model
units to keep the reviewed sleep drop-ins. Idle suspend covers the vision lane in
the same contract as the chat lane: a model unit joins the residency set once it is
enabled, must then be active with a root-published start baseline
(`/run/sage-lantern-model/<unit>.json`), must survive a sleep cycle as the same
instance, and the drain wait requires an idle slot on **both** model servers
(8081 and 8082) before any suspend is submitted. Nothing is stopped by the idle
path and nothing is restarted on wake — suspend-to-RAM retains both models, which
is why the same-instance checks exist. The chat unit no longer carries the legacy
`Wants=`/`After=` on the retired CPU 4B: both providers draft 8082, and a merely
disabled unit would still have been started by that dependency. Still open:
staging the pinned artifacts into the release tree and the D/E/F + G/H reruns.

## D-017 — A commissioned unit is succeeded, never re-commissioned

**Decision:** A fleet unit is commissioned once. A successor release is installed
**in place on the commissioned unit**: the unit is never re-commissioned, its
identity is never cloned onto another unit, and its key material is never
regenerated. Five rules, all landed in r11–r16 of the C–F lane:

1. The successor install is anchored on the **installed** release tree
   (`--installed-release-root`, with the commissioned release as
   `--predecessor-release-root` + `--commissioned-manifest-sha256`), not on the
   tree the unit was commissioned from. After a success the anchor moves to the
   new release; the commissioning pin stays at the release that commissioned the
   unit.
2. Every file the install would overwrite must hash to the predecessor release's
   recorded digest, already match the new release, or be explicitly accepted with
   `--accept-replaced` (operator stopgaps at paths the release now owns). Accepted
   targets are recorded as `replaced_unverifiable_targets` in the receipt — never
   replaced silently.
3. A commissioning output the **running appliance itself** evolved may be accepted
   only with `--accept-evolved-commissioning-output`, only for `sage.yaml`
   (`EVOLVABLE_COMMISSION_FILES` is exactly that set), and the observed digest is
   recorded as `accepted_evolved_commissioning_outputs`. Every other commissioning
   output stays byte-verified; a changed one is refused.
4. A refusal **names its gate** — the failing check is printed, never swallowed by
   a catch-all except clause.
5. The venv interpreter is verified **through** its symlink: every venv `bin/python`
   is a symlink by construction, so the check resolves the chain and requires a
   root-owned, non-writable interpreter under `/usr/bin` or `/opt/lantern-python/`.

**Reason:** C–F (and later G/H) are already commissioned, and the fleet rules forbid
cloning an identity, rerunning the base install or regenerating keys on a
commissioned unit. Before this, a fresh unit was the only supported target, so no
successor release could reach the fleet: the r11 refusals were all gates in that
path (successor anchor, evolved personal config, symlinked interpreter, NPU
provenance written before the vision engine existed). r14/r16 on C were the first
successor installs to complete end-to-end, including the NPU payload in place.

**Known gap (recorded with the decision):** the successor install replaces runtime
code and units on a **running** appliance. Services are stopped **by hand** before
an install today, and the installer neither requires nor drives the runtime's
drain procedure — a live install is an operator-coordinated stop, not a drained
transition. Prefer the maintenance/drain path where it exists, and say so in the
evidence. Closing the gap (install requires drain evidence, or drives the drain
itself) is open release work.

**Evidence:** the install chain on C in `work/cf-package-20260929-r16/README.md`
(r14 app install anchored on r6, NPU successor install, r16 app install anchored on
r14), receipts `lantern-cf-20260929-r14` / `lantern-cf-20260929-r16`, commits
`8761442` ("an installed unit can be succeeded, and a refusal names its gate") and
`75747b6` ("a running unit can succeed a release, and the NPU lane installs in
place"), `scripts/lantern/install-fleet-appliance-local.py` (`successor_state`,
`appliance_interpreter`), `scripts/lantern/provision-fresh-appliance.py`
(`EVOLVABLE_COMMISSION_FILES`, `commission_output_drift`).

**Affects:** [REAL_UNIT_PROVISIONING.md](REAL_UNIT_PROVISIONING.md) and the D/E/F
replication playbook (`work/cf-package-20260929-r16/README.md`, "Notes for the next
unit"), which must anchor each unit on its own installed tree.

## D-018 — The C–F sleep lane runs a Wayland session profile, selected by an installed record

**Decision:** The guarded deep-sleep controller keeps one journal, one fence
discipline and one model contract, and takes its session layer from an installed
profile record instead of assuming the A/B X11 kiosk. Six rules:

1. **The installed record selects the profile.** `/etc/sage-lantern/session-profile`
   must be a root-owned, single-link, non-symlink regular file containing exactly
   `x11-kiosk\n` or `wayland-mutter\n`. A unit without the record keeps the
   reviewed X11 profile — the only profile an installation without the record can
   have been commissioned as; any other content, owner or link refuses and names
   the gate. The C–F bundle stages `wayland-mutter`; the A/B bundle stages
   `x11-kiosk`.
2. **Idle comes from the compositor that owns the session.** C–F read
   `org.gnome.Mutter.IdleMonitor.GetIdletime` over the operator's session bus
   (`runuser -u <operator>` with `XDG_RUNTIME_DIR` and
   `DBUS_SESSION_BUS_ADDRESS` pinned to `/run/user/<uid>`), bounded uint64
   milliseconds. Never X11, never the mode supervisor's input presence.
3. **Display blank/unblank is the GNOME screen shield, and blanking refuses to
   lock the kiosk.** `org.gnome.ScreenSaver.SetActive true|false` over the same
   bus; blank first requires `org.gnome.desktop.screensaver lock-enabled` to be
   exactly false and then confirms the shield became active. Unblanking is never
   gated and is always attempted on resume, so a shield can never strand an
   unattended appliance behind a password prompt.
4. **The console fence freezes the mode supervisor's browser group.** The
   supervisor (`python3 /usr/local/libexec/sage-lantern-mode-supervisor`, the
   MainPID of the GNOME application scope
   `app-gnome-sage\x2dlantern\x2dconsole-<pid>.scope`) keeps running: it touches
   no model, no app server and no private endpoint, and its respawn loop treats a
   stopped browser as running. Exactly one Chromium child that holds the unit's
   console profile, in its own process group, and every process in that group,
   are SIGSTOPped before the cycle and SIGCONTinued after it by pinned
   pidfd identities. A second browser, a profile holder outside the group, a
   grouped process outside the frozen set, or any identity change refuses the
   cycle. Chromium rewrites `/proc/<pid>/cmdline` into one space-joined entry, so
   the profile is matched after the same split the X11 fence already performs.
5. **The stop set follows the installed profile.** C–F run no camera unit and no
   X11 display-power unit, so the C–F profile stops the device-auth, voice-loop
   and fleet-admin workers only; a stop request for a worker outside the profile
   is refused, not treated as success. Starting after wake uses the same set.
   The C–F voice drop-in carries the reviewed non-mesh ExecStart
   (`--photo-ready --drain-request /run/sage-lantern-voice-drain-request.json`,
   the path the controller publishes to); on a mesh-managed unit
   (`SAGE_LANTERN_MESH_BROKER=1` or `/etc/sage-lantern/mesh-runtime.json`
   present) the mesh broker owns that ExecStart and the deployer refuses the
   bundle instead of silently displacing it.
6. **The bundle is staged, never deployed, and it does not make a unit sleepable
   by itself.** `scripts/lantern/install-cf-idle-suspend-local.py` builds the
   plan, stages it as root and checks it against the frozen plan
   (`--apply`/`--check`, `activation=false`). The idle-suspend enable marker,
   unit enablement and the voice lane remain separate operator steps, and a real
   cycle still needs the drain hooks, both resident model baselines, the
   inference guard, an idle slot on 8081 and 8082, the reviewed fences and the
   sleep guard.
7. **A missing optional worker never blocks the device.** The voice lane joins
   the drain contract only once its unit is enabled, exactly like the second
   model unit: a unit that was never enabled is not part of the installation's
   profile, so the controller does not require a voice instance, publishes no
   voice drain request, stops nothing voice-shaped and starts nothing on wake.
   An enabled-but-inactive voice unit is still unknown evidence and refuses. The
   appliance therefore keeps its console, on-screen keyboard, device lock and
   automatic sleep when mesh, radio or voice are absent; the drain covers the
   legs the unit actually runs, and the auth leg stays mandatory.

**Reason:** C–F are commissioned Wayland units. The reviewed A/B stack cannot
observe idle or fence the console there — there is no `radxa` account, no X11
display and no launcher tree — and applying it as-is would install a
`systemd-suspend` guard that no controller on the unit could satisfy, i.e. it
would remove the unit's ability to suspend without giving it the reviewed sleep
path. The model half of the contract is already C–F-shaped and is unchanged.

**Evidence (read on C, 29–30 Sep 2026; no reboot, boot id `1e0d55bb-7c56-4203-a5f9-350902e88be3`):**
uid 1000 is `sage`, `runuser -u radxa` exits 1; the session is GNOME Shell on
Wayland under `systemd --user`; the console is one Chromium kiosk, child of
`sage-lantern-mode-supervisor`, in its own process group, inside the GNOME
application scope; Mutter returns idle milliseconds (`t 26809003` at 06:04);
`lock-enabled=false` and `idle-delay=0`; the DRM connector's `dpms` attribute is
read-only (0444) even for root and there is no backlight device, so kernel sysfs
is not an available display path; the browser's command line is a single
space-joined `argv[0]` entry. The signal-free preflight of the new fence passed
against the live session, and the staged C–F bundle verified at
`/var/lib/lantern-work/cf-sleep-20260930` (37 payload files, manifest
`46f3cfb7…`, `deployed:false`, `activated:false`), with the frozen plan kept
root-only beside it as `cf-sleep-20260930.plan.json`.

**Evidence, 30 Sep 2026 (same day):** the session layer was rehearsed live on C
from the staged module — idle query, screen-shield blank (verified active) and
unblank (verified inactive), and the console fence: preflight, freeze of the
browser plus its nine grouped children (all state `T`, same instance), resume to
`S` on the same pid and start ticks, temporary fence removed. The C–F deployer
refused the first bundle by name (`mesh_voice_lane_owns_execstart`) and the
finding is recorded: C's voice unit carries a dangling
`SAGE_LANTERN_MESH_BROKER=1` from the fleet installer while no mesh runtime or
`/etc/sage-lantern/mesh-runtime.json` exists (its reviewed preparation needs a
radio fingerprint, and C's plan declares the radio absent). The C–F voice
drop-in therefore selects the reviewed non-mesh drain path explicitly, and the
deployer refuses only a unit whose mesh runtime is actually installed.

**Deployed and verified on C, 30 Sep 2026 (not activated):** the frozen bundle
(`cf-sleep-20260930c`, manifest `29d1289c…`) was installed with 25 anchored
backups and zero stopgaps; the resident model units republished their start
baselines; `--check` prints `Sleep guards, drain hooks and idle observation
verified; no suspend requested` with no voice lane; the session layer was
rehearsed live (shield blank/unblank verified; the console fence froze browser
2682 and its nine grouped children and resumed the same instance); and the
bench substitute for the photo rehearsal passed — a vision-lane inference in
flight makes sleep admission refuse, and both lanes are idle again afterwards.
Seven unconfirmed auth drain receipts (instances that never shut down cleanly
before this boot) were preserved with a reconciliation note under
`/var/backups/sage-lantern-auth-drain-20260930T0650Z`.

**First full cycle on C, 30 Sep 2026 (rehearsed, one-shot, 60 s RTC wake):**
`sleep_cycle=resumed`, kernel suspend counter 1, **boot id unchanged**
(`1e0d55bb…`) so the unit slept and woke rather than rebooted, both model units
still the same instances (pids 34845/34836, invocation ids unchanged), the
console browser resumed and answering, no fence or marker left behind, and
`--check` green afterwards. The operator opt-in marker is still absent: sleep
remains opt-in.

Three findings from the live cycles are now part of the lane:

* **The image disables suspend.** C ships
  `/etc/systemd/sleep.conf.d/00-disable.conf` with `AllowSuspend=no`, so every
  submission failed inside systemd-sleep (*"Sleep operation suspend is disabled
  by configuration"*). `/etc/systemd/sleep.conf.d/90-sage-lantern.conf` sets
  `AllowSuspend=yes`; the previous `sleep.conf` is preserved under
  `/var/backups/sage-lantern-sleep-conf-20260930`. **D/E/F need the same
  drop-in**, and G/H images must be checked for it.
* **A refused or aborted attempt leaves `systemd-suspend.service` failed and
  `suspend.target` active**, which the reviewed `verify_sleep_idle()` then treats
  as unknown evidence — every later cycle refuses until an operator runs
  `systemctl reset-failed systemd-suspend.service` and stops the targets. That
  recovery step is not yet automated; it belongs in the operator runbook (or as
  an adapter precondition before a cycle).
* **The compositor's idle counter is coarser than the X11 one.** It can lag wall
  time by seconds, so the sleep-time re-check now allows 5 s of lag instead of
  1 s while still refusing a counter that moved backwards (input).

**Still open (small):** the panel blank on C–F is best-effort and reported
(`display_off=unconfirmed` on the successful cycle — the panel is dark during S3
anyway and the console is fenced); the photo-in-flight rehearsal needs the
camera and voice lane (no CSI camera on C; the model-busy proof is the bench
substitute); and an unclean power loss still leaves automatic sleep refused
until auth drain receipts are reconciled.

**Amendment 2026-10-09 — D-018 × D-022 integration: a blank is skipped, and a
shield this cycle did not create is preserved.** The sleep lane and the D-022
desktop lock meet at one rule: `lock-enabled=true` means this lane never
activates a shield, so the cycle owns no blank and `on()` must not dismiss one.
(a) The skip stays explicit and reported (`display_off=skipped_lock_enabled`)
and is **not** evidence that blank/unblank continues to work — the panel is dark
because the unit is in S3, and no panel-off path is verified on a single-panel
C–F unit. (b) `on()` removes only a shield the same cycle confirmed
(`restored`) and takes `shield_unblank_unconfirmed` into recovery when an owned
blank cannot be verified removed; failed owned restoration stays fail-closed.
(c) An active shield the cycle did not create — an operator blank, a login lock,
or a blank whose activation was never confirmed — is left untouched and reported
`preserved_unowned`, in the wake journal as `display_on=preserved_unowned`.
(d) That state completes the cycle without claiming a restored console and is
never treated as the operator having authenticated at the panel. A mode-only
blank decision or a temporary lock-policy downgrade stays rejected. **Not
verified on a unit:** no managed sleep/abort/wake, concealment or pixel-privacy
acceptance follows from this ruling.
**Evidence:** `software/runtime/tests/test_suspend_session.py` (off/on ownership
cases), the author settings-to-shield-to-cycle control in
`work/ember-d022-repair-20261009/controls/`, and the D-022 review's
`integration-review/INERT_INTEGRATION.py`, retained as the pre-repair
counterexample.

**Affects:** [AUTOMATIC_DEEP_SLEEP.md](AUTOMATIC_DEEP_SLEEP.md) (C–F status),
`software/runtime/src/sage_lantern/suspend_session.py`,
`software/runtime/src/sage_lantern/suspend_system.py` (profile stop set),
`scripts/lantern/lantern-idle-suspend.py` (session selection),
`scripts/lantern/install-cf-idle-suspend-local.py`, and the D/E/F replication
playbook, which must stage its own `wayland-mutter` record per unit.

## D-019 — A radio-less unit runs the question service locally, and typed questions are a first-class lane

**Decision:** The appliance must stay usable without a radio and without a
working capture path: a user asks by typing, and the answer comes from the one
reviewed pipeline. Seven rules:

1. **Audio is a per-session capability, not a service start condition.** The
   question service is `sage-lantern-voice-loop.service`; a session that asks by
   typing never opens the capture device, never transcribes and never claims a
   microphone. `audio_readiness --admit` stays the gate for *capture* (and for
   the transcribed lane's admission through the spoken path), not for the
   service's ability to exist.
2. **A non-mesh unit carries the reviewed local question gate.**
   `/etc/systemd/system/sage-lantern-voice-loop.service.d/60-local-question-lane.conf`
   holds exactly these bytes: `[Unit]` with `ConditionPathExists=` (reset) then
   `ConditionPathExists=/var/lib/sage-lantern/LOCAL_VOICE_READY` and
   `ConditionPathExists=!/run/sage-lantern-sleep.fenced`, and `[Service]` with
   `ExecCondition=` (reset) and `Restart=on-failure`. The empty assignments
   reset the mesh start gate (`40-mesh-broker.conf`) and the audio start gate
   (`50-fresh-audio-validation.conf`); the sleep fence stays authoritative, and
   the sleep bundle's later drop-in keeps its ExecStart. A unit whose mesh
   runtime is actually installed refuses this gate by name
   (`mesh_question_lane_owns_voice_start`): there the broker authorizes the
   voice start itself.
3. **Typed questions use the spoken lane's review path, never a second answer
   path.** `POST /question/start` (device session required, refused while
   another session is live, replay-safe per request id) opens a review with no
   transcript and no capture; `POST /review/submit` carries the typed text
   through the same validation, cancel, deadline and drain fences as a recorded
   question, and the same runtime, model, personal SAGE and history record the
   spoken lane uses. The state publishes `typed_ready`, and a typed review is
   marked `input_source: typed`.
   An attached photo travels with the typed question exactly as it does with a
   recorded one: validated to the same bounds, digest-bound, written 0600 under
   the photo directory, retained for protected history only under the same media
   consent, resolved by the same answer path, and discarded on cancel, expiry or
   finish. A device with neither microphone nor radio can therefore still ask a
   text question, a photo question, or both.
4. **The console never presents a dead end.** A `Type` control sits beside
   `Ask` and is offered whenever the question service advertises it — not only
   when audio is down. Audio and service state is shown as a notice (with the
   reason), the spoken control is disabled rather than silently failing, and a
   live phase (a question under review, an answer already produced) is
   authoritative over readiness flags, so a drop mid-turn cannot erase what the
   device already showed.
5. **The answer path remains unlock-gated.** Protected conversation memory
   requires an unlocked device epoch (`device_access.access_epoch()` returns the
   epoch only while the broker reports `locked: false`). While the unit is
   locked, no question — typed or spoken — can be answered, by design.
6. **Console changes ship through one anchored local lane.**
   `build-console-bundle-local.py` builds the app from the reviewed tree and
   freezes a staged bundle (app UI set plus the question loop plus, optionally,
   the gate, staged from the reviewed release file
   `software/systemd/sage-lantern-voice-loop.service.d/60-local-question-lane.conf`,
   and, with `--anchor-from`, the minimal D-017 anchor tree for one unit);
   `deploy-staged-console-bundle-local.py` verifies every payload
   digest, refuses a destination that neither matches the anchor nor is accepted
   by name, refuses a staged `package.json`/`package-lock.json` change (no
   `node_modules` travels in a console bundle), backs up every replaced file,
   installs atomically, reloads systemd and writes a receipt. The anchor is the
   installed release payload, or the previously deployed bundle stage for the
   second and later updates. The release stager carries the gate in the payload
   for review and for the next cut; the app installer does not install it, so
   activation stays an explicit, per-unit step
   (`docs/lantern/CF_CONSOLE_LANE_ACTIVATION.md`).
7. **Readiness is never claimed from this lane.** The staging output, the
   deploy receipt and the planner's `plan-only` record are not acceptance. A
   panel acceptance still needs an owner unlock and a real typed question on the
   glass.

**Reason:** C–F ship a mesh-shaped voice profile. On a unit with no radio the
broker's start authorization never appears and the fleet's audio gate refuses an
uncommissioned capture path, so the question service could not run at all: the
console showed "Voice is not ready" and every control that could answer was
disabled — the exact dead end the owner forbade ("no radio or no audio must
never make the appliance unusable"). The typed lane reuses the review/submit
path precisely so there is no second, unaudited way for a question to reach a
model.

**Evidence (read on C, 30 Sep 2026; boot id `1e0d55bb…` unchanged):**
`sage-lantern-voice-loop.service` was `disabled`/`inactive` with
`ConditionPathExists=/run/sage-lantern-mesh-control/voice-start-authorized.json`
unsatisfied (the directory does not exist) and an `ExecCondition` demanding
`audio_readiness --admit`. Bundle `console-20260930-typed-lane` (app build
`2dbef600…`, 120 files, manifest `62283fdb…`) was deployed with 88 anchored
backups and zero unverifiable replacements (receipt
`/var/backups/sage-lantern-console-20260930T1050Z/receipt.json`); bundle
`console-20260930-typed-lane-r2` followed, anchored on the first stage
(receipt `/var/backups/sage-lantern-console-20260930T1100Z`). The unit then
reported `typed_ready: true`, `review_ready: true` from `GET /health`; the
service was enabled at boot as the reviewed local activation; the sleep check
still prints `Sleep guards, drain hooks and idle observation verified; no
suspend requested`; and `plan-fresh-vision.py --unit-id unit-c --fresh-audio`
now returns `voice_sleep_profile: non-mesh-broker`, `local_question_lane: true`
with the gate in `preserve`. The typed lane was rehearsed end to end on C with
the installed bytes and a capture device that does not exist
(`--alsa-device hw:CARD=absent,DEV=0`):
`{"question": "What is 17 plus 26?", "answer": "43", "input_source": "typed",
"captured_audio_files": [], "review_audio_files": [], "seconds": 2.22}`. The
rehearsal substitutes the owner unlock (a fixed access epoch) and is not the
locked-device acceptance; `scripts/tests/test_unit_a_voice_typed_question.py`
covers the refusal, lock, cancel, expiry and replay behaviour instead.

**Evidence, photo lane (same day):** a third bundle
(`console-20260930-typed-photo`, app build `4d6d0f82…`, receipt
`/var/backups/sage-lantern-console-20260930T1120Z`) carried the photo-carrying
typed question; the deployed client and server bundles contain the typed photo
post, the installed gate is byte-identical to the reviewed release file
(`5781d194…`), and the rehearsal answered a typed photo question against the
live NPU vision lane with capture still impossible:
`{"question": "What colour fills this image? …", "answer": "The image is filled
with red.", "input_source": "typed", "photo_attached": true,
"captured_audio_files": [], "review_audio_files": [], "seconds": 4.42}`.

**Affects:** `scripts/lantern/unit-a-voice-loop.py` (`/question/start`,
`typed_ready`, the typed run path), `lib/device-state.ts` and the device screen
(`Type` beside `Ask`, notices instead of dead ends),
`scripts/lantern/build-console-bundle-local.py` and
`scripts/lantern/deploy-staged-console-bundle-local.py` (the anchored local
console lane), `scripts/lantern/plan-fresh-vision.py` (gate inventory and the
two reviewed sleep-drop-in variants), and the D/E/F replication playbook, which
must deploy the same bundle and record each unit's own receipts.

## D-020 — Portrait is the device default; landscape is the desk mode

**Decision (owner, 2026-09-30; trigger amended 2026-10-18):** a Lantern is
**portrait by default** — the handheld question-and-answer posture. It becomes a
**landscape Linux desktop when a USB keyboard is attached**; the mouse is
optional. The operator can force either state with the explicit
`sage-lantern-desktop-mode on|off|auto` control. Landscape is not the boot
default, and no orientation sensor is required to make the switch. The desktop
login is the `sage` account with the owner's Lantern password; D-022's fresh
operator-login gate still applies.

**History:** the original 30 September wording required a USB keyboard *and*
mouse. The owner corrected that on 18 October: the keyboard is the posture
signal, and a user may plug in a mouse after the desktop appears.

**Reason:** the primary interaction is hold-to-talk → read the answer, one
handed, and the console layout, the panel's native shape and the controls are
portrait; the desk work — browser, terminal, files, maps, video — is wide and
reads better landscape. Triggering on keyboard-and-mouse attachment matches the
posture change exactly, and physical orientation sensing is unverified, so
automatic rotation stays out of the supported path.

**Consequence:** portrait is the resting state; the keyboard-triggered switch rotates
display and touch together through the logical monitor. Still open and recorded
with this decision: the app's session hand-off across a mode switch (the unlock
token lives in browser memory, so the UI re-locks today), the physical
rotate-90-versus-rotate-270 check on the panel, and the product DSI panel's
rotation and touch acceptance under the internal-ribbon rule.

**Affects:** `scripts/lantern/cf-mode-supervisor.py`, `cf-output-mode.py`,
`cf-desktop-mode.sh`, `docs/lantern/CF_DISPLAY_LAYOUT.md`, and the public
hardware page's desktop section.

## D-021 — The product camera is the Radxa Camera 4K (IMX415), G/H and future

**Decision (owner, 2026-10-01):** the **Radxa Camera 4K (IMX415)** is the
product-line camera — G/H **and future units** — **conditional on it proving
out on the ROCK 5B+**: overlay, internal CSI ribbon and capture verified on a
physical board. C–F keep the four ordered **IMX519** modules as their camera
kit. This answers the question the camera-island re-cut was held on.

**Reason:** the IMX415 driver family is present in the Rockchip kernel and
Radxa publishes a rock-5b-plus cam0/cam1 pattern for the camera; the IMX519
has no in-kernel driver, so its port work stays a later upgrade path. The
owner's direction closes "G/H-only or product camera" in favour of product.

**Consequence:** the camera-island re-cut targets the IMX415, not the IMX519;
barrel, board and FPC dimensions still come from connector confirmation
against the delivered board revision — never from vendor CAD. IMX519 remains
the C–F kit. If the IMX415 does not bring up on the 5B+, this decision
re-opens before any enclosure cut.

**Affects:** [CF_INTERNAL_PANEL_CAMERA_RESEARCH.md](CF_INTERNAL_PANEL_CAMERA_RESEARCH.md),
[HARDWARE_BOM.md](HARDWARE_BOM.md), the G/H hardware task, and the hardware
workstream via Flint.

## D-022 — Docked desktop requires a login

**Decision (owner, 2026-10-01):** entering the landscape desk mode **requires
a login** (the SAGE/Lantern account) — the operator is not carried into the
desktop without authenticating. Public documentation of this ("on the site
etc") is deliberately deferred to its own pass.

**Reason:** the desktop is the wide-work posture (browser, terminal, files)
and should sit behind the device's own authentication. The unlock token living
only in browser memory, so a mode switch re-locks the UI, is therefore the
intended behaviour rather than a defect to bridge.

**Consequence:** the RAM-only session property stays; running conversation
state should survive the portrait↔docked switch without the switch itself
unlocking the UI. PIN recovery flows (locked-out recovery, etc.) are
owner-scoped to the later **UAT/UI/UX pass** and are recorded as deferred, not
built into the current lanes.

**Affects:** `docs/lantern/CF_DISPLAY_LAYOUT.md`, the mode-switch continuity
item in the C–F device-UX task, the public hardware page's desktop section
(later, when documentation catches up), and the UAT/UI/UX scope.

## D-023 — The runtime wheel is the unit of deployment

**Decision (owner, 2026-10-11):** a deployment ships the **runtime wheel**, and
that wheel is built from the whole of `software/runtime`. Whatever the package
tree holds at build time is what a unit receives, including uncommitted work
from lanes unrelated to the change being deployed. This is **accepted**: the
wheel is the unit of deployment, and no per-file filtering is applied when
building or installing it.

**Reason:** the coupling is real rather than accidental. The measured case is
the 10 October voice deployment, whose wheel carried eleven files of other
lanes' work in progress. A clean replacement wheel was built and validated
against the installed wheel (169 tests) but never applied, and two later wheels
shipped the same way. Filtering the tree at build time would have to keep
feature dependencies that look stray but are not — the audio lane's
`active_conversation` import reaches an untracked conversation-lane file — so a
filter is a per-release judgement, not a rule the build can apply.

**Consequence:** the commission gate does **not** cover the wheel, and a runtime
byte change owes no fresh capture. The admission contract is four files —
`/etc/sage-lantern/appliance-bindings.json`,
`/etc/sage-lantern/audio-capture-alsa.conf`, `/etc/sage-lantern/lantern.toml` and
`/usr/bin/arecord` — and reviewed code churn in the installed package or the voice
loop is deliberately excluded from it (D-018/D-019: optional capability churn
never fails closed). An earlier revision of this note claimed the gate hashes
every `sage_lantern/*.py` and owes one capture; that was wrong. Measured on unit C
on 11 October 2026 by installing a changed `adapters.py`: `admission_verified()`
was true before and after, and diffing the commissioned receipt against the live
snapshot showed `audio`, `boot_id`, `hardware`, `inputs`, `services` and `unit_id`
all identical, with exactly one moved subkey — the package's `adapters.py` under
the audit-only `code` map. Because no gate sits behind the wheel, the review duty
is the whole control: reviewing what a deployment changes means reading the wheel
contents, not trusting the commit range that motivated it.

*A caution for the next reader:* `capture_verified()` **does** move with code
churn — it requires the entire snapshot to match the receipt — but it also carries
a 600-second TTL, which makes it false for most of any day. On unit C it was
already false from expiry (10,425 s past) when the wheel above was installed, so
its `false` is not evidence that a deploy invalidated anything. Check
`admission_verified()`, and check the expiry, before blaming a change.

**Affects:** [REAL_UNIT_PROVISIONING.md](REAL_UNIT_PROVISIONING.md) step 3,
[COLD_RUNTIME_UPDATE.md](COLD_RUNTIME_UPDATE.md), and the deployment runbook at
`work/ember-voice-playback-20261010/REDEPLOY-RUNBOOK.md`.

## D-024 — Unit C production chat runs the V79.6 3B token engine with response guards

**Decision (owner, 2026-10-18):** Unit C's production chat port 8081 runs the
V79.6 3B RKLLM model on the token engine with deterministic interval and status
response guards. The 2B prompt-string service remains the rollback. The
client-facing model id stays `lantern-native5-2b`, the vision fallback stays
8082, and action dispatch remains with SAGE/OpenClaw.

**Reason:** V79.6 fixes the three measured English failures and keeps 28/28
action formatting. The prompt-string engine cannot initialize the 3B model: the
RKNPU driver refuses the 3,085,697,024-byte IOVA mapping even with the 2B
stopped. The token engine loads the same artifact and measured 625.1 ms median
TTFT and 7.82 tok/s decode. The token bridge supplies tokenizer-encoded
`input_ids`; the guards correct the raw model's E13 interval drift and S03_B
absence clause.

**Consequence:** the 3B is the resident chat lane; the 2B remains rollback.
The 16k 3B profile was verified with the vision lane active. The device
artifact is 16k context (RKLLM toolkit cap); the host model is 32k. The live
service is configured at `--context 16384 --max-new-tokens 8192`; explicit
long-form requests are buffered and paragraph counts normalized. The guards
are response-side checks, not an authorization boundary. Rollback removes the
drop-in and restarts the 2B unit; the 2B model file is untouched. Raw-model
caveats and the served caps must travel with any release note.

The production guard revision is final-reviewed at `response_guards.py`
`07d64238…` and `rkllm_server.py` `b568af7d…`. It binds write receipts to the
preceding action and tool identity, refuses another owner's private memory
without a grant, and buffers only the first assistant turn after a recall
receipt; later ordinary chat remains token-streamed. Long-form requests are
buffered and normalized to the requested paragraph count. The SAGE/OpenClaw
executor still owns the authenticated SAGE call; the runtime dispatcher owns
request canonicalization and one-shot dispatch (D-026).

**Affects:** [software/npu/README.md](../../software/npu/README.md),
[3B_MODEL_PRODUCTION.md](3B_MODEL_PRODUCTION.md),
`/etc/systemd/system/sage-lantern-llm.service.d/50-v79.6-token.conf`, and the
RK3588 performance notes.

## D-025 — Meshticulum 1.0.0-alpha is public; Lantern acceptance remains separate

**Decision (owner, 2026-10-17):** Meshticulum 1.0.0-alpha is published at
<https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha> from source
`673eb60d24dc60f67371e24bc5c9caeec3a68508`, package `1.0.0a1`. Publication does
not qualify Lantern private/native production, field range, end-to-end
messaging or upstream superiority.

**Reason:** Meshticulum is the public protocol/transport project; Lantern is
the product deployment. The published results are near-bench application
goodput (GFSK250 about 2.0 kB/s; one LoRa SF7/BW500 run at 1.07 kB/s) and do not
transfer to field or modulation-equivalence claims.

**Consequence:** docs and marketing link to the public release and keep
kB/s/kbit/s, GFSK versus LoRa and near-bench scope explicit. Lantern delivery
and read receipts, private/native qualification and any Lantern alpha
publication remain separate gates.

**Affects:** root `README.md`, the Meshticulum release notes and Lantern public
communications.

## D-026 — The canonical request, not the model call, is the execution input

**Decision (2026-10-18):** Lantern memory actions execute only a canonical call
derived from the structured request and trusted routing. The model's
`<tool_call>` is evidence, never the execution source. The runtime dispatcher
invokes a trusted executor once, returns its receipt unchanged, and treats an
exception or malformed receipt as `uncertain` without automatic retry.

**Reason:** model text can invent tags, confidence, destinations or completion
claims; the action guard's request-derived call removes that authority. The
runtime still needs the owner session, admission check and SAGE client to
authorize and execute the action.

**Consequence:** `ConversationService.dispatch_save()` is the runtime boundary;
`SageRememberExecutor` is bound to the current owner. A grant for another
person's private memory cannot execute until an explicitly owner-bound executor
is supplied. A missing product UI/HTTP route is a separate integration gap, not
permission to let the model write directly.

**Affects:** [ACTION_GUARD.md](ACTION_GUARD.md),
`software/runtime/src/sage_lantern/action_guard.py`,
`software/runtime/src/sage_lantern/conversation_service.py`.

## D-027 — Storage epoch verification has two contracts

**Decision (2026-10-19):** `RootStorageSupervisor.verified_epoch()` is
uncached: every call re-derives the signed storage snapshot and the vault
identity under the supervisor's state fence. Native forward-secrecy gates use
it for their pre/post observations. `admission_epoch()` is the cached hot-path
contract: it reuses one verified pair for `LIVE_CHECK_SECONDS`, keyed on the
broker's whole in-process state, and capture leases and other admission checks
use it.

**Reason:** PR 3 forked before the 19 September replay-window fix and
reintroduced a fresh `verified_epoch()` for its native callers. Main had
already replaced per-check probing with a one-second reuse after a measured
incident: a dispatch wake paid about 165 signed snapshots inside a 10 s radio
budget, the local SAGE node returned 54 `503 Replay window saturated` answers,
`authorize_context` was denied and the A-to-B row was undelivered. Neither
contract is safe for both callers. The six native fences must see the vault
status before and after their own signed-row read; a cached pair would collapse
those observations. Admission checks must not pay a signed snapshot per check.

**Consequence:** Do not merge the two names or serve a cached result from
`verified_epoch()`. A native security fence that needs current vault state uses
`verified_epoch()`; a hot admission or lease check uses `admission_epoch()`.
`test_admission_checks_reuse_one_verification_per_window` guards the reuse, and
`test_six_fences_each_observe_native_pre_vault_and_paired_post_vault_status`
guards the fresh path.

**Affects:** `software/runtime/src/sage_lantern/storage_supervisor.py`,
`software/runtime/src/sage_lantern/mesh_daemon.py`,
`software/runtime/tests/test_storage_supervisor.py`,
`software/meshticulum-integration/tests/integrations/sage/test_forward_radio_native_fence.py`.

## D-028 — Main fleet is four ROCK 5B+ plus two Orange Pi bench units

**Decision (2026-10-19, owner):** Six is a planning number, not a fixed
requirement. The working main fleet is **four ROCK 5B+ units** — the two 12 GB
boards in hand plus **two 16 GB boards to order** — and **two Orange Pi 5 Plus
units** used as bench/development main units. The Orange Pi units do not need
internal DSI/CSI ribbon routing, internal 5 V panel power, final enclosure fit,
or final panel/touch/thermal acceptance. The two ROCK 5B 8 GB boards remain the
A/B Meshticulum relay units and are separate from the main fleet.

**Reason:** 16 GB is the preferred memory size in
`FINAL_COMPUTE_REQUIREMENT.md`; 12 GB is the measured floor. Four ROCK 5B+
units give two of each memory profile on the product path; two Orange Pi units
preserve a second software/bench platform without a second product-integration
programme. Buying four 16 GB boards is unnecessary for this fleet.

**Consequence:** Order **two** 16 GB boards, not four. The four AC005 adapters
already ordered are exactly right for four ROCK units; only two more DSI panels
are needed (four total), plus two coolers, four 512 GB NVMe drives, four IMX415
cameras and four radio HATs for the ROCK units. The 12 GB pair stays labelled
as the floor-spec regression pair. Orange Pi panel/camera results remain bench
evidence only. The product platform remains ROCK; the January bake-off is no
longer a product-platform decision.

**Affects:** `docs/lantern/FINAL_COMPUTE_REQUIREMENT.md`,
`hardware/BILL_OF_MATERIALS.md`,
`docs/lantern/SESSION_HANDOFF_20261019.md`, provisioning and fleet records.

## Open decisions

| ID | Decision needed | Evidence required | Owner | Target phase |
|---|---|---|---|---|
| O-001 | Exact audio-board host protocol and firmware role | Enumeration, board documentation, capture/playback test | Hardware + Mynah | Unit A audio |
| O-002 | PTT and encoder electrical interface | Safe GPIO mapping and debounce test | Hardware | Unit A controls |
| O-003 | Local STT/LLM/TTS stack and performance target | Measured timings and thermals on ROCK 5B+ | SAGE software + hardware evidence | Unit A voice |
| O-004 | MeshCore host protocol limits | Verified Heltec firmware/API, payload ceiling, fragmentation, retry, duplicate, and two-node evidence | Hardware evidence, then SAGE software | Radio |
| O-005 | Portable runtime target | Measured representative load and power-bank behavior | Product + hardware | Portable beta |
| O-006 | Enclosure architecture | Full measured stack, thermal and antenna evidence | Hardware/product design | After acceptance |
| O-007 | Whether wake word is required for this beta | PTT usability and audio-frontend capability | Product | After stable PTT |
