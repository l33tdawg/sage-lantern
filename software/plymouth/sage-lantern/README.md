# SAGE Lantern Plymouth theme

Static yellow title on navy, with a quiet light tagline. Text is rendered by
Plymouth's native script/Image.Text API; there are no generated bitmaps, browser
components or animations. Password prompts show only masked input; question and
boot-message callbacks remain available. Escape handling is not intercepted.

API checked against Debian's upstream Plymouth 22.02.122 source archive:
https://deb.debian.org/debian/pool/main/p/plymouth/plymouth_22.02.122.orig.tar.xz
(`themes/script/script.script`, `script-lib-image.c`, `script-lib-plymouth.c`,
and `scripts/plymouth-set-default-theme.in`). This is Plymouth script, not JS.
Theme selector documentation:
https://manpages.debian.org/bookworm/plymouth/plymouth-set-default-theme.1.en.html

## Plan and apply

On the target, as an ordinary operator:

```sh
python3 scripts/lantern/install-boot-theme.py
```

Default mode only checks prerequisites, hashes local theme/initramfs files,
queries the previous theme with no selector arguments, and prints a JSON plan.
It neither installs dependencies nor changes disk. An unsupported workstation
fails closed rather than pretending its plan represents the target.

After reviewing the printed digest and recovery paths, an authorized operator
may run `sudo python3 scripts/lantern/install-boot-theme.py --apply
--expected-manifest SOURCE_DIGEST --expected-plan PLAN_DIGEST` as one command,
using both digests from the reviewed JSON. The plan digest binds previous default,
kernel, original initramfs and config hashes as well as source. No apply has been
performed during development. CLI has no alternate target/staging-root apply.

Requirements are deliberately narrow: Linux aarch64, Debian-style tool/plugin
and initramfs-hook locations, existing running-kernel modules, kernel/initramfs,
text plugin/font, sufficient backup/rebuild space and a recoverable previous
theme. Unsupported variants require review, not bypassing checks. No existing
Lantern theme or backup is overwritten; repeat installation requires explicit
operator review. This is a one-time installer, not an upgrade mechanism.

## Recovery and limits

The fresh runner exposes an explicit release-bound `--phase boot-theme`.
Without `--execute` it validates the canonical release and prints the child
command. `--execute` alone runs the child's read-only target inspection.
Mutation additionally requires `--apply-boot-theme`, `--boot-theme-manifest`
and `--boot-theme-plan` copied from an independently reviewed child plan.
Other phase options and apt installation are rejected in this phase.

Inspection requires `unmkinitramfs` as well as the existing tools, plugins and
fonts; it does not install packages. Current `/proc/cmdline` is inspected and
hash-bound without exporting its contents. `splash` means only that the current
kernel requests Plymouth, not that Plymouth rendered. Missing, disabled,
unreadable or unsupported requests cannot apply. Next-boot configuration stays
`unknown`: no bootloader configuration is inferred or rewritten.

After regeneration, a private temporary extraction verifies exact theme,
daemon, script/label plugin, font and selected configuration bytes. Only the
explicit unmkinitramfs root/main layouts and canonical member paths are
supported; missing, ambiguous, symlinked or changed members fail with backups
retained and without APPLIED. Successful verification writes hashes only in
`verified-initramfs.json`, then APPLIED. Extraction uses the administrator-trusted
distribution tool on its own initramfs, never an uploaded arbitrary archive.
This is not an initramfs bootability or graphics-driver test.

Before selecting the theme, the installer writes a root-only recovery plan,
previous initramfs (hash checked) and previous Plymouth configuration if present
under `/var/lib/sage-lantern-boot-theme-backup`. Failure does not automatically
reboot or claim rollback succeeded. Consult `recovery.json`: select the recorded
previous default and regenerate the recorded kernel's initramfs using its
`recovery_commands`. If regeneration cannot work, an administrator must restore
the saved initramfs/configuration through the established recovery console and
verify permissions and hashes before rebooting. The record notes whether the
original config was absent. Backups may contain sensitive initramfs material;
never include this directory in a sterile image or public evidence.

No bootloader/SPI flash, kernel command-line edits, console/getty changes,
diagnostic suppression, service masking or reboot is performed. Initramfs-tools
may run existing distribution hooks; their behavior and installed tool integrity
are part of the target administrator's trust boundary. Do not run alongside
package/kernel updates. It is not a power-loss-atomic boot update.

Plymouth starts after firmware/bootloader and early kernel output. RK3588 early
console text is outside this theme. Existing boot configuration must already
start Plymouth with usable display drivers; this installer does not add splash
arguments. A successful command is not evidence of graphical rendering or a
bootable updated initramfs. Native acceptance must check display resolution,
fonts, long prompts, password/question handling, Escape diagnostic fallback and
recovery access before an operator-approved reboot. No native interpreter or
visual test was available on the development Mac; tests exercise the guarded
installer with temporary fixtures and mocked commands only.
