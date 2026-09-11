# BASIC restore: DynamicUser portability

The native camera failure was traced to the frozen restore helper's mandatory
`pwd.getpwnam('sage-lantern-camera')` lookup. The camera was active under systemd
`DynamicUser=yes`, but this host did not resolve that name through NSS. This
finding does not establish the cause of earlier auth/status start failures.

The camera checkpoint diagnostic now requires a separately pinned positive
process UID, PID, start ticks and invocation, together with repeated live
systemd `DynamicUser=yes`, exact `User=sage-lantern-camera`, active state and
canonical cgroup. It verifies exact executable/argv, executable inode and
process identity twice. Full frozen configuration/hash/guard validation still
runs afterward. No generic UID fallback or account/NSS mutation is introduced.

Provisioning/restore regression coverage simulates an unavailable NSS name;
the camera-only path must succeed with all these proofs, and reject changed
UID, invocation, cgroup, executable, arguments, inode, or DynamicUser policy.
Other services retain their original identity checks. A future reprovision
must not assume that every DynamicUser name is NSS-resolvable or persist its
ephemeral UID across service restarts. This checkpoint binds one live instance.

This is read-only diagnosis, not permission to start another service. The
separate final-display continuation completed on Unit A in native session24093;
serving vault keys remain resident, and cryptographic relocking is unfinished.

## Clean provisioning follow-up

Source now includes `software/runtime/src/sage_lantern/camera_identity.py` and
uses it from `scripts/lantern/install-camera-local.sh` after fresh installation.
The verifier checks the reviewed unit/helper bytes, live DynamicUser process,
owned loopback listener and health between matching observations. It does not
depend on NSS, persist a temporary UID, or capture a photo. This utility is not
installed on A yet; the next canonical release must rebuild and include the
runtime and installer together. Unit B acceptance still requires actual native
provisioning and camera checks, not these source tests or A's recovery receipts.
