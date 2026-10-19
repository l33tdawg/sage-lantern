#!/usr/bin/env python3
"""Wait for the signed native status route before starting the auth broker."""
import argparse
from pathlib import Path
import time


def wait_ready(probe, *, timeout=120, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            if probe() is True:
                return
        except Exception:
            pass
        sleep(min(1, max(0, deadline - clock())))
    raise TimeoutError('native_auth_status_not_ready')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operator-key', required=True, type=Path)
    args = parser.parse_args()
    from sage_lantern.vault_setup import RootFileSigner, VaultSetup
    signer = RootFileSigner(args.operator_key)

    def probe():
        # This performs only the fixed ledger-status GET. A fresh probe does
        # not reset or alter the running broker's sticky cleanup evidence.
        vault = VaultSetup(signer, enable_allowed=lambda: False)
        vault.status()
        return vault.cleanup_confirmed is True

    wait_ready(probe)


if __name__ == '__main__':
    main()
