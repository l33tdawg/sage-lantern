# Fresh independent identity setup

2026-09-06. Source implementation, not native acceptance. Real cryptographic keys
were generated only in disposable integration-test homes, removed by test cleanup.
No appliance keys/state, staged binary, services or public enrollment were touched.

## Actual boundary

The existing `sage-gui setup` is a general-purpose browser wizard, not a bounded
appliance initializer. `serve` previously owned genesis generation but also starts
services. New `init-lantern-private` calls that same
`initCometBFTConfigWithBootstrap` without `serve`, database startup, network
listeners, TLS generation, public enrollment or PIN handling. It mints a private
chain through SAGE's existing random chain-ID mechanism and dual Root/application
signed genesis. The local application profile is **Companion**, not generic
Member; this requires the explicitly named consent flag. The separate public key
is only a key: no public authority has admitted it or granted proposer rights.

Paths, all on the target:
- Root seed: `/var/lib/sage-lantern/sage-personal/agent.key`.
- Private application seed: `/var/lib/sage-lantern/identity/lantern-agent.key`.
- Unenrolled public seed: `/var/lib/sage-lantern/identity/lantern-public.key`.
- Private genesis, validator/node keys and state:
  `/var/lib/sage-lantern/sage-personal/data/cometbft/{config,data}`.

The native command requires Linux ARM64, nonroot `sage-lantern`, exact fixed home,
prepared matching unit ID, private listener policy and independently reviewed
initial config. Root/app/public public IDs are compared for distinctness; random
independent generation is not an audit of every other appliance's identity.
Genesis is verified using SAGE's existing vendored-genesis verifier. Output is
public IDs plus actual genesis chain ID, with `local_enrollment_committed`,
`public_enrolled`, `vault_initialized`, `ready`, `provisioned` all false.

## Operator phase between base and app

Prepare service-owned 0600 `sage-personal/config.yaml` in its 0700 directory.
Its initial policy must include these fields; preserve separately reviewed
embedding/media settings rather than replacing them with this excerpt:

```yaml
data_dir: /var/lib/sage-lantern/sage-personal/data
agent_key_file: /var/lib/sage-lantern/sage-personal/agent.key
federation:
  enabled: false
quorum:
  enabled: false
voter:
  enabled: true
vendored_agent_bootstrap:
  agent_key_file: /var/lib/sage-lantern/identity/lantern-agent.key
  home_domain: local-field-knowledge
  clearance: 1
```

The domain/clearance are an explicit reviewed local policy choice. Do not supply
a copied `chain_id`; the new genesis determines it. No secret values are inputs.

Plan only:

```sh
python3 scripts/lantern/provision-fresh-appliance.py \
  --output "$CANONICAL_OUTPUT" --manifest-sha256 "$REVIEWED_MANIFEST_SHA256" \
  --unit-id unit-b --phase identity \
  --identity-config-sha256 "$REVIEWED_INITIAL_CONFIG_SHA256" \
  --owner-approved-companion-bootstrap
```

Only on the authorized native target, `--execute` verifies the prepared unit
binding, installed binary against the canonical payload, and initial config hash,
then runs `sage-gui init-lantern-private --owner-approved-companion-bootstrap unit-b`
as `sage-lantern` with a cleared environment and fixed loopback policy. No key is
passed on argv or copied from the stage. The matching binary must contain the
new command; old releases fail instead of falling back to `serve`.

Existing identity directory contents other than the preparer's requirements text,
existing state, redirected paths and partial initialization are rejected. The
ordinary SAGE instance lock serializes against `serve`; a durable exclusive
`lantern-initialization-started` claim fences retries, including partial failures.
Preserve partial state for reconciliation—never automatically remove that claim
or regenerate keys. It is not a success or readiness marker.

## Minimal ordered flow and consent boundary

For the existing operator-provisioned appliance flow, use one path:

1. Reviewed base install and independent unit metadata preparation.
2. Operator, acting with owner authorization, reviews initial policy/config and
   runs the explicit `identity-config` phase on that unit. No touchscreen UI is needed
   before this stage, and no identity is copied from another appliance.
3. The runner consumes the initializer's actual public output and installs bound
   protected configuration automatically; then install the locked app/auth stack.
   No chain-ID hand editing or voice-ready marker is needed for SAGE.
4. Start private SAGE/auth under the listener and initialized-file gates. The
   existing on-device walkthrough names the device and obtains the owner's PIN
   and recovery-backup confirmation. Name and PIN do not derive crypto identity.
5. Verify encrypted unlock, committed local enrollment and protected storage
   before enabling dependent voice/mesh workflows. Public enrollment is separate.

This is operator-authorized identity creation followed by owner vault setup,
**not** proof the touchscreen owner personally approved identity creation. The
CLI flag expresses that operator action; it is not an authenticated browser grant.
It avoids a redundant pre-initialization UI and requires no general Root proxy.

Only if product policy requires the owner's first touchscreen action to authorize
key creation must ordering change: install the locked app shell first, then use a
separate narrow root bootstrap broker that works while SAGE/auth are unavailable.
That broker would enforce native unit/release/freshness and single-use consent,
invoke this initializer as `sage-lantern`, and expose only sanitized progress.
It cannot depend on the SAGE PIN verifier before the keys/vault exist. That
alternative broker is **not implemented** here; do not route unauthenticated
browser flags into the operator runner or add another wizard merely to disguise
this missing authorization boundary.

## Remaining owner and integration gates

Owner-initiated on-device key creation, if selected instead of operator
commissioning, still needs the root-mediated handoff described above. No such
endpoint or UI route is added here. Mutable
device naming remains `DeviceProfile`; rename changes no keys, genesis or public
directory. This command does not mark that profile configured.

The `identity-config` runner automatically binds the actual private genesis chain
ID in protected runtime config (see `IDENTITY_CONFIG_COMMISSIONING.md`). The
identity-only operator option still leaves configuration as a separate step.
Install app/auth, then start personal SAGE through its
enforced private listener gates. Owner-selected PIN and recovery confirmation
remain necessary; normal unlock still fails with encryption off. Verify local
enrollment/consensus readiness after startup. Public gateway enrollment, directory
discoverability, radio identity/transport and voice activation are separate;
neither a key nor this phase's completion proves them.

## Real-generator integration evidence

`cmd/sage-gui/lantern_fresh_init_integration_test.go` in SAGE now invokes the actual
genesis and Ed25519 generators for two disposable temporary homes. It verifies:

- Six distinct Root/private/public identities and two distinct actual genesis
  chain IDs; generated seeds are 32-byte private regular 0600 files.
- Both bootstrap signatures, Root/application/domain/clearance/capability binding;
  wrong-chain and tampered Root/application signatures are rejected.
- SAGE's real existing-genesis startup preflight checks local validator/node/state
  and profile bindings; repeated config loads retain private listener policy and
  recover the authoritative chain ID in memory.
- Re-initialization rejects without changing keys, genesis, signing state or YAML;
  no runtime database, Badger store or certificates are created.

Passed locally with `GOPROXY=off GOSUMDB=off go test ./cmd/sage-gui
-run '^TestLanternFreshInitRealGenesisIntegration$' -count=1`. Other guard tests
retain injected delegates. No test starts `serve`, a listener, consensus, or an
appliance service. This proves the offline generator/config assumptions, **not**
committed enrollment, native ARM64 behavior, power-loss durability, PIN encryption
or full first-boot acceptance. Those remain separate acceptance tests.
