/* SPDX-License-Identifier: GPL-2.0-only
 * This executable ONLY parses a digest-bound object. No attach/launch command. */
#include "linux_backend.h"
#include <errno.h>
#include <openssl/evp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv)
{
    if (argc != 2) { fputs("usage: desktop-loader-inspect OBJECT (inspection only)\n", stderr); return 64; }
    FILE *file = fopen(argv[1], "rb"); if (!file) return 1;
    size_t limit = 16 * 1024 * 1024 + 1; void *bytes = malloc(limit);
    if (!bytes) { fclose(file); return 1; }
    size_t length = fread(bytes, 1, limit, file);
    int bad = ferror(file) || length == limit; fclose(file);
    struct lantern_policy p = {0}; unsigned n = 0;
    if (bad || !EVP_Digest(bytes, length, p.object_sha256, &n, EVP_sha256(), NULL) || n != 32) {
        free(bytes); return 1;
    }
    struct lantern_linux context;
    int result = lantern_linux_open(&context, bytes, length, &p);
    if (!result) {
        /* The shipped native prepare path must refuse, even as container root. */
        if (lantern_linux_ops.prepare(&context, &p) != -EOPNOTSUPP) result = -EPERM;
        lantern_linux_close(&context);
    }
    free(bytes);
    if (result) return 1;
    puts("13 hooks and pidfd task-storage map parsed; prepare=disabled; kernel_enforcement_qualified=false");
    return 0;
}
