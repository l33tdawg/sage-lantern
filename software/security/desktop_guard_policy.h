/* SPDX-License-Identifier: GPL-2.0-only
 * Decision core shared by the disabled BPF candidate and userspace replay.
 * Root management is outside the UID 1000 boundary. Device aliases use dev_t,
 * never a path. Task storage names one exact kernel task through its pidfd.
 */
#ifndef LANTERN_DESKTOP_GUARD_POLICY_H
#define LANTERN_DESKTOP_GUARD_POLICY_H

struct lantern_guard_grant {
    unsigned long long executable_inode;
    unsigned int executable_device;
    unsigned int reserved;
};

static inline int lantern_enrolled(const struct lantern_guard_grant *grant)
{
    return grant && grant->executable_inode && !grant->reserved;
}

static inline int lantern_shell(const struct lantern_guard_grant *grant,
                               unsigned int device, unsigned long long inode)
{
    return lantern_enrolled(grant) &&
           device == grant->executable_device && inode == grant->executable_inode;
}

static inline int lantern_device_denied(unsigned int major, unsigned int minor,
                                       int exact_shell)
{
    /* Raw memory/ports and virtual input never belong to the compositor. */
    if ((major == 1 && (minor == 1 || minor == 2 || minor == 4)) ||
        (major == 10 && minor == 223))
        return 1;
    return !exact_shell && (major == 13 || major == 29 ||
        (major == 226 && minor < 128) || (major == 4 && minor < 64) ||
        (major == 5 && minor <= 1));
}

static inline int lantern_peer_denied(int caller_is_app, int same_group,
                                     int target_enrolled)
{
    return caller_is_app && !same_group && target_enrolled;
}
#endif
