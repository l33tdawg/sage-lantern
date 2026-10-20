#!/bin/sh
# Build the disabled BPF-LSM object from a kernel BTF file. This never loads it.
set -eu

OUTPUT=${1:?usage: build-native-bpf.sh OUTPUT [BTF]}
BTF=${2:-/sys/kernel/btf/vmlinux}
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
SOURCE="$ROOT/software/security"

if [ "$(uname -s)" != Linux ]; then
    echo "linux_build_required" >&2
    exit 3
fi
for command in bpftool clang llvm-objdump; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "${command}_required" >&2
        exit 4
    }
done
test -r "$BTF" || { echo "btf_required" >&2; exit 5; }

TEMPORARY=$(mktemp -d "${TMPDIR:-/tmp}/sage-native-bpf.XXXXXX")
trap 'rm -rf -- "$TEMPORARY"' EXIT HUP INT TERM
bpftool btf dump file "$BTF" format c > "$TEMPORARY/vmlinux.h"
cp "$SOURCE/desktop_guard.bpf.c" "$SOURCE/desktop_guard_policy.h" "$TEMPORARY/"
clang -target bpf -D__TARGET_ARCH_arm64 -O2 -g \
    -Wall -Wextra -Werror -Wno-missing-declarations -Wno-unused-parameter \
    -fdebug-compilation-dir=/src -ffile-prefix-map="$TEMPORARY"=/src \
    -I"$TEMPORARY" -I"$SOURCE" \
    -c "$TEMPORARY/desktop_guard.bpf.c" -o "$TEMPORARY/desktop_guard.bpf.o"
llvm-objdump -h "$TEMPORARY/desktop_guard.bpf.o" > "$TEMPORARY/sections.txt"
cp "$TEMPORARY/desktop_guard.bpf.o" "$OUTPUT"
chmod 0644 "$OUTPUT"
