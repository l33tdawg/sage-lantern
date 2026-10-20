/* SPDX-License-Identifier: GPL-2.0-only
 * Linux/libbpf source candidate. No activation entry point is supplied.
 * prepare deliberately refuses until root early-boot/session integration is
 * qualified. The inspect binary calls only open/close, never load/attach/fork.
 */
#define _GNU_SOURCE
#include "linux_backend.h"
#include <bpf/bpf.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/magic.h>
#include <openssl/evp.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/random.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <sys/sysmacros.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static int err(void) { return errno ? -errno : -EPERM; }
static int live(const struct lantern_linux *c)
{
    return c && !c->fenced && c->object && geteuid() == 0;
}
/* Walk every component with directory FDs. A trusted leaf never launders a
 * replaceable ancestor. ACLs are not accepted as an exception to mode bits.
 * Existing descriptor identity is retained; no second pathname object load. */
static int root_open(const char *path, int flags)
{
    char parts[PATH_MAX], *next, *name;
    struct stat s;
    int base, fd = -1;
    if (!path || path[0] != '/' || strlen(path) >= sizeof(parts) ||
        strstr(path, "//") || path[strlen(path)-1] == '/') return -EINVAL;
    strcpy(parts, path + 1);
    base = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (base < 0) return err();
    if (fstat(base, &s) || s.st_uid || (s.st_mode & 0022)) goto bad;
    name = strtok_r(parts, "/", &next);
    while (name) {
        char *following = strtok_r(NULL, "/", &next);
        if (!strcmp(name, ".") || !strcmp(name, "..")) goto bad;
        fd = openat(base, name, (following ? O_RDONLY | O_DIRECTORY : flags) |
                    O_NOFOLLOW | O_CLOEXEC);
        if (fd < 0 || fstat(fd, &s) || s.st_uid || (s.st_mode & 0022) ||
            (following ? !S_ISDIR(s.st_mode) :
                (flags & O_DIRECTORY ? !S_ISDIR(s.st_mode) : !S_ISREG(s.st_mode))))
            goto bad;
        close(base); base = fd; fd = -1; name = following;
    }
    return base;
bad:
    if (fd >= 0) close(fd);
    close(base);
    return -EPERM;
}
int lantern_linux_open(struct lantern_linux *c, const void *bytes, size_t length,
                       const struct lantern_policy *p)
{
    unsigned char digest[32]; unsigned digest_size = 0, seen = 0, count = 0;
    struct bpf_program *program; struct bpf_map *map;
    if (!c || !p || !bytes || !length || length > 16 * 1024 * 1024 ||
        !EVP_Digest(bytes, length, digest, &digest_size, EVP_sha256(), NULL) ||
        digest_size != 32 || memcmp(digest, p->object_sha256, 32)) return -EINVAL;
    memset(c, 0, sizeof(*c));
    c->map_fd = c->shell_fd = c->pin_dir_fd = c->exec_error_fd = -1;
    c->owned_child.pidfd = -1; c->policy = *p;
    c->object = bpf_object__open_mem(bytes, length, NULL);
    if (!c->object || libbpf_get_error(c->object)) { c->object = NULL; return -EINVAL; }
    bpf_object__for_each_program(program, c->object) {
        unsigned i;
        for (i = 0; i < LANTERN_HOOK_COUNT; i++)
            if (!strcmp(bpf_program__section_name(program), lantern_sections[i])) break;
        if (i == LANTERN_HOOK_COUNT || (seen & (1u << i)) ||
            bpf_program__type(program) != BPF_PROG_TYPE_LSM ||
            bpf_program__expected_attach_type(program) != BPF_LSM_MAC) goto bad;
        c->programs[i] = program; seen |= 1u << i;
    }
    if (seen != (1u << LANTERN_HOOK_COUNT) - 1) goto bad;
    bpf_object__for_each_map(map, c->object) { c->map = map; count++; }
    if (count != 1 || strcmp(bpf_map__name(c->map), "shell_grant") ||
        bpf_map__type(c->map) != BPF_MAP_TYPE_TASK_STORAGE ||
        bpf_map__key_size(c->map) != sizeof(int) ||
        bpf_map__value_size(c->map) != sizeof(struct lantern_guard_grant) ||
        bpf_map__map_flags(c->map) != BPF_F_NO_PREALLOC) goto bad;
    return 0;
bad:
    bpf_object__close(c->object); c->object = NULL; return -EINVAL;
}
int lantern_linux_shell(struct lantern_linux *c, const char *path)
{
    struct stat s; uint32_t device; int fd;
    if (!c || !path || strcmp(path, "/usr/bin/gnome-shell") || c->shell_fd >= 0)
        return -EINVAL;
    fd = root_open(path, O_RDONLY);
    if (fd < 0) return fd;
    if (fstat(fd, &s) || !(s.st_mode & 0111) || (s.st_mode & 06000) ||
        major(s.st_dev) > 4095 || minor(s.st_dev) > 1048575) { close(fd); return -EPERM; }
    /* Kernel dev_t differs from libc's userspace encoding. Never cast st_dev. */
    device = (uint32_t)(major(s.st_dev) << 20) | (uint32_t)minor(s.st_dev);
    if (s.st_ino != c->policy.executable.executable_inode ||
        device != c->policy.executable.executable_device) { close(fd); return -EPERM; }
    c->shell_fd = fd; return 0;
}
static int shell_digest(struct lantern_linux *c)
{
    unsigned char buffer[65536], digest[32]; unsigned length;
    struct stat before, after; off_t offset = 0; ssize_t got;
    EVP_MD_CTX *hash = EVP_MD_CTX_new();
    if (!hash) return -ENOMEM;
    int result = -EPERM;
    if (fstat(c->shell_fd, &before) || before.st_size <= 0 ||
        before.st_size > 64 * 1024 * 1024 ||
        !EVP_DigestInit_ex(hash, EVP_sha256(), NULL)) goto done;
    while ((got = pread(c->shell_fd, buffer, sizeof(buffer), offset)) > 0) {
        offset += got;
        if (offset > 64 * 1024 * 1024 || !EVP_DigestUpdate(hash, buffer, (size_t)got)) goto done;
    }
    if (got < 0 || offset != before.st_size || fstat(c->shell_fd, &after) ||
        before.st_dev != after.st_dev || before.st_ino != after.st_ino ||
        before.st_size != after.st_size || before.st_ctim.tv_sec != after.st_ctim.tv_sec ||
        before.st_ctim.tv_nsec != after.st_ctim.tv_nsec ||
        !EVP_DigestFinal_ex(hash, digest, &length) || length != 32 ||
        memcmp(digest, c->policy.shell_sha256, 32)) goto done;
    result = 0;
done:
    EVP_MD_CTX_free(hash); return result;
}
int lantern_linux_pins(struct lantern_linux *c, const char *path)
{
    struct statfs fs; struct stat s; int fd;
    /* Explicitly prepared root-owned private directory. Never mkdir/mount,
     * adopt existing objects, or remove a predecessor's pins here. */
    if (!c || !path || strcmp(path, "/sys/fs/bpf/sage-lantern-desktop") ||
        c->pin_dir_fd >= 0) return -EINVAL;
    fd = root_open(path, O_RDONLY | O_DIRECTORY);
    if (fd < 0) return fd;
    if (fstatfs(fd, &fs) || fs.f_type != BPF_FS_MAGIC || fstat(fd, &s) ||
        (s.st_mode & 0777) != 0700) { close(fd); return -EPERM; }
    c->pin_dir_fd = fd; strcpy(c->pins, path); return 0;
}
static int same_pins(struct lantern_linux *c)
{
    struct stat held, now; int fd = root_open(c->pins, O_RDONLY | O_DIRECTORY);
    if (fd < 0) return fd;
    int ok = !fstat(fd, &now) && !fstat(c->pin_dir_fd, &held) &&
        now.st_uid == 0 && (now.st_mode & 0777) == 0700 &&
        now.st_dev == held.st_dev && now.st_ino == held.st_ino;
    close(fd); return ok ? 0 : -EPERM;
}
static int pin_name(struct lantern_linux *c, const char *leaf, char *path)
{
    struct stat s;
    if (same_pins(c)) return -EPERM;
    if (!fstatat(c->pin_dir_fd, leaf, &s, AT_SYMLINK_NOFOLLOW) || errno != ENOENT)
        return -EEXIST;
    if (snprintf(path, PATH_MAX, "%s/%s", c->pins, leaf) >= PATH_MAX) return -EINVAL;
    return 0;
}
static int pinned_path(struct lantern_linux *c, const char *leaf, char *path)
{
    size_t root = strnlen(c->pins, PATH_MAX), tail = strlen(leaf);
    if (root == PATH_MAX || root + tail + 2 > PATH_MAX) return -EINVAL;
    memcpy(path, c->pins, root); path[root] = '/';
    memcpy(path + root + 1, leaf, tail + 1); return 0;
}
static int pinned_fd(struct lantern_linux *c, const char *leaf)
{
    char path[PATH_MAX]; struct stat info;
    if (same_pins(c) || fstatat(c->pin_dir_fd, leaf, &info, AT_SYMLINK_NOFOLLOW) ||
        info.st_uid || !S_ISREG(info.st_mode) || (info.st_mode & 0777) != 0600 ||
        pinned_path(c, leaf, path)) return -EPERM;
    return bpf_obj_get(path);
}
static int prepare(void *context, const struct lantern_policy *p)
{
    (void)context; (void)p;
    /* An authenticated early-boot admission barrier, complete root asset and
     * GNOME closure evidence, power approval and a fresh-session window are
     * REQUIRED. There is deliberately no boolean/JSON override or live caller.
     * Integration must replace this refusal only after its separate review. */
    return -EOPNOTSUPP;
}
static int load_map(void *context, uint32_t *id)
{
    struct lantern_linux *c = context; struct bpf_map_info info = {0};
    uint32_t size = sizeof(info); char path[PATH_MAX];
    if (!live(c) || c->pin_dir_fd < 0 || pin_name(c, "shell_grant", path)) return -EPERM;
    for (unsigned i = 0; i < LANTERN_HOOK_COUNT; i++) {
        char leaf[32]; snprintf(leaf, sizeof(leaf), "hook-%02u", i);
        char unused[PATH_MAX]; if (pin_name(c, leaf, unused)) return -EEXIST;
    }
    if (bpf_object__load(c->object)) return -EPERM;
    c->map_fd = bpf_map__fd(c->map);
    if (bpf_obj_get_info_by_fd(c->map_fd, &info, &size) || !info.id ||
        info.type != BPF_MAP_TYPE_TASK_STORAGE || info.key_size != sizeof(int) ||
        info.value_size != sizeof(struct lantern_guard_grant) ||
        info.map_flags != BPF_F_NO_PREALLOC || bpf_map__pin(c->map, path)) return -EPERM;
    *id = info.id; return 0;
}
static int program_map(struct lantern_linux *c, unsigned i, uint32_t *id)
{
    uint32_t ids[2] = {0}, size; struct bpf_prog_info pi = {0}; struct bpf_map_info mi = {0};
    pi.nr_map_ids = 2; pi.map_ids = (uint64_t)(uintptr_t)ids; size = sizeof(pi);
    if (bpf_obj_get_info_by_fd(bpf_program__fd(c->programs[i]), &pi, &size)) return -EPERM;
    size = sizeof(mi);
    /* Credential/setuid/BPF hooks do not consult shell_grant; all other hooks
     * must reference this exact map. Binding their object IDs is still required. */
    unsigned expected_maps = i < 10 ? 1 : 0;
    if (bpf_obj_get_info_by_fd(c->map_fd, &mi, &size) || pi.type != BPF_PROG_TYPE_LSM ||
        !pi.id || !pi.attach_btf_id || pi.nr_map_ids != expected_maps ||
        (expected_maps && ids[0] != mi.id)) return -EPERM;
    *id = pi.id; return 0;
}
static int attach_pin(void *context, unsigned i, struct lantern_binding *binding)
{
    struct lantern_linux *c = context; char leaf[32], path[PATH_MAX];
    struct bpf_link_info li = {0}; struct bpf_map_info mi = {0}; uint32_t size, program_id;
    if (!live(c) || i >= LANTERN_HOOK_COUNT || c->links[i]) return -EPERM;
    snprintf(leaf, sizeof(leaf), "hook-%02u", i);
    if (pin_name(c, leaf, path) || program_map(c, i, &program_id)) return -EPERM;
    c->links[i] = bpf_program__attach_lsm(c->programs[i]);
    if (!c->links[i] || libbpf_get_error(c->links[i])) { c->links[i] = NULL; return -EPERM; }
    if (bpf_link__pin(c->links[i], path)) return -EPERM;
    size = sizeof(li);
    if (bpf_obj_get_info_by_fd(bpf_link__fd(c->links[i]), &li, &size) || !li.id ||
        li.type != BPF_LINK_TYPE_TRACING || li.prog_id != program_id ||
        li.tracing.attach_type != BPF_LSM_MAC) return -EPERM;
    size = sizeof(mi);
    if (bpf_obj_get_info_by_fd(c->map_fd, &mi, &size)) return -EPERM;
    *binding = (struct lantern_binding){li.id, program_id, mi.id}; return 0;
}
static int attest(void *context, const struct lantern_binding *links, uint32_t map_id)
{
    struct lantern_linux *c = context; uint32_t size;
    struct bpf_map_info mi = {0}; int fd;
    if (!live(c) || same_pins(c)) return -EPERM;
    fd = pinned_fd(c, "shell_grant"); if (fd < 0) return -EPERM;
    size = sizeof(mi); int result = bpf_obj_get_info_by_fd(fd, &mi, &size); close(fd);
    if (result || mi.id != map_id || mi.type != BPF_MAP_TYPE_TASK_STORAGE ||
        mi.key_size != sizeof(int) || mi.value_size != sizeof(struct lantern_guard_grant) ||
        mi.map_flags != BPF_F_NO_PREALLOC) return -EPERM;
    for (unsigned i = 0; i < LANTERN_HOOK_COUNT; i++) {
        struct bpf_link_info li = {0}; uint32_t program_id;
        char leaf[32]; snprintf(leaf, sizeof(leaf), "hook-%02u", i);
        fd = pinned_fd(c, leaf); if (fd < 0) return -EPERM;
        size = sizeof(li); result = bpf_obj_get_info_by_fd(fd, &li, &size); close(fd);
        if (result || program_map(c, i, &program_id) || li.id != links[i].link_id ||
            li.prog_id != links[i].program_id || li.prog_id != program_id ||
            links[i].map_id != map_id || li.type != BPF_LINK_TYPE_TRACING ||
            li.tracing.attach_type != BPF_LSM_MAC) return -EPERM;
    }
    return 0;
}
static int stopped(void *context, const struct lantern_child *child)
{
    struct lantern_linux *c = context; siginfo_t info = {0};
    struct pollfd pollfd = {child->pidfd, POLLIN, 0};
    if (!live(c) || !c->spawned || c->released || !c->stopped_seen ||
        memcmp(child, &c->owned_child, sizeof(*child)) || poll(&pollfd, 1, 0) != 0)
        return -EPERM;
    if (waitid(P_PIDFD, (id_t)child->pidfd, &info, WSTOPPED | WNOWAIT | WNOHANG) ||
        info.si_pid != child->pid || info.si_code != CLD_STOPPED || info.si_status != SIGSTOP)
        return -EPERM;
    return 0;
}
static int child_start_ticks(pid_t pid, uint64_t *value)
{
    char path[64], buffer[4096], *cursor, *end;
    ssize_t count;
    int fd;
    if (pid <= 1 || !value) return -EINVAL;
    if (snprintf(path, sizeof(path), "/proc/%ld/stat", (long)pid) >=
        (int)sizeof(path)) return -EINVAL;
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -EPERM;
    count = read(fd, buffer, sizeof(buffer) - 1);
    close(fd);
    if (count <= 0 || count >= (ssize_t)sizeof(buffer)) return -EPERM;
    buffer[count] = '\0';
    end = strrchr(buffer, ')');
    if (!end) return -EPERM;
    cursor = end + 1;
    /* The post-comm fields begin with state (field 3). Start time is field
     * 22, so skip 19 complete fields and parse the next token. */
    for (unsigned field = 0; field < 19; field++) {
        while (*cursor == ' ') cursor++;
        if (!*cursor) return -EPERM;
        while (*cursor && *cursor != ' ') cursor++;
    }
    while (*cursor == ' ') cursor++;
    if (*cursor < '1' || *cursor > '9') return -EPERM;
    errno = 0;
    unsigned long long parsed = strtoull(cursor, &end, 10);
    if (errno || end == cursor || (*end && *end != ' ') || !parsed)
        return -EPERM;
    *value = (uint64_t)parsed;
    return 0;
}
static int spawn_stopped(void *context, struct lantern_child *child)
{
    struct lantern_linux *c = context; struct rlimit core = {0, 0};
    int pipefd[2], fd; pid_t pid; uint64_t cookie, start_ticks;
    if (!live(c) || c->spawned || c->shell_fd < 0 || shell_digest(c) || !c->environment ||
        getrandom(&cookie, sizeof(cookie), 0) != sizeof(cookie) || !cookie) return -EPERM;
    c->spawned = 1;
    if (pipe2(pipefd, O_CLOEXEC | O_NONBLOCK)) return err();
    /* fork must be called by the single-threaded early supervisor, never from
     * a Python threaded daemon/preexec_fn. All untrusted input is preflighted. */
    pid = fork();
    if (pid == 0) {
        int failure = EPERM;
        close(pipefd[0]);
        int nullfd = open("/dev/null", O_RDWR | O_NOFOLLOW | O_CLOEXEC);
        struct stat nullstat;
        if (nullfd < 0 || fstat(nullfd, &nullstat) || !S_ISCHR(nullstat.st_mode) ||
            major(nullstat.st_rdev) != 1 || minor(nullstat.st_rdev) != 3) goto child_failed;
        for (int standard = 0; standard < 3; standard++)
            if (dup2(nullfd, standard) < 0) goto child_failed;
        if (nullfd > 2) close(nullfd);
        /* Only fixed exec/error descriptors survive before exec; all remaining
         * inherited descriptors are closed automatically by successful exec. */
        if (syscall(SYS_close_range, 3u, ~0u, 4u /* CLOSE_RANGE_CLOEXEC */)) goto child_failed;
        umask(0077);
        /* Initial child retains root until exact-task enrollment is complete.
         * SIGSTOP precedes UID drop and the FIRST compositor exec. */
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() <= 1 ||
            prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) || setrlimit(RLIMIT_CORE, &core) ||
            chdir("/") || raise(SIGSTOP)) goto child_failed;
        if (setgroups(0, NULL) || setresgid(1000, 1000, 1000) ||
            setresuid(1000, 1000, 1000) || prctl(PR_SET_DUMPABLE, 0)) goto child_failed;
        /* Parent death must NOT kill a released, potentially running session.
         * Reset after UID change (the kernel clears this setting on setuid). */
        if (prctl(PR_SET_PDEATHSIG, 0)) goto child_failed;
        char *argv[] = {"/usr/bin/gnome-shell", "--mode=ubuntu", NULL};
        fexecve(c->shell_fd, argv, c->environment);
child_failed:
        failure = errno ? errno : EPERM;
        ssize_t written = write(pipefd[1], &failure, sizeof(failure));
        (void)written;
        _exit(127);
    }
    close(pipefd[1]);
    if (pid < 0) { close(pipefd[0]); return err(); }
    fd = (int)syscall(SYS_pidfd_open, pid, 0);
    if (fd < 0) { /* Only our unreleased child; no numeric-PID adoption. */
        kill(pid, SIGKILL); waitpid(pid, NULL, 0); close(pipefd[0]); return err();
    }
    if (child_start_ticks(pid, &start_ticks)) {
        kill(pid, SIGKILL); waitpid(pid, NULL, 0);
        close(fd); close(pipefd[0]); return -EPERM;
    }
    c->owned_child = (struct lantern_child){fd, pid, start_ticks, cookie};
    c->exec_error_fd = pipefd[0];
    struct timespec pause = {0, 10000000};
    /* Bounded startup wait; a stalled child never blocks the supervisor
     * indefinitely. The future serialized event loop owns cancellation. */
    for (unsigned attempt = 0; attempt < 200 && !c->fenced; attempt++) {
        siginfo_t info = {0};
        if (waitid(P_PIDFD, (id_t)fd, &info, WSTOPPED | WEXITED | WNOWAIT | WNOHANG))
            return -EPERM;
        if (info.si_pid) {
            if (info.si_code != CLD_STOPPED || info.si_status != SIGSTOP || info.si_pid != pid)
                return -EPERM;
            c->stopped_seen = 1; *child = c->owned_child; return stopped(c, child);
        }
        if (nanosleep(&pause, NULL) && errno != EINTR) return -EPERM;
    }
    return -ETIMEDOUT;
}
static int insert(void *context, int pidfd, const struct lantern_guard_grant *grant)
{
    struct lantern_linux *c = context;
    if (!live(c) || pidfd != c->owned_child.pidfd || stopped(c, &c->owned_child)) return -EPERM;
    return bpf_map_update_elem(c->map_fd, &pidfd, grant, BPF_NOEXIST) ? err() : 0;
}
static int lookup(void *context, int pidfd, struct lantern_guard_grant *grant)
{
    struct lantern_linux *c = context;
    if (!live(c) || pidfd != c->owned_child.pidfd) return -EPERM;
    return bpf_map_lookup_elem(c->map_fd, &pidfd, grant) ? err() : 0;
}
static int freeze(void *context)
{
    struct lantern_linux *c = context;
    return live(c) && !bpf_map_freeze(c->map_fd) ? 0 : -EPERM;
}
static int frozen(void *context, int pidfd, const struct lantern_guard_grant *grant)
{
    struct lantern_linux *c = context;
    if (!live(c) || pidfd != c->owned_child.pidfd) return -EPERM;
    errno = 0;
    /* Same-value write cannot change authority; success is still a refusal.
     * A frozen map MUST return EPERM, including through its original RW FD. */
    int result = bpf_map_update_elem(c->map_fd, &pidfd, grant, BPF_EXIST);
    return result < 0 && errno == EPERM ? 0 : -EPERM;
}
static int record_attempt(void *context, const struct lantern_child *child)
{
    struct lantern_linux *c = context;
    if (!live(c) || !c->record_callback) return -EOPNOTSUPP;
    return c->record_callback(c->record_context, child);
}
static int release(void *context, const struct lantern_child *child)
{
    struct lantern_linux *c = context;
    if (stopped(c, child) || shell_digest(c)) return -EPERM;
    c->released = 1; /* Sticky BEFORE syscall: uncertainty never permits kill. */
    return syscall(SYS_pidfd_send_signal, child->pidfd, SIGCONT, NULL, 0) ? err() : 0;
}
static void fence(void *context, const struct lantern_child *child, int released)
{
    struct lantern_linux *c = context; (void)child;
    c->fenced = 1;
    /* Retain the caller's conservative attempt state through later holds and
     * close, even if release returned before setting our own syscall marker. */
    if (released) c->released = 1;
    /* Only dispose of a root-created child that was never release-attempted.
     * Running application/session preservation wins over cleanup convenience. */
    if (!c->released && c->owned_child.pidfd >= 0)
        (void)syscall(SYS_pidfd_send_signal, c->owned_child.pidfd, SIGKILL, NULL, 0);
}
void lantern_linux_close(struct lantern_linux *c)
{
    if (!c) return;
    fence(c, &c->owned_child, c->released);
    /* These LSM links use libbpf's FD-close destructor. Closing our FD does
     * not unlink a bpffs pin; no explicit unpin or kernel detach is called. */
    for (unsigned i = 0; i < LANTERN_HOOK_COUNT; i++) if (c->links[i]) {
        bpf_link__destroy(c->links[i]); c->links[i] = NULL;
    }
    if (c->object) { bpf_object__close(c->object); c->object = NULL; }
    int fds[] = {c->shell_fd, c->pin_dir_fd, c->exec_error_fd, c->owned_child.pidfd};
    for (unsigned i = 0; i < sizeof(fds)/sizeof(fds[0]); i++) if (fds[i] >= 0) close(fds[i]);
    c->shell_fd = c->pin_dir_fd = c->exec_error_fd = c->owned_child.pidfd = -1;
    c->map = NULL; c->map_fd = -1;
}
const struct lantern_ops lantern_linux_ops = {prepare, load_map, attach_pin, attest,
    spawn_stopped, stopped, insert, lookup, freeze, frozen, record_attempt,
    release, fence};
