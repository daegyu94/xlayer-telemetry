"""Compatibility path for :mod:`xlayer_telemetry.analysis.evidence_quality`."""

import sys

from .analysis import evidence_quality as _implementation

sys.modules[__name__] = _implementation
