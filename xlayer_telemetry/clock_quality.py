"""Compatibility path for :mod:`xlayer_telemetry.analysis.clock_quality`."""

import sys

from .analysis import clock_quality as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
