/* SPDX-License-Identifier: GPL-2.0-only
 * Disabled source candidate. Compilation is not verifier/load qualification.
 * Do not load into an existing session: it cannot revoke existing mappings.
 * No attach command, loader, service, or activation marker is supplied here.
 */
#ifdef LANTERN_GUARD_REPLAY
#include "desktop_guard_replay.h"
#else
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_tracing.h>
#endif
#include "desktop_guard_policy.h"

#define APP_UID 1000
#define EPERM 1
#define CLONE_THREAD 0x00010000
#define S_IFMT 00170000
#define S_IFCHR 0020000

/* Empty map denies seat access for UID 1000. The future root launcher must
 * enroll its stopped exact child before release, freeze the map after its
 * single write, and bind every live link/map/object to the launch policy.
 * A task-storage map update uses the child's pidfd, not a numeric PID or birth
 * time rounded through /proc. Storage is destroyed with that kernel task.
 * These integration requirements remain OPEN; no JSON claim creates a grant.
 */
struct {
    __uint(type, BPF_MAP_TYPE_TASK_STORAGE);
    __uint(map_flags, BPF_F_NO_PREALLOC);
    __type(key, int);
    __type(value, struct lantern_guard_grant);
} shell_grant SEC(".maps");

static __always_inline const struct lantern_guard_grant *grant(struct task_struct *task)
{
    /* Direct CO-RE pointer access retains the verifier's task BTF type;
     * probe_read into a scalar pointer would lose the helper argument type.
     */
    return bpf_task_storage_get(&shell_grant, task->group_leader, 0, 0);
}

static __always_inline int app_cred(const struct cred *cred)
{
    /* Kernel kuids resist a user-namespace UID 0 disguise. */
    return BPF_CORE_READ(cred, uid.val) == APP_UID ||
           BPF_CORE_READ(cred, euid.val) == APP_UID ||
           BPF_CORE_READ(cred, suid.val) == APP_UID ||
           BPF_CORE_READ(cred, fsuid.val) == APP_UID;
}

static __always_inline int app_task(struct task_struct *task)
{
    return app_cred(BPF_CORE_READ(task, cred));
}

static __always_inline int enrolled(struct task_struct *task)
{
    return lantern_enrolled(grant(task));
}

static __always_inline int exact_shell(struct task_struct *task)
{
    struct task_struct *leader = BPF_CORE_READ(task, group_leader);
    struct file *exe = BPF_CORE_READ(leader, mm, exe_file);
    struct inode *inode;
    if (!exe)
        return 0;
    inode = BPF_CORE_READ(exe, f_inode);
    return lantern_shell(grant(task),
        BPF_CORE_READ(inode, i_sb, s_dev), BPF_CORE_READ(inode, i_ino));
}

static __always_inline int device_check(struct file *file, int ret)
{
    struct task_struct *task;
    struct inode *inode;
    __u32 device;
    if (ret || !file)
        return ret;
    task = bpf_get_current_task_btf();
    if (!app_task(task))
        return 0;
    inode = BPF_CORE_READ(file, f_inode);
    if ((BPF_CORE_READ(inode, i_mode) & S_IFMT) != S_IFCHR)
        return 0;
    device = BPF_CORE_READ(inode, i_rdev);
    return lantern_device_denied(device >> 20, device & 0xfffff,
                                  exact_shell(task)) ? -EPERM : 0;
}

SEC("lsm/file_open")
int BPF_PROG(guard_open, struct file *file, int ret)
{
    return device_check(file, ret);
}
SEC("lsm/file_receive")
int BPF_PROG(guard_receive, struct file *file, int ret)
{
    /* Includes SCM_RIGHTS from an unconfined/root donor such as logind. */
    return device_check(file, ret);
}
SEC("lsm/file_permission")
int BPF_PROG(guard_permission, struct file *file, int mask, int ret)
{
    return device_check(file, ret);
}
SEC("lsm/file_ioctl")
int BPF_PROG(guard_ioctl, struct file *file, unsigned int cmd,
             unsigned long arg, int ret)
{
    /* A previously inherited/opened handle must not bypass the device check. */
    return device_check(file, ret);
}
SEC("lsm/mmap_file")
int BPF_PROG(guard_mmap, struct file *file, unsigned long reqprot,
             unsigned long prot, unsigned long flags, int ret)
{
    return device_check(file, ret);
}
SEC("lsm/file_mprotect")
int BPF_PROG(guard_mprotect, struct vm_area_struct *vma,
             unsigned long reqprot, unsigned long prot, int ret)
{
    return device_check(BPF_CORE_READ(vma, vm_file), ret);
}

static __always_inline int peer_check(struct task_struct *target,
                                     const struct cred *cred, int ret)
{
    struct task_struct *caller;
    if (ret)
        return ret;
    caller = bpf_get_current_task_btf();
    return lantern_peer_denied(cred ? app_cred(cred) : app_task(caller),
        BPF_CORE_READ(caller, tgid) == BPF_CORE_READ(target, tgid),
        enrolled(target)) ? -EPERM : 0;
}
SEC("lsm/ptrace_access_check")
int BPF_PROG(guard_ptrace, struct task_struct *target, unsigned int mode, int ret)
{
    /* Covers ptrace, process_vm_* and /proc/pid/mem access checks. */
    return peer_check(target, 0, ret);
}
SEC("lsm/task_kill")
int BPF_PROG(guard_signal, struct task_struct *target, struct kernel_siginfo *info,
             int sig, const struct cred *cred, int ret)
{
    /* Some kernel callers provide the sender's saved credentials explicitly. */
    return peer_check(target, cred, ret);
}
SEC("lsm/task_alloc")
int BPF_PROG(guard_fork, struct task_struct *child, unsigned long flags, int ret)
{
    struct task_struct *task;
    if (ret)
        return ret;
    task = bpf_get_current_task_btf();
    /* Never copy compositor mappings/descriptors into an application process.
     * Threads retain its address space; apps must launch via the user manager.
     * Real GNOME compatibility with this restriction is still unqualified.
     */
    return app_task(task) && exact_shell(task) && !(flags & CLONE_THREAD)
           ? -EPERM : 0;
}
SEC("lsm/bprm_check_security")
int BPF_PROG(guard_exec, struct linux_binprm *bprm, int ret)
{
    struct task_struct *task;
    const struct cred *next;
    if (ret)
        return ret;
    task = bpf_get_current_task_btf();
    if (!app_task(task))
        return 0;
    if (exact_shell(task))
        return -EPERM; /* No reexec/replacement of the enrolled process. */
    next = BPF_CORE_READ(bprm, cred);
    return BPF_CORE_READ(next, uid.val) != APP_UID ||
           BPF_CORE_READ(next, euid.val) != APP_UID ||
           BPF_CORE_READ(next, suid.val) != APP_UID ||
           BPF_CORE_READ(next, fsuid.val) != APP_UID ? -EPERM : 0;
}
SEC("lsm/task_fix_setuid")
int BPF_PROG(guard_setuid, struct cred *next, const struct cred *old,
             int flags, int ret)
{
    if (ret || !app_cred(old))
        return ret;
    return BPF_CORE_READ(next, uid.val) != APP_UID ||
           BPF_CORE_READ(next, euid.val) != APP_UID ||
           BPF_CORE_READ(next, suid.val) != APP_UID ||
           BPF_CORE_READ(next, fsuid.val) != APP_UID ? -EPERM : 0;
}
SEC("lsm/capable")
int BPF_PROG(guard_capability, const struct cred *cred, struct user_namespace *ns,
             int cap, unsigned int opts, int ret)
{
    /* UID 1000 cannot gain policy administration via set-id/file capabilities.
     * Ordinary unprivileged application operations remain governed by DAC.
     */
    return ret ? ret : app_cred(cred) ? -EPERM : 0;
}
SEC("lsm/bpf")
int BPF_PROG(guard_bpf, int cmd, union bpf_attr *attr, unsigned int size, int ret)
{
    /* Also rejects map operations that need only an existing descriptor. */
    return ret ? ret : app_task(bpf_get_current_task_btf()) ? -EPERM : 0;
}

char LICENSE[] SEC("license") = "GPL";
