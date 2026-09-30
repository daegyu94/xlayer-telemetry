"""Compatibility path for :mod:`xlayer_telemetry.analysis.diagnosis_analysis`."""

import sys

from .analysis import diagnosis_analysis as _implementation

sys.modules[__name__] = _implementation
