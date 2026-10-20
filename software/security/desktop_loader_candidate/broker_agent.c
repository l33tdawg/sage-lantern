/* SPDX-License-Identifier: GPL-2.0-only
 * Fitted root broker around linux_backend.c. prepare remains EOPNOTSUPP. */
#define _GNU_SOURCE
#include "linux_backend.c"
#include <getopt.h>

struct agent {
    int client;
    int aborted;
};

static int send_message(struct agent *a, const char *body, int fd)
{
    struct iovec iov = {(void *)body, strlen(body)};
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
    return sendmsg(a->client, &msg, 0) < 0 ? -1 : 0;
}

static int receive_line(struct agent *a, char *buffer, size_t size, int timeout_ms)
{
    struct pollfd ready = {a->client, POLLIN, 0};
    ssize_t count;
    if (poll(&ready, 1, timeout_ms) != 1 || !(ready.revents & POLLIN)) return -1;
    count = recv(a->client, buffer, size - 1, 0);
    if (count <= 0) return -1;
    buffer[count] = '\0';
    return 0;
}

static int record_callback(void *context, const struct lantern_child *child)
{
    struct agent *a = context;
    char message[512], response[1024];
    int count = snprintf(response, sizeof(response),
        "{\"schema\":\"sage.lantern.desktop-loader-broker.v1\","
        "\"status\":\"stopped\",\"pid\":%d,\"start_ticks\":1,\"cookie\":%llu}\n",
        child->pid, (unsigned long long)child->cookie);
    if (count < 0 || (size_t)count >= sizeof(response) ||
        send_message(a, response, child->pidfd)) return -EPERM;
    if (receive_line(a, message, sizeof(message), 5000)) return -EPERM;
    if (!strstr(message, "\"action\":\"release\"")) {
        a->aborted = 1;
        return -EPERM;
    }
    return 0;
}

static void *read_file(const char *path, size_t *length)
{
    struct stat info;
    void *bytes;
    int fd = root_open(path, O_RDONLY);
    if (fd < 0) return NULL;
    if (fstat(fd, &info) || !S_ISREG(info.st_mode) ||
        info.st_size <= 0 || info.st_size > 16 * 1024 * 1024) {
        close(fd); return NULL;
    }
    bytes = malloc((size_t)info.st_size);
    if (!bytes) { close(fd); return NULL; }
    size_t offset = 0;
    while (offset < (size_t)info.st_size) {
        ssize_t got = read(fd, (char *)bytes + offset, (size_t)info.st_size - offset);
        if (got <= 0) { free(bytes); close(fd); return NULL; }
        offset += (size_t)got;
    }
    close(fd); *length = (size_t)info.st_size; return bytes;
}

static int unhex(const char *value, unsigned char *result, size_t size)
{
    if (!value || strlen(value) != size * 2) return -1;
    for (size_t i = 0; i < size; i++) {
        unsigned byte;
        if (sscanf(value + i * 2, "%2x", &byte) != 1) return -1;
        result[i] = (unsigned char)byte;
    }
    return 0;
}

static void usage(void)
{
    fputs("usage: sage-lantern-desktop-loader --socket PATH --object PATH "
          "--object-sha HEX --policy-sha HEX --shell-sha HEX --pins PATH\n", stderr);
}

int main(int argc, char **argv)
{
    static const struct option options[] = {
        {"socket", required_argument, NULL, 's'},
        {"object", required_argument, NULL, 'o'},
        {"object-sha", required_argument, NULL, 'O'},
        {"policy-sha", required_argument, NULL, 'p'},
        {"shell-sha", required_argument, NULL, 'h'},
        {"pins", required_argument, NULL, 'P'},
        {NULL, 0, NULL, 0},
    };
    const char *socket_path = NULL, *object_path = NULL, *object_sha = NULL;
    const char *policy_sha = NULL, *shell_sha = NULL, *pins = NULL;
    char *const environment[] = {
        "HOME=/home/sage", "USER=sage", "LOGNAME=sage",
        "PATH=/usr/bin:/bin", "LANG=C.UTF-8",
        "XDG_RUNTIME_DIR=/run/user/1000",
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
        "XDG_SESSION_TYPE=wayland", "XDG_SEAT=seat0",
        "XDG_CURRENT_DESKTOP=ubuntu:GNOME", "XDG_SESSION_DESKTOP=ubuntu",
        "DESKTOP_SESSION=ubuntu", "NOTIFY_SOCKET=/run/sage-lantern-desktop/ready.sock",
        NULL,
    };
    int option;
    while ((option = getopt_long(argc, argv, "", options, NULL)) != -1) {
        switch (option) {
        case 's': socket_path = optarg; break;
        case 'o': object_path = optarg; break;
        case 'O': object_sha = optarg; break;
        case 'p': policy_sha = optarg; break;
        case 'h': shell_sha = optarg; break;
        case 'P': pins = optarg; break;
        default: usage(); return 64;
        }
    }
    if (optind != argc || !socket_path || !object_path || !object_sha ||
        !policy_sha || !shell_sha || !pins) {
        usage(); return 64;
    }
    size_t length = 0;
    void *bytes = read_file(object_path, &length);
    struct lantern_policy policy = {0};
    policy.uid = 1000;
    policy.executable = (struct lantern_guard_grant){999, 12345, 0};
    if (!bytes || unhex(object_sha, policy.object_sha256, 32) ||
        unhex(policy_sha, policy.policy_sha256, 32) ||
        unhex(shell_sha, policy.shell_sha256, 32)) {
        free(bytes); return 1;
    }
    struct sockaddr_un address = {0};
    if (strlen(socket_path) >= sizeof(address.sun_path)) { free(bytes); return 1; }
    struct agent a = {.client = -1};
    struct lantern_linux context;
    memset(&context, 0, sizeof(context));
    context.map_fd = context.shell_fd = context.pin_dir_fd =
        context.exec_error_fd = context.owned_child.pidfd = -1;
    context.environment = environment;
    context.record_callback = record_callback;
    context.record_context = &a;
    int result = lantern_linux_open(&context, bytes, length, &policy);
    free(bytes);
    if (!result) result = lantern_linux_shell(&context, "/usr/bin/gnome-shell");
    if (!result) result = lantern_linux_pins(&context, pins);
    int listener = -1;
    if (!result) {
        unlink(socket_path);
        listener = socket(AF_UNIX, SOCK_STREAM, 0);
        address.sun_family = AF_UNIX;
        strcpy(address.sun_path, socket_path);
        if (listener < 0 ||
            bind(listener, (struct sockaddr *)&address, sizeof(address)) ||
            listen(listener, 1)) result = -1;
    }
    if (!result) {
        a.client = accept(listener, NULL, NULL);
        close(listener); listener = -1;
        char request[4096];
        if (a.client < 0 || receive_line(&a, request, sizeof(request), 5000) ||
            !strstr(request, "\"action\":\"stage\"")) result = -1;
    }
    if (!result) {
        result = lantern_prepare(&context, &policy, &lantern_linux_ops, &context);
        if (!result) result = lantern_launch(&context, &lantern_linux_ops, &context);
    }
    if (a.client >= 0) {
        if (!result) {
            send_message(&a, "{\"schema\":\"sage.lantern.desktop-loader-broker.v1\","
                "\"status\":\"released\"}\n", -1);
        } else if (a.aborted) {
            send_message(&a, "{\"schema\":\"sage.lantern.desktop-loader-broker.v1\","
                "\"status\":\"aborted\"}\n", -1);
        } else {
            send_message(&a, "{\"schema\":\"sage.lantern.desktop-loader-broker.v1\","
                "\"status\":\"error\"}\n", -1);
        }
        close(a.client);
    }
    if (listener >= 0) close(listener);
    unlink(socket_path);
    lantern_linux_close(&context);
    return result ? 1 : 0;
}
