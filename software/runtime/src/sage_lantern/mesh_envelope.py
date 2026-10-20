# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; the wire protocol implementation lives in Meshticulum."""
import sys
from meshticulum.privacy import mesh_envelope as _canonical
sys.modules[__name__] = _canonical
