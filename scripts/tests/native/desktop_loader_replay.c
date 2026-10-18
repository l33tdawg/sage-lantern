/* SPDX-License-Identifier: GPL-2.0-only
 * Fault-inject the ACTUAL sequencing core; no BPF or process operations. */
#include "loader.h"
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

static unsigned checks;
#define CHECK(x) do { checks++; if (!(x)) { fprintf(stderr, "check failed line %d\n", __LINE__); return 1; } } while (0)
struct fake {
    struct lantern_loader *state;
    int call, fail_at, cancel_at, spawned, released, fenced, preserve;
    int post_fence_calls;
    int duplicate_link, duplicate_program, wrong_map, wrong_grant, dead_child;
    struct lantern_guard_grant grant;
};
static struct lantern_ops ops;
static int point(struct fake *f)
{
    if (f->fenced) f->post_fence_calls++;
    f->call++;
    if (f->call == f->cancel_at) lantern_hold(f->state, &ops, f);
    return f->call == f->fail_at ? -EPERM : 0;
}
static int prepare(void *v, const struct lantern_policy *p)
{ (void)p; return point(v); }
static int load(void *v, uint32_t *id)
{ *id = 42; return point(v); }
static int attach(void *v, unsigned i, struct lantern_binding *b)
{
    struct fake *f = v;
    *b = (struct lantern_binding){100 + i, 200 + i, 42};
    if (i && f->duplicate_link) b->link_id = 100;
    if (i && f->duplicate_program) b->program_id = 200;
    if (f->wrong_map) b->map_id = 43;
    return point(v);
}
static int attest(void *v, const struct lantern_binding *b, uint32_t id)
{ (void)b; (void)id; return point(v); }
static int spawn(void *v, struct lantern_child *c)
{
    struct fake *f = v; f->spawned++;
    *c = (struct lantern_child){7, 123, 99}; return point(v);
}
static int stopped(void *v, const struct lantern_child *c)
{ (void)c; struct fake *f = v; int result = point(v); return f->dead_child ? -ESRCH : result; }
static int insert(void *v, int fd, const struct lantern_guard_grant *g)
{ (void)fd; struct fake *f = v; f->grant = *g; return point(v); }
static int lookup(void *v, int fd, struct lantern_guard_grant *g)
{
    (void)fd; struct fake *f = v; *g = f->grant;
    if (f->wrong_grant) g->executable_inode++;
    return point(v);
}
static int freeze(void *v) { return point(v); }
static int frozen(void *v, int fd, const struct lantern_guard_grant *g)
{ (void)fd; (void)g; return point(v); }
static int release(void *v, const struct lantern_child *c)
{ (void)c; struct fake *f = v; f->released++; return point(v); }
static void fence(void *v, const struct lantern_child *c, int preserve)
{ (void)c; struct fake *f = v; f->fenced++; f->preserve |= preserve; }
static struct lantern_policy policy(void)
{
    struct lantern_policy p = {0}; p.uid = 1000;
    p.executable = (struct lantern_guard_grant){999, 12345, 0};
    p.object_sha256[0] = 1; p.policy_sha256[0] = 2; p.shell_sha256[0] = 3; return p;
}
static void init(struct fake *f, struct lantern_loader *s)
{ memset(f, 0, sizeof(*f)); memset(s, 0, sizeof(*s)); f->state = s; }
int main(void)
{
    struct lantern_loader s; struct fake f; struct lantern_policy p = policy();
    ops = (struct lantern_ops){prepare, load, attach, attest, spawn, stopped,
        insert, lookup, freeze, frozen, release, fence};
    init(&f, &s);
    CHECK(!lantern_prepare(&s, &p, &ops, &f)); CHECK(s.pinned == LANTERN_HOOK_COUNT);
    CHECK(!f.spawned); CHECK(!lantern_launch(&s, &ops, &f));
    CHECK(s.phase == LANTERN_RELEASED); CHECK(f.spawned == 1 && f.released == 1);
    CHECK(!lantern_recheck(&s, &ops, &f)); int max_calls = f.call;
    lantern_hold(&s, &ops, &f); lantern_hold(&s, &ops, &f);
    CHECK(s.phase == LANTERN_HELD && f.preserve);
    CHECK(lantern_launch(&s, &ops, &f) < 0 && f.spawned == 1);
    /* Every operation boundary fails closed. Once release may have happened,
     * fence preserves that uncertainty even after another repeated hold. */
    for (int cancel = 0; cancel < 2; cancel++) for (int n = 1; n <= max_calls; n++) {
        init(&f, &s); if (cancel) f.cancel_at = n; else f.fail_at = n;
        int result = lantern_prepare(&s, &p, &ops, &f);
        if (!result) result = lantern_launch(&s, &ops, &f);
        if (!result) result = lantern_recheck(&s, &ops, &f);
        CHECK(result < 0); CHECK(s.phase == LANTERN_HELD); CHECK(f.fenced);
        /* Cancellation inside one callback must prevent the next callback,
         * not merely be noticed at the end of a multi-operation chain. */
        CHECK(!f.post_fence_calls);
        CHECK(n > 16 || !f.spawned); CHECK(f.released <= 1);
        CHECK(!f.released || f.preserve);
        unsigned old_spawned = f.spawned; int old_released = f.released;
        CHECK(lantern_launch(&s, &ops, &f) < 0);
        CHECK(f.spawned == (int)old_spawned && f.released == old_released);
        CHECK(!f.released || f.preserve);
    }
    for (int bad = 0; bad < 8; bad++) {
        init(&f, &s); struct lantern_policy invalid = p;
        if (bad == 0) invalid.uid = 999;
        if (bad == 1) invalid.executable.executable_inode = 0;
        if (bad == 2) invalid.executable.reserved = 1;
        if (bad == 3) memset(invalid.object_sha256, 0, 32);
        if (bad == 4) memset(invalid.policy_sha256, 0, 32);
        if (bad == 5) f.duplicate_link = 1;
        if (bad == 6) f.duplicate_program = 1;
        if (bad == 7) memset(invalid.shell_sha256, 0, 32);
        CHECK(lantern_prepare(&s, &invalid, &ops, &f) < 0); CHECK(!f.spawned);
    }
    init(&f, &s); f.wrong_map = 1;
    CHECK(lantern_prepare(&s, &p, &ops, &f) < 0 && !f.spawned);
    init(&f, &s); CHECK(!lantern_prepare(&s, &p, &ops, &f)); f.wrong_grant = 1;
    CHECK(lantern_launch(&s, &ops, &f) < 0 && !f.released);
    init(&f, &s); CHECK(!lantern_prepare(&s, &p, &ops, &f)); f.dead_child = 1;
    CHECK(lantern_launch(&s, &ops, &f) < 0 && !f.released);
    init(&f, &s); CHECK(!lantern_prepare(&s, &p, &ops, &f));
    CHECK(lantern_prepare(&s, &p, &ops, &f) < 0 && !f.spawned);
    struct lantern_ops incomplete = ops; incomplete.freeze = NULL;
    init(&f, &s); CHECK(lantern_prepare(&s, &p, &incomplete, &f) == -EINVAL && !f.call);
    printf("%u loader failure/identity checks passed; kernel_enforcement_qualified=false\n", checks);
    return 0;
}
