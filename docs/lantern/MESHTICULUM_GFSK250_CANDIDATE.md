# GFSK250 source integration

This candidate consumes Meshticulum commit
`07717f0067e2ea185ab594550301ad0cd426443d`. Both Python dependency pins and
the source-correspondence lock name that revision. The reusable profile,
drivers, capability protocol and firmware remain in Meshticulum.

Lantern adds the explicit `gfsk250` profile with the same finite lease and
per-run attempt/airtime ceilings as its existing GFSK125 choice. Grants bind
the new profile JSON schema and modem marker. The Node selects
`Gfsk250SerialWorker` for A and `Gfsk250HATWorker` for C. The serial worker
requires an exact firmware identity and a matching current-boot capability
reply before ARM. Status fallback preserves the same two-second original
read deadline and accepts only exact unsupported-opcode refusals.

The compiled A image is `mtb1-def16514fc`, 391040 bytes, SHA-256
`c865685db18dc016d9b0c7f16b83df79ccf94be4a0dd43a847c4d2d6c30e6c4f`.
It has not been installed by this candidate. Default firmware observations,
GFSK125/LoRa behavior and all retained trial debt are preserved. C uses Python
SPI drivers; the existing GPIO/SPI ABI check needs no rebuild because its
layout, pins and 1 MHz clock are unchanged. A future runtime bundle must
include the new core modules and exact source lock; the existing builder
already collects the complete bench package.

The distinct native kind is `c-gfsk250-mb1-bundle100k-v1`. Its owner, grant,
context, provision/approval, serving and recipient verification paths bind
the new modem while retaining the existing authenticated MB1 representation.
The portable runner keeps five parts, 1350 seconds whole, 1355 seconds phase,
180 seconds per part, and the full 2560-attempt/600000000-us additions for
each of A/C. B receives no additional radio allowance. Legacy kinds keep
their existing behavior and descriptor rules.

`gfsk250_mb1_bundle.PHYSICAL_BINDINGS` keeps the scope, actual preceding
history receipt and preceding parent unbound until the owner supplies the
reviewed evidence. Runtime and manifest hashes are external immutable owner
pins; included source never contains its own runtime hash. Missing bindings
refuse port opening and controller enrollment.

Build the opt-in runtime with `build_runtime.py --radio-profile gfsk250`.
Its hash-verified read-only inspection selects the 250 worker for A. The
runner requires that first in-window inspection to prove the same firmware
boot, capability and required zero counters before capture/claim enrollment.
Expected zero counters are readiness conditions, not earlier observations.

`prepare_public_gfsk250_mb1_bundle_fixture.py --baseline-fixture ...
--baseline-fixture-sha256 ...` verifies and reconstructs the previous signed
100000-byte file, then signs a fresh manifest namespace. The runner binds
both fixture hashes and requires the same useful-file SHA, a new namespace,
and all five recipient pieces absent before transmission. Earlier cached
delivery cannot count as a new physical run. Hardware actions and final
scope binding remain with the current device owner; these source controls
do not establish physical speed, range or alpha qualification.
