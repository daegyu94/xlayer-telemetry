"""Compatibility path for :mod:`xlayer_telemetry.collectors.topology_textfile`."""

import sys

from .collectors import topology_textfile as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
