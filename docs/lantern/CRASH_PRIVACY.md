# Lantern OS crash-memory policy

Lantern must not persist process memory through the OS crash collector. The
per-unit tool is `scripts/lantern/configure-crash-privacy.py`. It checks by
default; applying it changes crash-reporting configuration without restarting
Lantern, its models, GNOME or the browser. Existing reports remain untouched.

This closes one OS persistence path. It does not establish the desktop login
boundary, browser profile privacy, secure deletion, or protected-storage
acceptance on C–F.

## Policy

| Surface | Required configuration |
| --- | --- |
| Kernel | Empty `kernel.core_pattern`, `kernel.core_uses_pid=0`, `fs.suid_dumpable=0` |
| Ubuntu collection and reporting | Apport, automatic-report paths/timer, forwarding socket/templates and Whoopsie masked; active instances stopped; `/etc/default/apport` has `enabled=0` |
| systemd collector | Socket/template masked; `Storage=none` and `ProcessSizeMax=0` |
| Lantern units | Explicit `LimitCORE=0:0` on every installed `sage-lantern-*.service` and the reviewed service inventory |
| New system/user services | `DefaultLimitCORE=0:0`; `user@.service` explicitly inherits a zero hard limit |
| New PAM sessions | Zero soft and hard core limits for ordinary accounts and root |

Linux ignores `RLIMIT_CORE` when the kernel pipes a dump to a collector. An empty
pattern disables the kernel dump only when `core_uses_pid` is also zero. See the
[Linux core manual](https://man7.org/linux/man-pages/man5/core.5.html).
`Storage=none` alone can still allow a temporary memory file while systemd
processes a backtrace; both storage and processing must be disabled. See
[systemd coredump configuration](https://www.freedesktop.org/software/systemd/man/latest/coredump.conf.html).

The tool writes only its own policy files, edits only Apport's `enabled` setting,
and preserves other defaults. It refuses linked, hardlinked, writable or
unexpected local configuration and reused backup directories. It records
original files, modes and collector states in a root-only backup receipt before
changing configuration. Existing enabled-unit links remain in place beneath
the masks, preserving their rollback state.

Apport's stop command resets the kernel core pattern. The tool therefore writes
the required live sysctls after stopping reporters. It reapplies only those three
settings, never the whole platform sysctl configuration. Read-back checks compare
both live values and the effective ordered boot sysctl configuration.

## Apply on each commissioned unit

Review the source from a verified release tree. The helper and this runbook are
included as support in successor appliance releases; they do not alter the
historical r17 manifest or receipt. Run before admitting private owner content
on a newly commissioned unit.

```sh
python3 scripts/lantern/configure-crash-privacy.py --plan
sudo python3 scripts/lantern/configure-crash-privacy.py --check
sudo python3 scripts/lantern/configure-crash-privacy.py --apply \
  --backup-dir /var/backups/sage-lantern-crash-privacy-YYYYMMDDTHHMMSSZ
sudo python3 scripts/lantern/configure-crash-privacy.py --check
```

Use a new timestamped backup directory. `--apply` requires root, ARM64 and an
existing commissioned Lantern receipt. It stops only crash collectors/reporters.
It does not unlock the owner session, run inference, capture media, suspend,
reboot, remove reports, or restart application services. No remote reporting is
invoked.

Exit 0 means the **OS configuration check passes**, not product acceptance.
Exit 1 means read-back failed. Exit 2 means the operation refused or could not
finish; inspect the backup receipt before retrying. After a partial apply, keep
the evidence and complete read-back or restore deliberately. Do not reuse the
backup directory or treat its existence as a successful application.

The check reads only configuration, systemd state and `/proc` resource-limit
metadata. For `/var/crash` and `/var/lib/systemd/coredump`, it counts directory
entries and bytes using `lstat`; it never opens or exports artifact payloads or
filenames. An inaccessible inventory is explicit. The report contains no process
command line, environment, owner key, journal body or memory image.

## Qualification still required

1. After qualified power is available, restart Lantern through its guarded
   lifecycle and start fresh GNOME/SSH sessions. Verify zero soft **and hard**
   core limits on desktop, browser, auth, native SAGE, voice and model processes.
   `daemon-reload` cannot retrofit existing process limits. The report names
   accounts still awaiting a fresh session; an empty or unreadable process
   sample does not count as evidence.
2. On the installed vendor kernel, crash a separate disposable synthetic process
   containing a canary. Verify that no OS dump/report appears and that the live
   policy remains intact. Never crash a real owner process or model for this
   test. Check again after a qualified reboot and relevant OS updates.
3. Qualify application-managed crash files separately, especially Chromium's
   profile/Crashpad and any desktop browser launched outside the Lantern helper.
   OS core controls do not prevent an application from writing its own minidump.
   Do not rely on an unverified Chromium command-line flag.
4. Resolve old-report retention/disposal with the owner. Applying this policy
   does not erase an existing report or establish whether it contains owner
   content. Do not copy a memory report into a support bundle. Metadata-only
   diagnostics may record bounded state, exit/signal codes and resource limits;
   ordinary application log content still needs its own privacy review.

For rollback, use the receipt's file map and original modes: restore each saved
configuration, remove only policy files that previously did not exist, and
remove only masks marked `absent` before installation. Reload systemd and restore
the previous live sysctls and collector states only under an explicit operator
decision; doing so can re-enable memory persistence or automatic reporting.
Do not delete unrelated masks, reports or backups. Existing process limits and
manager defaults need fresh-session checks in either direction.

Current C evidence and remaining security gates are linked from
[NEXT_SESSION](NEXT_SESSION.md) and the
[protected-storage audit](C_PROTECTED_STORAGE_AUDIT_20261001.md).

## C result — 1 October 2026

The policy was applied on C from hash-verified helper source
`ed0fe4824dd0e6028954470f54a58d76c37a9cf9a2ad809caa0b13f9c020254a`.
The application and independent read-back pass: all 11 collector/template masks,
the three live and ordered boot sysctls, and 17 present service/user-manager
configurations were checked. The two existing reports, totalling 30,607,285
bytes, remain unchanged by count and size; their payloads were not opened.

The boot ID and the main PIDs, invocation IDs and active states of personal
SAGE, auth, voice, both NPU models, fleet-admin and the desktop user manager
match their pre-application values. No Lantern/desktop service restart, owner
unlock, inference, capture, synthetic crash, suspend or reboot was performed.
The 5 V/4 A adapter remains last-known pending; no new delivery evidence was
provided in this session.

Existing `sage` and `sage-lantern` processes still need fresh-session hard-limit
qualification. Browser minidumps, the disposable vendor-kernel crash canary,
qualified reboot checks and old-report disposition remain open. The r17 release
and its historical receipt remain intact; this policy is a separate recorded
OS configuration change.

Backups and the application receipt are on C at
`/var/backups/sage-lantern-crash-privacy-20261001T112919Z/`.
The helper remains in its root-only operator staging directory:
`/var/lib/lantern-work/crash-privacy-20261001/configure-crash-privacy.py`.
Local evidence: [work/c-crash-privacy-20261001](../../work/c-crash-privacy-20261001/README.md).
Fourteen focused policy tests and 13 appliance-manifest tests pass, plus 37
manifest subtests. This is OS configuration evidence, not full privacy signoff.
