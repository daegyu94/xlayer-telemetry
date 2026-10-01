"""VM validation preflight must not create guests for unverified input."""
from pathlib import Path
import subprocess
import sys


def test_wrong_image_digest_rejected_before_guest_creation(tmp_path):
    image = tmp_path / 'untrusted.img'
    image.write_bytes(b'not a VM image')
    output = tmp_path / 'state'
    result = subprocess.run([sys.executable, '-m', 'examples.multinode.validate_vms',
        '--image', str(image), '--image-sha256', '0'*64,
        '--node-exporter', '/nonexistent/exporter', '--prometheus', '/nonexistent/prometheus',
        '--output', str(output)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and 'SHA256 mismatch' in result.stderr
    assert not output.exists()
