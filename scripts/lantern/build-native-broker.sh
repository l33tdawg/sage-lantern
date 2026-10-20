#!/bin/sh
# Build the fitted Linux broker only on a Linux host with reviewed headers.
set -eu

OUTPUT=${1:?usage: build-native-broker.sh OUTPUT}
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
SOURCE="$ROOT/software/security/desktop_loader_candidate"

if [ "$(uname -s)" != Linux ]; then
    echo "linux_build_required" >&2
    exit 3
fi
if ! pkg-config --exists libbpf openssl; then
    echo "libbpf_and_openssl_dev_required" >&2
    exit 4
fi
if ! command -v cc >/dev/null 2>&1; then
    echo "c_compiler_required" >&2
    exit 5
fi

cc -std=c11 -O2 -Wall -Wextra -Werror \
    -I"$SOURCE" \
    "$SOURCE/broker_agent.c" "$SOURCE/loader.c" \
    $(pkg-config --cflags --libs libbpf openssl) -lelf -lz \
    -o "$OUTPUT"
chmod 0755 "$OUTPUT"
