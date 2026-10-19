/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef LANTERN_LINUX_LOADER_H
#define LANTERN_LINUX_LOADER_H
#include "loader.h"
#include <bpf/libbpf.h>
#include <linux/limits.h>

struct lantern_linux {
    struct bpf_object *object;
    struct bpf_map *map;
    struct bpf_program *programs[LANTERN_HOOK_COUNT];
    struct bpf_link *links[LANTERN_HOOK_COUNT];
    int map_fd, shell_fd, pin_dir_fd, exec_error_fd;
    int spawned, stopped_seen, released, fenced;
    struct lantern_child owned_child;
    struct lantern_policy policy;
    char pins[PATH_MAX];
    /* Fixed environment supplied from the authenticated root launch policy,
     * not inherited. Caller must additionally validate native overlay assets,
     * environment, session, early-boot gate and ancestry via integration. */
    char *const *environment;
};
int lantern_linux_open(struct lantern_linux *, const void *, size_t,
                       const struct lantern_policy *);
int lantern_linux_shell(struct lantern_linux *, const char *);
int lantern_linux_pins(struct lantern_linux *, const char *);
void lantern_linux_close(struct lantern_linux *);
extern const struct lantern_ops lantern_linux_ops;
#endif
