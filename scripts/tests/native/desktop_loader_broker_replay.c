/* SPDX-License-Identifier: GPL-2.0-only
 * Native broker replay over the actual loader sequencing core.
 * No BPF, compositor, session or device operation is performed. */
#include "loader.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

struct broker {
    int client;
    int child_pid;
    int passed_fd;
    int released;
    int aborted;
    struct lantern_loader loader;
};

static int send_message(struct broker *b, const char *message, int fd)
{
    struct iovec iov = {(void *)message, strlen(message)};
    char control[CMSG_SPACE(sizeof(int))] = {0};
    struct msghdr msg = {0};
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    if (fd >= 0) {
        struct cmsghdr *cmsg;
        msg.msg_control = control;
        msg.msg_controllen = sizeof(control);
        cmsg = CMSG_FIRSTHDR(&msg);
        cmsg->cmsg_level = SOL_SOCKET;
        cmsg->cmsg_type = SCM_RIGHTS;
        cmsg->cmsg_len = CMSG_LEN(sizeof(int));
        memcpy(CMSG_DATA(cmsg), &fd, sizeof(fd));
    }
    return sendmsg(b->client, &msg, 0) < 0 ? -1 : 0;
}

static int receive_line(struct broker *b, char *buffer, size_t size, int timeout_ms)
{
    struct pollfd ready = {b->client, POLLIN, 0};
    ssize_t count;
    int result = poll(&ready, 1, timeout_ms);
    if (result != 1 || !(ready.revents & POLLIN)) return -1;
    count = recv(b->client, buffer, size - 1, 0);
    if (count <= 0) return -1;
    buffer[count] = '\0';
    return 0;
}

static int prepare(void *context, const struct lantern_policy *policy)
{
    (void)context; (void)policy; return 0;
}
static int load_map(void *context, uint32_t *id)
{
    (void)context; *id = 42; return 0;
}
static int attach_pin(void *context, unsigned index, struct lantern_binding *binding)
{
    (void)context;
    *binding = (struct lantern_binding){100 + index, 200 + index, 42};
    return 0;
}
static int attest(void *context, const struct lantern_binding *binding,
                  uint32_t map_id)
{
    (void)context; (void)binding; (void)map_id; return 0;
}
static int spawn_stopped(void *context, struct lantern_child *child)
{
    struct broker *b = context;
    int status;
    pid_t pid = fork();
    if (pid < 0) return -errno;
    if (pid == 0) {
        raise(SIGSTOP);
        _exit(0);
    }
    if (waitpid(pid, &status, WUNTRACED) != pid || !WIFSTOPPED(status) ||
        WSTOPSIG(status) != SIGSTOP) {
        kill(pid, SIGKILL);
        waitpid(pid, NULL, 0);
        return -EPERM;
    }
    int passed = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (passed < 0) {
        kill(pid, SIGKILL);
        waitpid(pid, NULL, 0);
        return -errno;
    }
    b->child_pid = pid;
    b->passed_fd = passed;
    *child = (struct lantern_child){passed, pid, 9001};
    return 0;
}
static int stopped(void *context, const struct lantern_child *child)
{
    struct broker *b = context;
    return b->released || child->pid != b->child_pid || b->passed_fd != child->pidfd
        ? -EPERM : 0;
}
static int insert(void *context, int pidfd, const struct lantern_guard_grant *grant)
{
    (void)context; (void)pidfd; (void)grant; return 0;
}
static int lookup(void *context, int pidfd, struct lantern_guard_grant *grant)
{
    (void)context; (void)pidfd;
    *grant = (struct lantern_guard_grant){999, 12345, 0};
    return 0;
}
static int freeze(void *context) { (void)context; return 0; }
static int frozen(void *context, int pidfd, const struct lantern_guard_grant *grant)
{
    (void)context; (void)pidfd; (void)grant; return 0;
}
static int record_attempt(void *context, const struct lantern_child *child)
{
    struct broker *b = context;
    char message[512], response[1024];
    int count;
    if (child->pid != b->child_pid || child->pidfd != b->passed_fd) return -EPERM;
    count = snprintf(response, sizeof(response),
        "{\"schema\":\"sage.lantern.desktop-loader-broker.v1\","
        "\"status\":\"stopped\",\"pid\":%d,\"start_ticks\":1,\"cookie\":%llu}\n",
        child->pid, (unsigned long long)child->cookie);
    if (count < 0 || (size_t)count >= sizeof(response) ||
        send_message(b, response, child->pidfd)) return -EPERM;
    if (receive_line(b, message, sizeof(message), 5000)) return -EPERM;
    if (!strstr(message, "\"action\":\"release\"")) {
        b->aborted = 1;
        return -EPERM;
    }
    return 0;
}
static int release(void *context, const struct lantern_child *child)
{
    struct broker *b = context;
    if (child->pid != b->child_pid || child->pidfd != b->passed_fd ||
        b->released) return -EPERM;
    b->released = 1;
    if (kill(b->child_pid, SIGCONT)) return -1;
    if (waitpid(b->child_pid, NULL, 0) != b->child_pid) return -1;
    return 0;
}
static void fence(void *context, const struct lantern_child *child, int preserve)
{
    struct broker *b = context;
    (void)child;
    if (preserve || b->released) return;
    if (b->child_pid > 0) {
        kill(b->child_pid, SIGKILL);
        waitpid(b->child_pid, NULL, 0);
    }
}

static struct lantern_ops operations(void)
{
    return (struct lantern_ops){
        prepare, load_map, attach_pin, attest, spawn_stopped, stopped,
        insert, lookup, freeze, frozen, record_attempt, release, fence};
}

int main(int argc, char **argv)
{
    struct broker b = {0};
    struct sockaddr_un address = {0};
    struct lantern_policy policy = {0};
    struct lantern_ops ops = operations();
    char request[4096];
    int listener, result;
    if (argc != 2 || strlen(argv[1]) >= sizeof(address.sun_path)) return 64;
    if (strspn(argv[1], "0123456789") == strlen(argv[1])) {
        b.client = atoi(argv[1]);
        goto connected;
    }
    unlink(argv[1]);
    listener = socket(AF_UNIX, SOCK_STREAM, 0);
    if (listener < 0) return 1;
    address.sun_family = AF_UNIX;
    strcpy(address.sun_path, argv[1]);
    if (bind(listener, (struct sockaddr *)&address, sizeof(address)) ||
        listen(listener, 1)) return 1;
    b.client = accept(listener, NULL, NULL);
    close(listener);
connected:
    if (b.client < 0 || receive_line(&b, request, sizeof(request), 5000)) return 1;
    if (!strstr(request, "\"action\":\"stage\"")) return 1;
    policy.uid = 1000;
    policy.executable = (struct lantern_guard_grant){999, 12345, 0};
    policy.object_sha256[0] = 1;
    policy.policy_sha256[0] = 2;
    policy.shell_sha256[0] = 3;
    b.loader.child.pidfd = -1;
    result = lantern_prepare(&b.loader, &policy, &ops, &b);
    if (!result) result = lantern_launch(&b.loader, &ops, &b);
    if (!result) {
        send_message(&b, "{\"schema\":\"sage.lantern.desktop-loader-broker.v1\","
            "\"status\":\"released\"}\n", -1);
    } else if (b.aborted) {
        send_message(&b, "{\"schema\":\"sage.lantern.desktop-loader-broker.v1\","
            "\"status\":\"aborted\"}\n", -1);
    } else {
        send_message(&b, "{\"schema\":\"sage.lantern.desktop-loader-broker.v1\","
            "\"status\":\"error\"}\n", -1);
    }
    if (b.passed_fd > 0) close(b.passed_fd);
    close(b.client);
    if (strspn(argv[1], "0123456789") != strlen(argv[1])) unlink(argv[1]);
    return result ? 1 : 0;
}
