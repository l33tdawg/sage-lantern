#!/usr/bin/env bash

set -Eeuo pipefail

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

readonly IMAGE=/home/radxa/rock-5b-plus_bookworm_kde_r7.output_512.img.xz
readonly IMAGE_SHA512=3e4ddd8eb88dd9d8b2232e69212f45913f21cf8ebb30953f4f637b60876b250722beae744021502b49cf4de900f341878b6fa4025fcfdab38713d9f90b088ebb
readonly SOURCE_SHA256=063bc9500c8406fc86d4926eddefc938e4a114b6720251f635f13089bc871024
readonly IMAGE_BYTES=7726932992
readonly TARGET=/dev/nvme0n1
readonly TARGET_BYTES=512110190592
readonly TARGET_MODEL='Netac NVMe SSD 512GB'
readonly TARGET_SERIAL=AA20260312512G069623
readonly SPI_DEVICE=/dev/mtd0
readonly SPI_BYTES=16777216
readonly UBOOT_DIR=/usr/lib/u-boot/rock-5b-plus
readonly SPI_BACKUP=/home/radxa/unit-a-spi-before-nvme.bin

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

[[ ${EUID} -eq 0 ]] || die 'Run this script through sudo.'

printf '%s\n' 'Blocking suspend for this boot while migration is active...'
systemctl mask --runtime sleep.target suspend.target hibernate.target hybrid-sleep.target

printf '%s\n' '=== Unit A preflight ==='
root_source=$(findmnt -n -o SOURCE /)
[[ ${root_source} == /dev/mmcblk1p3 ]] || die "Expected microSD root /dev/mmcblk1p3; found ${root_source}."

[[ -b ${TARGET} ]] || die "Target ${TARGET} is missing."
target_bytes=$(lsblk -b -dn -o SIZE "${TARGET}" | xargs)
target_model=$(lsblk -dn -o MODEL "${TARGET}" | xargs)
target_serial=$(lsblk -dn -o SERIAL "${TARGET}" | xargs)
[[ ${target_bytes} == "${TARGET_BYTES}" ]] || die "Unexpected NVMe size: ${target_bytes}."
[[ ${target_model} == "${TARGET_MODEL}" ]] || die "Unexpected NVMe model: ${target_model}."
[[ ${target_serial} == "${TARGET_SERIAL}" ]] || die "Unexpected NVMe serial: ${target_serial}."

if lsblk -nrpo MOUNTPOINT "${TARGET}" | grep -qv '^[[:space:]]*$'; then
    die "A filesystem on ${TARGET} is mounted."
fi

[[ -f ${IMAGE} ]] || die "Image is missing: ${IMAGE}."
actual_sha512=$(sha512sum "${IMAGE}" | awk '{print $1}')
[[ ${actual_sha512} == "${IMAGE_SHA512}" ]] || die 'Compressed image SHA-512 mismatch.'
xz -t "${IMAGE}"

printf '%s\n' 'Computing decompressed source hash...'
actual_source_sha256=$(xzcat "${IMAGE}" | sha256sum | awk '{print $1}')
[[ ${actual_source_sha256} == "${SOURCE_SHA256}" ]] || die 'Decompressed source SHA-256 mismatch.'

[[ -c ${SPI_DEVICE} ]] || die "SPI device ${SPI_DEVICE} is missing."
[[ -f ${UBOOT_DIR}/setup.sh && -f ${UBOOT_DIR}/idbloader.img && -f ${UBOOT_DIR}/u-boot.itb ]] || \
    die 'ROCK 5B+ vendor bootloader files are incomplete.'

printf '%s\n' 'Preflight passed:'
printf '  root:   %s\n' "${root_source}"
printf '  target: %s | %s bytes | %s | %s\n' "${TARGET}" "${target_bytes}" "${target_model}" "${target_serial}"
printf '  image:  SHA-512 and decompressed SHA-256 verified\n'

printf '%s\n' '=== Backing up current SPI ==='
dd if="${SPI_DEVICE}" of="${SPI_BACKUP}" bs=1M status=progress
[[ $(stat -c %s "${SPI_BACKUP}") == "${SPI_BYTES}" ]] || die 'SPI backup has the wrong size.'
sha256sum "${SPI_BACKUP}" | tee /home/radxa/unit-a-spi-before-nvme.sha256
chown radxa:radxa "${SPI_BACKUP}" /home/radxa/unit-a-spi-before-nvme.sha256

printf '%s\n' '=== Updating SPI with the installed Radxa ROCK 5B+ bootloader ==='
rsetup update_spinor rock-5b-plus

expected_spi=$(mktemp /tmp/unit-a-spi-expected.XXXXXX)
trap 'rm -f "${expected_spi:-}"' EXIT
truncate -s "${SPI_BYTES}" "${expected_spi}"
dd conv=notrunc,fsync if="${UBOOT_DIR}/idbloader.img" of="${expected_spi}" bs=512 seek=64 status=none
dd conv=notrunc,fsync if="${UBOOT_DIR}/u-boot.itb" of="${expected_spi}" bs=512 seek=16384 status=none
cmp -n "${SPI_BYTES}" "${expected_spi}" "${SPI_DEVICE}" || die 'SPI read-back does not match the vendor bootloader image.'
printf '%s\n' 'SPI FLASH VERIFIED.'

printf '%s\n' "=== Writing ${IMAGE_BYTES} decompressed bytes to ${TARGET} ==="
xzcat "${IMAGE}" | dd of="${TARGET}" bs=4M conv=fsync status=progress
sync
blockdev --flushbufs "${TARGET}"

printf '%s\n' "=== Reading back exactly ${IMAGE_BYTES} bytes from ${TARGET} ==="
target_sha256=$(head -c "${IMAGE_BYTES}" "${TARGET}" | sha256sum | awk '{print $1}')
[[ ${target_sha256} == "${SOURCE_SHA256}" ]] || die "NVMe read-back mismatch: ${target_sha256}."

printf '%s\n' 'NVME FLASH VERIFIED: source and exact-byte read-back hashes match.'
printf '%s\n' 'Migration write is complete. Do not remove the microSD until the board is shut down cleanly.'
