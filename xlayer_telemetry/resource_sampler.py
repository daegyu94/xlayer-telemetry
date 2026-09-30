"""Compatibility path for :mod:`xlayer_telemetry.collectors.resource_sampler`."""

import sys

from .collectors import resource_sampler as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
