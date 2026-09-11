# Unified Discover — requested product behavior

One user-facing Discover panel combines saved contacts, nearby radio observations
and available SAGE directory results. Users must not choose between a radio search
and an identity directory before finding a person.

Search supports a person's display name, device display name, and a precise
SAGE-node/device identity combination. Typing is primary; speech is optional.
Names may repeat or change. Routing and recipient authentication continue to use
verified cryptographic identities, never a display-name match alone.

The conversation's `maya@river-village/lantern-7` example illustrates a readable
hierarchy only. It is NOT a reserved namespace, implemented address format or
protocol decision. Reuse existing SAGE identity and signed binding fields after
source review. A human-readable address does not imply physical location.

## White-pages browsing and shared households

The agreed product direction is a white-pages-style directory: browse a city or
region, narrow to a town, village or community, then find a household or person.
These are adaptable locality labels, not a mandatory city-to-town administrative
hierarchy. Direct typed search remains available at every level.

A Lantern can represent an individual, household or shared community, like a
shared landline rather than necessarily a personal mobile phone. First-run and
Settings should support that distinction and optional person, family/household,
device and locality labels. A household listing does not establish separate
authenticated accounts for each named member.

Offer both an accessible list and a tappable visual directory inspired by SAGE's
federation connectome. Selecting a node opens a profile card with the voluntarily
published person/household name, device label, locality, stable identity hint and
availability evidence so the sender can confirm the intended recipient. Graph
links must distinguish directory grouping, verified identity bindings and actual
radio/federation connectivity; layout is not a physical map or proof of reachability.

All additional profile and locality fields are optional and private by default.
Publishing requires explicit consent about which fields are shared. Never infer
or publish a precise home address, GPS position or household member list from
radio observations. Cached/offline browsing must retain provenance, age and
visibility restrictions, with stale or unavailable sources clearly indicated.

This is a roadmap requirement, not functionality already available on A or B.

Private profile storage must also be private at rest. Existing local
`DeviceProfileStore` uses a permission-restricted plaintext file; adding family,
person or locality fields there is not sufficient. Keep sensitive extended
profiles behind the unlocked encrypted store and audit status serializers so
private fields cannot leak through a generic profile response. Existing SAGE
operational organization/department/node fields are not geographic fields.

Use a separate, explicitly consented listing projection for publication, bound
to the private profile revision and exact `(chain_id, agent_id)` recipient.
Reuse the existing `agent_id@chain_id` identity address. Profile grouping labels
do not change this address or certify residence. Publication needs expiry,
withdrawal and audience controls; changing private fields must not silently
refresh a publicly shared listing.

## Evidence behind discovery results

- Radio hardware present: USB metadata only, not reachable peers.
- Radio contact: saved companion record, not proof of recent reception.
- Nearby observation: bounded, fresh radio evidence with observation time.
- Directory identity: authenticated, permitted listing with source and expiry.
- Reachable: supported by actual route/receipt evidence, not mere discoverability.
- Last seen or availability unknown: explicit when fresh evidence is missing.

Only merge results when verified identity bindings establish that they are the
same recipient. Unbound radio advertisements remain device observations, not
verified people. Never merge by name, six-byte key prefix or an untrusted claim.
Disambiguate duplicate names with device/node labels and a short stable identity
identifier. Confirm the selected recipient before sending.

## Privacy and offline behavior

Directory listing is opt-in. Local device naming does not automatically publish a
person or turn on advertisements. Explain what each visibility setting exposes.
Respect revocation, renamed records, expiration and disconnected directory sources.
Offline searches use available cached, signed records and clearly state their age;
an unavailable provider is not an empty successful search.

## Delivery gates

1. Inspect actual SAGE directory and radio schemas; define verified join fields.
2. Implement bounded authenticated search/pagination and typed UI, without exposing
   private names or queries to unauthorized radio or public-directory consumers.
3. Attach live radio telemetry through the existing exclusive radio owner, not a
   second reader competing for the serial port.
4. Test duplicate names, impersonating advertisements, stale/revoked records,
   disconnected providers, relay evidence and directory visibility changes.
5. Prove A–B discovery and delivery over real radio independently of host tests.

Current state: Nearby is a placeholder. Contact UI receives only `id` and `name`;
typed filtering covers loaded contacts, not full directory search. A's latest
audit has messaging/dispatch disabled, zero configured peers and no open radio
descriptor. No unified discovery or over-air acceptance is claimed.
