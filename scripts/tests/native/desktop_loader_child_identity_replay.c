/* SPDX-License-Identifier: GPL-2.0-only
 * Exercise the real Linux stopped-child path and its /proc start-time identity.
 * This is a disposable container check; it never releases or execs a child.
 */
#include "linux_backend.c"

static unsigned checks;
#define CHECK(x) do { checks++; if (!(x)) { \
    fprintf(stderr, "child identity check failed line %d\n", __LINE__); \
    return 1; } } while (0)

int main(void)
{
    static const char directory[] = "/review-child-identity";
    static const char path[] = "/review-child-identity/shell";
    int fd, status;
    unsigned digest_size = 0;
    struct lantern_linux context = {0};
    struct lantern_child child = {-1, 0, 0, 0};
    char *environment[] = {"PATH=/usr/bin:/bin", NULL};

    CHECK(!mkdir(directory, 0700) || errno == EEXIST);
    CHECK(!chmod(directory, 0700));
    fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    CHECK(fd >= 0);
    CHECK(write(fd, "review-shell", 12) == 12);
    close(fd);
    context.shell_fd = root_open(path, O_RDONLY);
    CHECK(context.shell_fd >= 0);
    CHECK(EVP_Digest("review-shell", 12, context.policy.shell_sha256,
                     &digest_size, EVP_sha256(), NULL));
    CHECK(digest_size == 32);
    context.environment = environment;
    /* The backend's ordinary live check also requires a non-null object
     * handle. This replay never asks it to load or attach that object. */
    context.object = (void *)&context;
    CHECK(!spawn_stopped(&context, &child));
    CHECK(child.pid > 1 && child.pidfd >= 0 && child.start_ticks > 0 &&
          child.cookie);
    CHECK(!stopped(&context, &child));
    fence(&context, &child, 0);
    CHECK(waitpid(child.pid, &status, 0) == child.pid);
    CHECK(WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL);
    close(context.shell_fd);
    context.shell_fd = -1;
    printf("%u child identity checks passed; child_released=false\n", checks);
    return 0;
}
