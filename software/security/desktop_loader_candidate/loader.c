/* SPDX-License-Identifier: GPL-2.0-only
 * Actual sequencing core, shared by the source-only backend and fault replay.
 * There is no installed caller, live entry point, service or activation file.
 */
#include "loader.h"
#include <errno.h>
#include <string.h>

const char *const lantern_sections[LANTERN_HOOK_COUNT] = {
    "lsm/file_open", "lsm/file_receive", "lsm/file_permission", "lsm/file_ioctl",
    "lsm/mmap_file", "lsm/file_mprotect", "lsm/ptrace_access_check", "lsm/task_kill",
    "lsm/task_alloc", "lsm/bprm_check_security", "lsm/task_fix_setuid",
    "lsm/capable", "lsm/bpf"
};
static int present(const unsigned char *bytes, size_t n)
{
    unsigned char any = 0;
    while (n--) any |= *bytes++;
    return any != 0;
}
static int complete(const struct lantern_ops *o)
{
    return o && o->prepare && o->load_map && o->attach_pin && o->attest &&
        o->spawn_stopped && o->stopped && o->insert && o->lookup && o->freeze &&
        o->frozen && o->record_attempt && o->release && o->fence;
}
void lantern_hold(struct lantern_loader *s, const struct lantern_ops *o, void *ctx)
{
    int released;
    if (!s) return;
    released = s->release_attempted != 0;
    /* Fence admission before callbacks. Never unpin on shutdown or failure. */
    s->phase = LANTERN_HELD;
    if (o && o->fence) o->fence(ctx, &s->child, released);
}
static int failed(struct lantern_loader *s, const struct lantern_ops *o, void *ctx)
{
    lantern_hold(s, o, ctx);
    return -EPERM;
}
static int identities(const struct lantern_loader *s)
{
    unsigned i, j;
    if (!s->map_id || s->pinned != LANTERN_HOOK_COUNT) return 0;
    for (i = 0; i < LANTERN_HOOK_COUNT; i++) {
        if (!s->links[i].link_id || !s->links[i].program_id ||
            s->links[i].map_id != s->map_id) return 0;
        for (j = 0; j < i; j++)
            if (s->links[i].link_id == s->links[j].link_id ||
                s->links[i].program_id == s->links[j].program_id) return 0;
    }
    return 1;
}
int lantern_prepare(struct lantern_loader *s, const struct lantern_policy *p,
                    const struct lantern_ops *o, void *ctx)
{
    unsigned i;
    if (!s || !p || !complete(o)) return -EINVAL;
    /* One loader lifetime, one object, one fresh map, one launch attempt. */
    if (s->phase != LANTERN_NEW || s->pinned || s->map_id || s->attempted)
        return failed(s, o, ctx);
    s->child.pidfd = -1;
    if (p->uid != 1000 || !lantern_enrolled(&p->executable) ||
        !present(p->object_sha256, 32) || !present(p->policy_sha256, 32) ||
        !present(p->shell_sha256, 32))
        return failed(s, o, ctx);
    s->policy = *p;
    if (o->prepare(ctx, p) || s->phase != LANTERN_NEW ||
        o->load_map(ctx, &s->map_id) || s->phase != LANTERN_NEW || !s->map_id)
        return failed(s, o, ctx);
    for (i = 0; i < LANTERN_HOOK_COUNT; i++) {
        if (o->attach_pin(ctx, i, &s->links[i]) || s->phase != LANTERN_NEW)
            return failed(s, o, ctx);
        s->pinned++;
    }
    if (!identities(s) || o->attest(ctx, s->links, s->map_id) || s->phase != LANTERN_NEW)
        return failed(s, o, ctx);
    s->phase = LANTERN_PINNED;
    return 0;
}
static int grant_matches(struct lantern_loader *s, const struct lantern_ops *o, void *ctx)
{
    struct lantern_guard_grant got = {0};
    return !o->lookup(ctx, s->child.pidfd, &got) &&
        got.executable_inode == s->policy.executable.executable_inode &&
        got.executable_device == s->policy.executable.executable_device && !got.reserved;
}
int lantern_launch(struct lantern_loader *s, const struct lantern_ops *o, void *ctx)
{
    if (!s || !complete(o)) return -EINVAL;
    if (s->phase != LANTERN_PINNED || s->attempted || !identities(s) ||
        o->attest(ctx, s->links, s->map_id) || s->phase != LANTERN_PINNED)
        return failed(s, o, ctx);
    s->attempted = 1; /* Includes failed spawn: never retry/adopt a replacement. */
    if (o->spawn_stopped(ctx, &s->child) || s->phase != LANTERN_PINNED || s->child.pidfd < 0 ||
        s->child.pid <= 1 || !s->child.start_ticks || !s->child.cookie ||
        o->stopped(ctx, &s->child) ||
        s->phase != LANTERN_PINNED)
        return failed(s, o, ctx);
    s->phase = LANTERN_CHILD_STOPPED;
    if (o->insert(ctx, s->child.pidfd, &s->policy.executable) ||
        s->phase != LANTERN_CHILD_STOPPED ||
        !grant_matches(s, o, ctx) || s->phase != LANTERN_CHILD_STOPPED)
        return failed(s, o, ctx);
    s->phase = LANTERN_GRANTED;
    if (o->freeze(ctx) || s->phase != LANTERN_GRANTED ||
        o->frozen(ctx, s->child.pidfd, &s->policy.executable) || s->phase != LANTERN_GRANTED ||
        !grant_matches(s, o, ctx) || s->phase != LANTERN_GRANTED)
        return failed(s, o, ctx);
    s->phase = LANTERN_FROZEN;
    if (o->stopped(ctx, &s->child) || s->phase != LANTERN_FROZEN ||
        o->attest(ctx, s->links, s->map_id) ||
        s->phase != LANTERN_FROZEN) return failed(s, o, ctx);
    if (o->record_attempt(ctx, &s->child) || s->phase != LANTERN_FROZEN)
        return failed(s, o, ctx);
    /* Once release was attempted, cancellation never kills a possibly running
     * session, even when the transport cannot confirm success. */
    s->release_attempted = 1;
    if (o->release(ctx, &s->child) || s->phase != LANTERN_FROZEN)
        return failed(s, o, ctx);
    s->phase = LANTERN_RELEASED;
    return 0; /* Not seat/READY/concealment/PIN acceptance. */
}
int lantern_recheck(struct lantern_loader *s, const struct lantern_ops *o, void *ctx)
{
    if (!s || !complete(o)) return -EINVAL;
    if (s->phase != LANTERN_RELEASED || !identities(s) ||
        o->attest(ctx, s->links, s->map_id) || s->phase != LANTERN_RELEASED ||
        !grant_matches(s, o, ctx) || s->phase != LANTERN_RELEASED ||
        o->frozen(ctx, s->child.pidfd, &s->policy.executable) ||
        s->phase != LANTERN_RELEASED) return failed(s, o, ctx);
    return 0;
}
