#!/usr/bin/env python3
"""Prepare a clean, already-cloned pinned MeshCore tree. Never flashes hardware."""
import argparse
import pathlib
import shutil
import subprocess

BASE = "d92964352441e53b93e8667b802e04f6e072b39e"
HERE = pathlib.Path(__file__).resolve().parent


def git(tree, *args):
    return subprocess.check_output(["git", "-C", str(tree), *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=pathlib.Path)
    args = parser.parse_args()
    tree = args.checkout.resolve()
    if git(tree, "rev-parse", "HEAD") != BASE:
        parser.error(f"checkout must be at {BASE}")
    if git(tree, "status", "--porcelain", "--untracked-files=normal"):
        parser.error("checkout must be clean; use a fresh temporary clone")
    subprocess.run(["git", "-C", str(tree), "apply", "--check", str(HERE / "pinned-r5.patch")], check=True)
    subprocess.run(["git", "-C", str(tree), "apply", str(HERE / "pinned-r5.patch")], check=True)
    target = tree / "variants" / "lantern_xiao"
    target.mkdir()
    shutil.copyfile(HERE / "platformio.ini", target / "platformio.ini")
    print(f"Prepared {tree}; build with pio run -d {tree} -e lantern_xiao_s3_wio_usb")


if __name__ == "__main__":
    main()
