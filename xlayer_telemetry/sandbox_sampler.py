"""Compatibility path for :mod:`xlayer_telemetry.collectors.sandbox_sampler`."""

import sys

from .collectors import sandbox_sampler as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
