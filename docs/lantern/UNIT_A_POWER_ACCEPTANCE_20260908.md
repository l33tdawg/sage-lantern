# Unit A power acceptance — read-only observation

Boot: `7bfcc4c6-b7d7-4485-9273-a2097c083bf5`.

Correction: `ConditionResult=no` did not establish a missing display policy.
Direct native metadata and hash checks show
`/etc/sage-lantern-display-power.enabled` exists, regular, single-link,
root:root 0644, 13 bytes, exactly `fresh unit-a\n`, SHA256
`b8d921dbb6507b9f3f11a644d0af10a63de8058a6f4961f4f94bd2d26e454faf`.
Its mtime/ctime are both `1788693721485072210` ns. The earlier reported absence
was an unsupported inference, not an observed removal or new policy change.

`sage-lantern-display-power.service` is loaded but disabled, inactive/dead,
MainPID 0, empty InvocationID, no drop-ins, and no current-boot journal entries.
Its existing unit requires that policy and starts the trusted display-session
supervisor. The historical reset plan records a successful explicit activation
on September 6, deliberately leaving boot enablement unchanged pending physical
screen-off/touch-wake acceptance. The observed disabled boot state is consistent
with that incomplete boot integration; the policy was not proved absent.

The Lantern screen-off/touch-wake supervisor is not running on this boot.
This does not prove the absence of unrelated OS/X11 blanking, nor does it prove
screen-off, touch wake, or owner-authentication behavior physically works.

The idle-suspend service is not-found; its installed helper and approval marker
are absent, as is `/run/sage-lantern-sleep-status.json`. Thus there is no current
Lantern controller capability evidence for deep suspend. Generic systemd sleep
targets exist, but are not a guarded Lantern suspend/resume acceptance result.

No power marker, enablement, unit, session, service or target was changed during
this inspection. Do not enable display or suspend incidentally during vision.
Vision review v4's absent-marker profile must reject this actual present-marker
state; do not fabricate an absent observation to run it.

Outstanding separate acceptance: review exact installed session/helper bindings,
validate non-blanking X11 access, explicitly restore/enable the approved display
controller when authorized, physically test screen-off/touch-wake/PIN and reboot,
and commission/test guarded deep suspend separately. Preserving inactive display
during another update is not completion of these power requirements.

## Exact installed restoration inputs

Read-only checks found these root:root, regular, single-link files, matching
the existing reviewed host sources byte-for-byte:

| Path | Mode | Bytes | SHA256 |
| --- | --- | ---: | --- |
| `/etc/systemd/system/sage-lantern-display-power.service` | 0644 | 807 | `98ab19a541dc075111a04fdbc5db7affc421df3955c7af61b127f4efea5cb989` |
| `/usr/local/libexec/sage-lantern-display-session` | 0755 | 27629 | `3a1caace513f2121d3e17080ac9aaa8966c9ffae69e9da10d45a50a104d39c9b` |
| `/usr/local/libexec/sage-lantern-display-power` | 0755 | 14826 | `944fc6ff1d598e2d19f003321e7b8dfa0d6925946dd1e83abd74fc05d734ecea` |

The hash-verified installed supervisor's `--check --unit-id unit-a` returned
exit 0, verified true, activated false: current local session 1, display `:0`,
launcher PID 1709. This was metadata-only: no X11 connection, blanking, capture,
service start, or enablement. The service has no job, no cgroup, and
NeedDaemonReload=no. A later power restoration must revalidate these observations,
perform the existing non-blanking `--check-x11`, then separately review activation
and boot enablement with physical wake/authentication acceptance. Deep-suspend
controller installation and capability validation remain a separate missing path.

## Vision preservation candidate v5

V4 is retained unchanged and rejects a present policy. V5 adds only an explicit
present-policy variant selected by its exact hash in the expected file inventory.
It requires the exact 13-byte fresh-unit-A policy, full SHA256, root:root 0644,
single-link regular leaf, trusted ancestors, no-follow descriptor reads, and
stable inode/metadata before and after reading. The file remains covered by the
maintenance inventory's repeated hash/metadata checks. Missing or altered policy
cannot fall back to the absent-policy profile after collection.

Both profiles require the exact display unit disabled/inactive/dead with no job,
PID, invocation or restart, and an actually empty canonical cgroup. Policy,
unit state and disabled status are rechecked after the empty-tree observation.
The temporary update guard can be released, but the display service is neither
started nor enabled; its existing policy is not changed. Negative tests cover
wrong policy/hash/owner/group/mode, links, in-read replacement, post-read
substitution/removal, enablement and process-state races. This is preservation,
not power activation or proof of complete power settings.
