"""Compatibility path for :mod:`xlayer_telemetry.analysis.llm_diagnosis`."""

import sys

from .analysis import llm_diagnosis as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
