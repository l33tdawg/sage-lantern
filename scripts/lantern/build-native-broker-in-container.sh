#!/bin/sh
# Build the fitted broker with the pinned Linux builder. On Linux this uses the
# host toolchain; on another host it requires Docker and never runs the result.
set -eu

OUTPUT=${1:?usage: build-native-broker-in-container.sh OUTPUT}
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
IMAGE=${SAGE_NATIVE_BUILDER_IMAGE:-sage-lantern-native-broker-builder:24.04}

if [ "$(uname -s)" = Linux ]; then
    exec "$ROOT/scripts/lantern/build-native-broker.sh" "$OUTPUT"
fi

command -v docker >/dev/null 2>&1 || {
    echo "docker_required_for_non_linux_build" >&2
    exit 6
}

case "$(uname -m)" in
    arm64|aarch64) platform=linux/arm64 ;;
    x86_64|amd64) platform=linux/amd64 ;;
    *) echo "unsupported_builder_architecture" >&2; exit 7 ;;
esac

docker build --pull=false --platform "$platform" \
    --file "$ROOT/scripts/lantern/native-broker-builder.Dockerfile" \
    --tag "$IMAGE" "$ROOT"
TEMPORARY=$(mktemp -d "${TMPDIR:-/tmp}/sage-native-broker.XXXXXX")
trap 'rm -rf -- "$TEMPORARY"' EXIT HUP INT TERM
docker run --rm --network none --platform "$platform" \
    -v "$ROOT:/src:ro" -v "$TEMPORARY:/out" "$IMAGE" \
    /out/sage-lantern-desktop-loader
cp "$TEMPORARY/sage-lantern-desktop-loader" "$OUTPUT"
chmod 0755 "$OUTPUT"
