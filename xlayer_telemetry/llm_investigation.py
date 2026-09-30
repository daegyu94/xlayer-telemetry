"""Compatibility path for :mod:`xlayer_telemetry.analysis.llm_investigation`."""

import sys

from .analysis import llm_investigation as _implementation

sys.modules[__name__] = _implementation
