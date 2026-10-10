# SPDX-License-Identifier: Apache-2.0
"""Integration controls consume fixtures from an explicit Meshticulum checkout.

Only the source test harness crosses this development boundary. Wheels and
device bundles never require or copy another repository's test tree.
"""
import os
from pathlib import Path
import tests

source = os.environ.get('MESHTICULUM_SOURCE')
if not source:
    raise RuntimeError('Set MESHTICULUM_SOURCE to the pinned Meshticulum source checkout for integration tests.')
fixtures = Path(source).resolve() / 'tests'
if not (fixtures / 'test_meshticulum_private_carrier.py').is_file():
    raise RuntimeError('Meshticulum source fixture checkout is incomplete.')
tests.__path__.append(str(fixtures))
