"""Compatibility path for :mod:`xlayer_telemetry.analysis.diagnostics`."""

import sys

from .analysis import diagnostics as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
