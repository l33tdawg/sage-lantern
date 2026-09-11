# Lantern SSH management

Updated 12 September 2026. One dedicated operator key lives on the management
Mac. Each enrolled Lantern accepts its public key for the dedicated `sage`
maintenance account as well as the original `radxa` account. Device SSH host
keys and Lantern application identities remain unique.

## Maintenance account

On 12 September the owner asked for a dedicated maintenance login:

- Account `sage` (uid 1002) on both units, member of `sudo`, home
  `/home/sage`, bash login shell.
- Key-only SSH: the same management public key is installed in
  `/home/sage/.ssh/authorized_keys` (0700 directory, 0600 file, owned by `sage`).
- `sudo` requires the account password, exactly like `radxa`. No passwordless
  rule was added, and the root account stays locked.
- `radxa` login and its sudo rights are retained as a fallback; revoke the
  `sage` account by removing that line from `authorized_keys`, running
  `deluser sage sudo` or `userdel -r sage`, before rotating the key.
- `/Users/l33tdawg/.ssh/config.d/lantern-management.conf` now uses
  `User sage` for `lantern-a`, `lantern-b`, `sage-lantern-a` and
  `sage-lantern-b`; the previous version is kept beside it as
  `.before-sage-user-20260912`. Use `ssh -l radxa lantern-a` for the fallback.

## Current access

| Device | SSH command | Current address | Status |
|---|---|---|---|
| A | `ssh lantern-a` | `192.168.50.103` | `sage` key-only login and sudo verified 12 September |
| B | `ssh lantern-b` | `192.168.50.96` | `sage` key-only login and sudo verified 12 September |

The aliases `sage-lantern-a` and `sage-lantern-b` work equivalently. Addresses
reflect the current LAN assignment; update the appropriate `HostName` after
independently identifying a device at a new address.

- Private key: `/Users/l33tdawg/.ssh/id_ed25519_lantern_management` (0600).
- Public key: the same path with `.pub` appended.
- Public fingerprint: `SHA256:s0L2Kz6w9R2xxyVlzCydkgOrUl2cvMjcKCP0lv8chBc`.
- Key comment: `lantern-management-20260910`.
- Aliases: `/Users/l33tdawg/.ssh/config.d/lantern-management.conf`, included
  from the existing SSH config. Existing unrelated SSH entries are preserved.
- Host pins: `/Users/l33tdawg/.ssh/known_hosts_lantern` (0600).

The key has no passphrase so this management Mac can run unattended SSH jobs.
Its private material stays outside this repository and is never copied to a
Lantern or stored in SAGE. A private-key backup, if wanted, belongs in the
owner's encrypted credential backup. Both aliases disable agent forwarding,
password fallback and keyboard-interactive authentication, and require the
pinned Ed25519 host identity.

## Verified host identities

| Device | Ed25519 host fingerprint | Basis |
|---|---|---|
| A | `SHA256:+20m6SyHORKdfNS2wneEzGgZdLojWfQTm3kQeNJIPz0` | Existing authenticated connection and saved host key |
| B | `SHA256:vi/yQWb7M7HAk3Vmy3YhaQqE90S7YCAKRpKbhTpc8ag` | Accepted NVMe provisioning record in `UNIT_B_PROVISIONING_GATES.md` |

Both current network keys were compared with these recorded fingerprints
before writing the dedicated host-pin file. The general `known_hosts` file
still contains B's historical SD-image key; these aliases use the dedicated
file and do not silently erase that historical entry. An IP address or an
unverified `ssh-keyscan` response alone does not establish host identity.

## Installation and verification

Before adding a future Lantern, establish its identity through its console or
another already authenticated channel, record its unique host fingerprint and
create a per-device alias with the same `IdentityFile`. Pin that verified host
key before sending a bootstrap credential. Keep the existing authorized keys.
Never clone host keys or ship this operator's private key in an OS image.

Copy only the public key using an existing authenticated management session
or an interactive bootstrap login. For example, once the new alias and host
pin have been configured:

```sh
ssh-copy-id -i ~/.ssh/id_ed25519_lantern_management.pub \
  -o PasswordAuthentication=yes \
  -o PreferredAuthentications=publickey,password lantern-b
```

This command prompts locally for any needed existing account credential. It
does not put a password in command arguments or a file. If another key is the
bootstrap credential, explicitly select it instead. Do not use `-f` for routine
repeat installation: let `ssh-copy-id` detect an already authorized key.

Verify with a new transport, without password fallback, an SSH agent or an
existing multiplex connection:

```sh
ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none \
  -o IdentityAgent=none lantern-a 'hostname; id -un'
```

Use the corresponding alias for each device. Confirm the expected hostname,
`radxa` account, `.ssh` mode 0700, `authorized_keys` mode 0600 and the new public
fingerprint on the device. This key authorizes SSH login; it does not change
sudo policy or satisfy Lantern's separate owner-unlock/maintenance gates.

### Owner-authorized remote unlock

The owner can separately authorize entry of their device PIN over the existing
SSH connection. This was verified on A using its installed authentication
broker's normal `/unlock` and `/activate` flow, followed by the same presence
heartbeat used by the UI. No authentication code or lock settings change.

The bounded client `work/model-trial-20260910/npu-next/owner-unlock-session.py`
reads the PIN from a terminal with echo disabled and retains the resulting
session token only in memory. It expires its client session after 110 seconds,
ends when maintenance stops the auth service, and attempts to cancel its own
token on exit. It does not renew the configured idle timeout. Credentials and
tokens must not be added to scripts, logs, this document or SAGE. A successful
remote PIN unlock and subsequent full-model maintenance preflight were verified
on 10 September; repeated physical unlock trips are unnecessary for this
owner-authorized workflow.

Longer active recovery uses `owner-recovery-session.py`, bounded to ten minutes.
It sends the normal authenticated `/activity` call every 20 seconds as well as
presence, then cancels its own token on exit. Saved idle/lock policy stays intact.
The 110-second client is too short for cold-boot recovery: it expired during
reconciliation after the model started on 10 September. The explicit continuation
subsequently completed under the longer active session, preserving all failure
records and the archived supervisor. Neither client stores the PIN or token.

## Rotation and revocation

Generate a replacement key at a new local filename. Add its public key to each
device while retaining the old access, then verify fresh key-only sessions
with the replacement. Update the aliases only after those checks pass. Remove
the old public-key entry from every enrolled device by matching its key type
and full public-key blob, preserving all unrelated entries. Verify both that
the replacement succeeds and that the revoked key fails. Track offline devices
as pending; deleting a private key on the Mac does not revoke device access.

## Evidence and recovery

`work/ssh-management-20260910/` holds public fingerprints, authentication proof
and before/after service identities. A's existing key is preserved byte for
byte, all six production service identities were unchanged and the boot ID
matched. No SSH daemon restart or Lantern owner unlock was required.

Before this change, the local SSH config was saved as
`~/.ssh/config.before-lantern-management-20260910`; A's authorized keys were
saved on A as `~/.ssh/authorized_keys.before-lantern-management-20260910`, both
0600. These snapshots are historical: do not restore them over later changes
without reviewing the differences. To undo this enrollment, remove only the
matching management public-key entry and its local alias configuration.
