# Pinned bootstrap image for the source-only Linux broker build. The apt
# package versions are recorded with each review; the base image digest is
# immutable so a builder change is visible in the build command.
FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential pkg-config libbpf-dev libelf-dev zlib1g-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
ENTRYPOINT ["/src/scripts/lantern/build-native-broker.sh"]
