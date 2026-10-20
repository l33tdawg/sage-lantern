/* SPDX-License-Identifier: GPL-2.0-only
 * Include actual backend to exercise private filesystem/digest predicates.
 * The only BPF query wrapper synthesizes map/program metadata. A disposable
 * root child stops inside the capless container and is killed before UID drop
 * or exec. No BPF syscall/attachment or compositor/session exec is performed.
 */
#include "linux_backend.c"
static unsigned checks;
#define CHECK(x) do { checks++; if (!(x)) { fprintf(stderr,"linux check failed line %d\n",__LINE__); return 1; } } while (0)
static unsigned expected_map_count = 1;
static uint32_t injected_map_id = 42;
int __wrap_bpf_obj_get_info_by_fd(int fd, void *info, uint32_t *length)
{
    (void)fd;
    if (*length == sizeof(struct bpf_prog_info)) {
        struct bpf_prog_info *p = info;
        uint32_t *ids = (uint32_t *)(uintptr_t)p->map_ids;
        p->type = BPF_PROG_TYPE_LSM; p->id = 100; p->attach_btf_id = 555;
        p->nr_map_ids = expected_map_count;
        if (expected_map_count) ids[0] = injected_map_id;
    } else if (*length == sizeof(struct bpf_map_info)) {
        struct bpf_map_info *m = info; m->id = 42;
    } else return -1;
    return 0;
}
int main(int argc, char **argv)
{
    if (argc != 2) return 64;
    const char *base = "/review-fixtures";
    CHECK(!mkdir(base, 0700) || errno == EEXIST);
    int fd = open("/review-fixtures/file", O_RDWR | O_CREAT | O_EXCL, 0600);
    CHECK(fd >= 0); CHECK(write(fd, "trusted", 7) == 7); close(fd);
    fd = root_open("/review-fixtures/file", O_RDONLY); CHECK(fd >= 0); close(fd);
    CHECK(root_open("review-fixtures/file", O_RDONLY) < 0);
    CHECK(root_open("/review-fixtures/../review-fixtures/file", O_RDONLY) < 0);
    CHECK(root_open("/review-fixtures/./file", O_RDONLY) < 0);
    CHECK(root_open("/review-fixtures//file", O_RDONLY) < 0);
    CHECK(!symlink("file", "/review-fixtures/alias"));
    CHECK(root_open("/review-fixtures/alias", O_RDONLY) < 0);
    CHECK(!chmod("/review-fixtures/file", 0660));
    CHECK(root_open("/review-fixtures/file", O_RDONLY) < 0);
    CHECK(!chmod("/review-fixtures/file", 0600));
    CHECK(!chmod(base, 0770));
    CHECK(root_open("/review-fixtures/file", O_RDONLY) < 0);
    CHECK(!chmod(base, 0700));
    CHECK(root_open("/review-fixtures/missing", O_RDONLY) < 0);
    struct lantern_linux c = {0}; c.shell_fd = open("/review-fixtures/file", O_RDONLY);
    unsigned digest_size = 0;
    CHECK(EVP_Digest("trusted", 7, c.policy.shell_sha256, &digest_size, EVP_sha256(), NULL));
    CHECK(!shell_digest(&c));
    fd = open("/review-fixtures/file", O_WRONLY); CHECK(fd >= 0);
    CHECK(write(fd, "altered", 7) == 7); close(fd);
    CHECK(shell_digest(&c) < 0); close(c.shell_fd);
    FILE *f = fopen(argv[1], "rb"); CHECK(f != NULL);
    CHECK(!fseek(f, 0, SEEK_END)); long length = ftell(f); CHECK(length > 0);
    rewind(f); void *bytes = malloc((size_t)length); CHECK(bytes != NULL);
    CHECK(fread(bytes, 1, (size_t)length, f) == (size_t)length); fclose(f);
    struct lantern_policy p = {0};
    CHECK(EVP_Digest(bytes, (size_t)length, p.object_sha256, &digest_size, EVP_sha256(), NULL));
    CHECK(!lantern_linux_open(&c, bytes, (size_t)length, &p));
    CHECK(lantern_linux_ops.prepare(&c, &p) == -EOPNOTSUPP);
    uint32_t id = 0;
    for (unsigned i = 0; i < LANTERN_HOOK_COUNT; i++) {
        expected_map_count = i < 10 ? 1 : 0; injected_map_id = 42;
        CHECK(!program_map(&c, i, &id) && id == 100);
        expected_map_count = i < 10 ? 0 : 1;
        CHECK(program_map(&c, i, &id) < 0);
        if (i < 10) {
            expected_map_count = 1; injected_map_id = 43;
            CHECK(program_map(&c, i, &id) < 0);
        }
    }
    /* Real Linux pidfd/own-child stop primitives, isolated from C and BPF.
     * The trusted descriptor is a toy file; no executable is ever released. */
    c.shell_fd = root_open("/review-fixtures/file", O_RDONLY); CHECK(c.shell_fd >= 0);
    CHECK(EVP_Digest("altered", 7, c.policy.shell_sha256, &digest_size, EVP_sha256(), NULL));
    char *environment[] = {"PATH=/usr/bin:/bin", NULL}; c.environment = environment;
    struct lantern_child child = {-1, 0, 0, 0};
    CHECK(!spawn_stopped(&c, &child));
    CHECK(child.pid > 1 && child.pidfd >= 0 && child.cookie);
    CHECK(!stopped(&c, &child));
    struct lantern_child wrong = child; wrong.cookie++;
    CHECK(stopped(&c, &wrong) < 0);
    wrong = child; wrong.pid++;
    CHECK(stopped(&c, &wrong) < 0);
    fence(&c, &child, 0);
    int status;
    CHECK(waitpid(child.pid, &status, 0) == child.pid);
    CHECK(WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL);
    CHECK(!c.released); CHECK(stopped(&c, &child) < 0);
    lantern_linux_close(&c); lantern_linux_close(&c);
    p.object_sha256[0] ^= 1;
    CHECK(lantern_linux_open(&c, bytes, (size_t)length, &p) < 0);
    CHECK(lantern_linux_open(&c, bytes, 0, &p) < 0);
    CHECK(lantern_linux_open(&c, bytes, 16 * 1024 * 1024 + 1, &p) < 0);
    /* The core's release-attempt uncertainty can be stronger than the
     * backend's own syscall marker. Keep it through later hold and close.
     * No child or descriptor belongs to this additional fixture. */
    struct lantern_linux uncertain = {0};
    uncertain.owned_child.pidfd = uncertain.shell_fd = uncertain.pin_dir_fd =
        uncertain.exec_error_fd = -1;
    fence(&uncertain, &uncertain.owned_child, 1);
    CHECK(uncertain.released && uncertain.fenced);
    fence(&uncertain, &uncertain.owned_child, 0);
    CHECK(uncertain.released);
    lantern_linux_close(&uncertain);
    CHECK(uncertain.released);
    free(bytes);
    printf("%u Linux filesystem/digest/object checks passed; kernel_enforcement_qualified=false\n", checks);
    return 0;
}
