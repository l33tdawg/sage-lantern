# Sterile runtime release 0.2.0

## Current unpromoted local SDK checkpoint

Runtime source and installer now require `sage-agent-sdk==11.19.10+lantern.20260906`.
The old shipping bundle/lock are deliberately not promoted or regenerated yet;
the release verifier must reject that stale bundle. Final runtime rebuild waits
for `workflow_store.py` freeze and its required-module/import gate.

The separate SDK builder stages trusted `../sage/sdk/python` sources without
editing upstream version 11.19.10, pins build tools, and embeds original and
staged SHA256 manifests. It excludes non-source payloads and checks source drift:

```sh
python3 scripts/build-sdk-release.py --output /private/tmp/sdk-check-one
python3 scripts/build-sdk-release.py --output /private/tmp/sdk-check-two
cmp /private/tmp/sdk-check-one/*.whl /private/tmp/sdk-check-two/*.whl
```

Two actual frozen-source SDK builds matched SHA256
`d0b961c205c82b81832c298bcf3145267f074278ee21c20bc3b087afb9444cd6`.
Required signatures now include storage status, encrypted message submission,
pipe idempotency, and workflow get/put/list. These SDK APIs do not establish that
the upstream SAGE binary implements the endpoints or enforces encryption and
authorization. Capability remains unavailable/unverified pending native checks.
No old artifact is automatically deleted; promotion requires separate review.

## Historical bundled checkpoint (not current source acceptance)

This wheel contains 49 current runtime source modules, not identities, services,
configuration, models, UI, SDK source or an appliance installer. Runtime source
and pyproject digests are embedded in `sage_lantern/release-manifest.json`.

The final MeshCore source included is SHA-256
`247897c049da36416ebdb7f28b937ce4aa019ea0c8bda7d199576fae0cceb3f5`.
The wheel SHA-256 is
`21809cefad4566b45aed592bea66ee01c211f9add372ecba70981324ad04bccd`.
Two independent builds produced identical bytes. This establishes reproducibility
with the recorded build tools on this host, not every Python/platform toolchain.

## Rebuild without source-tree build debris

Use Python with build 1.4.0, setuptools 80.9.0 and wheel 0.45.1 already installed:

```sh
python3 scripts/build-runtime-release.py --output /tmp/lantern-release-check-one
python3 scripts/build-runtime-release.py --output /tmp/lantern-release-check-two
cmp /tmp/lantern-release-check-one/*.whl /tmp/lantern-release-check-two/*.whl
```

Output directories must be new. The builder snapshots only pyproject and direct
package `.py` files into temporary staging, fixes timestamps/hash seed, performs
an offline/no-isolation build, then rechecks both source bytes and module set.
Never promote a build that reports changed source. The build toolchain is pinned
but not bundled; do not resolve an arbitrary replacement toolchain implicitly.

After review, promote the generated wheel, retain old artifacts until explicit
cleanup approval, regenerate `software/stack.lock.json` with
`scripts/generate-stack-lock.py`, then run:

```sh
python3 scripts/verify-lantern-bundle.py
python3 scripts/verify-runtime-release.py --bundle
```

The integrity lock is not a cryptographic publisher signature. Its trust must
come from the reviewed release distribution. Source-to-wheel comparison rejects
stale payloads; the verifier also rejects extra packages, `.pth`, cache/key files
and unexpected wheel members. Ship the matching source snapshot and build/check
scripts with the bundle because the fresh installer invokes this gate before
claiming any installation root.

## Dependencies and honest capability status

Runtime dependencies are PyNaCl 1.6.2, cryptography 46.0.3 and the **separate,
unchanged** bundled sage-agent-sdk 11.19.10 wheel. The newly bundled cryptography
wheel is CPython 3.11 ABI3, manylinux 2.28 aarch64, matching Bookworm's glibc.
It is hash-locked along with the existing transitive wheelhouse. The SDK is not
represented as current upstream. Required base/conversation SDK method keyword
signatures are checked statically from its wheel and again after installation.

SDK 11.19.10 lacks `message_storage_status` and
`message_send(require_encrypted_storage=...)`. Both verification modes report
canonical messaging `available: false`, `verified: false`, with these gaps.
Even a future API-complete SDK would still require actual authenticated storage
and runtime-policy verification. Do not enable canonical messaging, claim
encryption, or substitute a different SDK solely because base imports pass.
Nothing here activates public sharing, radio, or messaging.

## Validation evidence and limits

- Both final builds matched the wheel digest above and included the frozen
  MeshCore response-27 fix.
- Full lock verification covered 95 artifacts; only the new runtime and
  cryptography wheels replaced/extended the previous locked file set. The old
  runtime 0.1.0 wheel was removed; other pre-existing artifact bytes were unchanged.
- Offline pip dry-resolution of the installer's complete pinned requirements
  succeeded for Linux aarch64/Python 3.11 using only local wheels.
- The final runtime and SDK wheels were installed with `--no-index --no-deps`
  into a disposable Mac/Python 3.12 venv. It reused host system-site dependencies;
  this is **not** a hermetic Linux installation. The checker imported 48 modules
  without constructing clients, hashed all 49 source files, checked dependency
  versions/SDK signatures, and CLI version entry points returned 0.2.0.
- `__main__.py` is hashed/syntax checked, not imported (it executes the CLI).
  Required-module imports detect the missing dependencies overlooked by the old
  wheel. Python 3.11 syntax is checked separately; native execution remains open.

Focused checks:

```sh
LANTERN_RELEASE_PYTHON=/path/to/validation-venv/bin/python \
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/tests -p test_runtime_release.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  scripts/tests/test_install_lantern_unit.py
```

The installed-import test skips unless an explicitly prepared validation Python
is supplied. The fresh installer runs the installed verifier unconditionally
after pip installation, before native builds proceed. No device installation,
service action, identity operation, connectivity test or readiness assertion
was performed for this release slice. Existing interrupted-install refusal and
manual-recovery behavior remain unchanged.
