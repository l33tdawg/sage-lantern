/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef LANTERN_LOADER_CANDIDATE_H
#define LANTERN_LOADER_CANDIDATE_H
#include <stddef.h>
#include <stdint.h>
#include "../desktop_guard_policy.h"

#define LANTERN_HOOK_COUNT 13
extern const char *const lantern_sections[LANTERN_HOOK_COUNT];
enum lantern_phase { LANTERN_NEW, LANTERN_PINNED, LANTERN_CHILD_STOPPED,
    LANTERN_GRANTED, LANTERN_FROZEN, LANTERN_RELEASED, LANTERN_HELD };

struct lantern_binding { uint32_t link_id, program_id, map_id; };
/* start_ticks is procfs stat field 22, taken from the same child as pidfd. */
struct lantern_child { int pidfd; int pid; uint64_t start_ticks; uint64_t cookie; };
struct lantern_policy {
    uint32_t uid;
    struct lantern_guard_grant executable;
    unsigned char object_sha256[32], policy_sha256[32], shell_sha256[32];
};
/* All return 0 only after positive verification. No caller JSON facts.
 * prepare must enforce a root-controlled early-start gate before ANY UID1000
 * process; load consumes digest-checked bytes, never reopens an object path.
 * attest compares root-owned pins against this loaded object and identities.
 * stopped must verify the parent's own unreleased child, never adopt a PID.
 * record_attempt must durably bind the stopped child before release.
 * fence retains all pins and does not terminate an already running session.
 */
struct lantern_ops {
    int (*prepare)(void *, const struct lantern_policy *);
    int (*load_map)(void *, uint32_t *);
    int (*attach_pin)(void *, unsigned, struct lantern_binding *);
    int (*attest)(void *, const struct lantern_binding *, uint32_t);
    int (*spawn_stopped)(void *, struct lantern_child *);
    int (*stopped)(void *, const struct lantern_child *);
    int (*insert)(void *, int, const struct lantern_guard_grant *);
    int (*lookup)(void *, int, struct lantern_guard_grant *);
    int (*freeze)(void *);
    int (*frozen)(void *, int, const struct lantern_guard_grant *);
    int (*record_attempt)(void *, const struct lantern_child *);
    int (*release)(void *, const struct lantern_child *);
    void (*fence)(void *, const struct lantern_child *, int);
};
struct lantern_loader {
    enum lantern_phase phase;
    uint32_t map_id;
    unsigned pinned, attempted, release_attempted;
    struct lantern_binding links[LANTERN_HOOK_COUNT];
    struct lantern_child child;
    struct lantern_policy policy;
};
int lantern_prepare(struct lantern_loader *, const struct lantern_policy *,
                    const struct lantern_ops *, void *);
int lantern_launch(struct lantern_loader *, const struct lantern_ops *, void *);
int lantern_recheck(struct lantern_loader *, const struct lantern_ops *, void *);
void lantern_hold(struct lantern_loader *, const struct lantern_ops *, void *);
#endif
