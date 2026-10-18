/* Userspace representation of kernel inputs, not an enforcement emulator.
 * Compiles the actual hook bodies for attack/identity decision replay only.
 */
#ifndef LANTERN_GUARD_REPLAY_H
#define LANTERN_GUARD_REPLAY_H
#include "desktop_guard_policy.h"
#include <stddef.h>
typedef unsigned int __u32;
typedef unsigned long long __u64;
struct kuid { unsigned int val; };
struct cred { struct kuid uid, euid, suid, fsuid; };
struct super_block { unsigned int s_dev; };
struct inode {
    unsigned short i_mode;
    unsigned int i_rdev;
    unsigned long i_ino;
    struct super_block *i_sb;
};
struct file { struct inode *f_inode; };
struct mm_struct { struct file *exe_file; };
struct task_struct {
    int tgid;
    __u64 start_boottime;
    struct task_struct *group_leader;
    const struct cred *cred;
    struct mm_struct *mm;
};
struct vm_area_struct { struct file *vm_file; };
struct linux_binprm { const struct cred *cred; };
struct kernel_siginfo { int unused; };
struct user_namespace { int unused; };
union bpf_attr { int unused; };
extern struct task_struct *replay_task;
extern struct lantern_guard_grant *replay_grant;
extern struct task_struct *replay_grant_owner;
#define SEC(name)
#define BPF_MAP_TYPE_TASK_STORAGE 29
#define BPF_F_NO_PREALLOC 1
#define __uint(name, value) int (*name)[value]
#define __type(name, value) value *name
#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
#endif
#define BPF_PROG(name, ...) name(__VA_ARGS__)
#define CORE1(s,a) ((s) ? (s)->a : 0)
#define CORE2(s,a,b) ((s) && (s)->a ? (s)->a->b : 0)
#define CORE3(s,a,b,c) ((s) && (s)->a && (s)->a->b ? (s)->a->b->c : 0)
#define SELECT_CORE(_1,_2,_3,NAME,...) NAME
#define BPF_CORE_READ(s,...) SELECT_CORE(__VA_ARGS__,CORE3,CORE2,CORE1)(s,__VA_ARGS__)
static inline struct task_struct *bpf_get_current_task_btf(void) { return replay_task; }
static inline void *bpf_task_storage_get(void *map, struct task_struct *task,
                                         void *value, unsigned long long flags)
{
    (void)map; (void)value; (void)flags;
    return task == replay_grant_owner ? replay_grant : NULL;
}
#endif
