#include <stdio.h>
#include <stdlib.h>
#define LANTERN_GUARD_REPLAY 1
#include "desktop_guard.bpf.c"

struct task_struct *replay_task;
struct lantern_guard_grant *replay_grant;
struct task_struct *replay_grant_owner;
static unsigned int checks;

static void expect(int actual, int wanted, const char *scenario)
{
    ++checks;
    if (actual != wanted) {
        fprintf(stderr, "%s: got %d expected %d\n", scenario, actual, wanted);
        exit(1);
    }
}

static void all_device_hooks(struct file *file, int wanted, const char *scenario)
{
    struct vm_area_struct vma = {.vm_file = file};
    expect(guard_open(file, 0), wanted, scenario);
    expect(guard_receive(file, 0), wanted, scenario);
    expect(guard_permission(file, 6, 0), wanted, scenario);
    expect(guard_ioctl(file, 0xffffffff, 0, 0), wanted, scenario);
    expect(guard_mmap(file, 3, 3, 1, 0), wanted, scenario);
    expect(guard_mprotect(&vma, 3, 3, 0), wanted, scenario);
    /* Another LSM's denial cannot be erased by this candidate. */
    expect(guard_open(file, -13), -13, scenario);
    expect(guard_receive(file, -13), -13, scenario);
    expect(guard_permission(file, 6, -13), -13, scenario);
    expect(guard_ioctl(file, 0, 0, -13), -13, scenario);
    expect(guard_mmap(file, 3, 3, 1, -13), -13, scenario);
    expect(guard_mprotect(&vma, 3, 3, -13), -13, scenario);
}

int main(void)
{
    struct cred app = {{1000}, {1000}, {1000}, {1000}};
    struct cred root = {{0}, {0}, {0}, {0}};
    struct super_block sb = {.s_dev = 123};
    struct inode exe_inode = {.i_mode = 0100755, .i_ino = 456, .i_sb = &sb};
    struct file exe = {.f_inode = &exe_inode};
    struct mm_struct mm = {.exe_file = &exe};
    struct task_struct shell = {.tgid = 200, .start_boottime = 9000000001,
                                .cred = &app, .mm = &mm};
    struct task_struct other = {.tgid = 201, .start_boottime = 9000000002,
                                .cred = &app, .mm = &mm};
    struct task_struct thread = shell;
    struct task_struct manager = {.tgid = 90, .start_boottime = 8000000000,
                                  .cred = &root};
    struct lantern_guard_grant record = {.executable_inode = exe_inode.i_ino,
        .executable_device = sb.s_dev};
    struct inode device_inode = {.i_mode = 0020600};
    struct file device = {.f_inode = &device_inode};
    const unsigned int denied[][2] = {
        {13, 0}, {13, 64}, {13, 1024}, {226, 0}, {226, 63}, {226, 64},
        {226, 127}, {29, 0}, {4, 0}, {4, 63}, {5, 0}, {5, 1}
    };
    const unsigned int forbidden[][2] = {{1, 1}, {1, 2}, {1, 4}, {10, 223}};
    const unsigned int allowed[][2] = {
        {226, 128}, {226, 255}, {1, 3}, {1, 5}, {10, 200}, {4, 64},
        {5, 2}, {188, 0}, {510, 0}
    };
    shell.group_leader = &shell;
    other.group_leader = &other;
    thread.group_leader = &shell;
    manager.group_leader = &manager;
    replay_grant = &record;
    replay_grant_owner = &shell;
    for (unsigned int i = 0; i < sizeof(denied)/sizeof(denied[0]); ++i) {
        device_inode.i_rdev = (denied[i][0] << 20) | denied[i][1];
        replay_task = &other;
        all_device_hooks(&device, -1, "alternate process, alias or transferred handle");
        replay_task = &shell;
        all_device_hooks(&device, 0, "exact enrolled compositor");
        replay_task = &thread;
        all_device_hooks(&device, 0, "compositor thread uses leader task storage");
        replay_task = &manager;
        all_device_hooks(&device, 0, "independent root management");
    }
    for (unsigned int i = 0; i < sizeof(forbidden)/sizeof(forbidden[0]); ++i) {
        device_inode.i_rdev = (forbidden[i][0] << 20) | forbidden[i][1];
        replay_task = &shell;
        all_device_hooks(&device, -1, "raw memory and virtual input stay forbidden");
        replay_task = &other;
        all_device_hooks(&device, -1, "raw memory and virtual input for application");
    }
    replay_task = &other;
    for (unsigned int i = 0; i < sizeof(allowed)/sizeof(allowed[0]); ++i) {
        device_inode.i_rdev = (allowed[i][0] << 20) | allowed[i][1];
        all_device_hooks(&device, 0, "render nodes, serial, NPU and ordinary devices");
    }
    device_inode.i_rdev = 226 << 20;
    replay_task = &shell;
    replay_grant = NULL;
    all_device_hooks(&device, -1, "missing map entry fails closed");
    replay_grant = &record;
    struct task_struct reused = shell;
    reused.group_leader = &reused;
    replay_task = &reused;
    all_device_hooks(&device, -1, "PID reuse fails closed");
    replay_task = &shell;
    record.reserved = 1;
    all_device_hooks(&device, -1, "invalid task grant fails closed");
    record.reserved = 0;
    ++record.executable_inode;
    all_device_hooks(&device, -1, "executable inode mismatch fails closed");
    --record.executable_inode;
    ++record.executable_device;
    all_device_hooks(&device, -1, "executable filesystem mismatch fails closed");
    --record.executable_device;
    shell.mm = NULL;
    all_device_hooks(&device, -1, "missing executable fails closed");
    shell.mm = &mm;
    device_inode.i_mode = 0100600;
    replay_task = &other;
    all_device_hooks(&device, 0, "regular file with matching rdev is not a device");
    all_device_hooks(NULL, 0, "anonymous mapping has no device file");

    expect(guard_ptrace(&shell, 2, 0), -1, "same UID cannot trace Shell");
    expect(guard_signal(&shell, NULL, 9, NULL, 0), -1, "same UID cannot kill Shell");
    expect(guard_signal(&shell, NULL, 19, NULL, 0), -1, "same UID cannot stop Shell");
    expect(guard_ptrace(&shell, 2, -13), -13, "preserve prior ptrace denial");
    expect(guard_signal(&shell, NULL, 9, NULL, -13), -13, "preserve prior signal denial");
    replay_task = &manager;
    expect(guard_signal(&shell, NULL, 9, &app, 0), -1,
           "kernel worker using application's saved credentials cannot signal Shell");
    expect(guard_signal(&shell, NULL, 9, &root, 0), 0, "root management signal");
    expect(guard_ptrace(&shell, 2, 0), 0, "root management tracing");
    replay_task = &thread;
    expect(guard_ptrace(&shell, 2, 0), 0, "same address space");
    expect(guard_signal(&shell, NULL, 0, NULL, 0), 0, "same group signal");
    expect(guard_fork(&other, CLONE_THREAD, 0), 0, "compositor threads");
    expect(guard_fork(&other, 0, 0), -1, "no descriptor/mapping inheritance through fork");
    expect(guard_fork(&other, 0x4100, 0), -1, "vfork/clone VM cannot copy compositor state");
    expect(guard_fork(&other, CLONE_THREAD, -13), -13, "preserve prior clone denial");
    struct linux_binprm binary = {.cred = &app};
    expect(guard_exec(&binary, 0), -1, "Shell cannot reexec in granted PID");
    replay_task = &other;
    expect(guard_fork(&other, 0, 0), 0, "application children have no Shell grant");
    expect(guard_exec(&binary, 0), 0, "ordinary application exec");
    binary.cred = &root;
    expect(guard_exec(&binary, 0), -1, "set-id executable cannot escape application UID");
    expect(guard_exec(&binary, -13), -13, "preserve prior exec denial");
    expect(guard_setuid(&root, &app, 0, 0), -1, "application cannot change credentials");
    expect(guard_setuid(&app, &root, 0, 0), 0, "root can create application child");
    expect(guard_setuid(&app, &app, 0, 0), 0, "unchanged application credentials");
    expect(guard_setuid(&app, &app, 0, -13), -13, "preserve prior UID denial");
    for (int cap = 0; cap <= 40; ++cap) {
        expect(guard_capability(&app, NULL, cap, 0, 0), -1, "application cannot gain capabilities");
        expect(guard_capability(&root, NULL, cap, 0, 0), 0, "root administration preserved");
        expect(guard_capability(&root, NULL, cap, 0, -13), -13, "preserve prior capability denial");
    }
    for (int command = 0; command <= 40; ++command) {
        replay_task = &other;
        expect(guard_bpf(command, NULL, 0, 0), -1, "application cannot use an inherited BPF descriptor");
        expect(guard_bpf(command, NULL, 0, -13), -13, "preserve prior BPF denial");
        replay_task = &manager;
        expect(guard_bpf(command, NULL, 0, 0), 0, "root BPF management");
    }
    for (int field = 0; field < 4; ++field) {
        struct cred disguised = root;
        if (field == 0) disguised.uid.val = 1000;
        if (field == 1) disguised.euid.val = 1000;
        if (field == 2) disguised.suid.val = 1000;
        if (field == 3) disguised.fsuid.val = 1000;
        other.cred = &disguised;
        replay_task = &other;
        device_inode.i_mode = 0020600;
        all_device_hooks(&device, -1, "kernel UID survives namespace/mixed credential disguise");
    }
    printf("%u hook decision checks passed; kernel_enforcement_qualified=false\n", checks);
    return 0;
}
