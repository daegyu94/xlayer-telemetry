"""VM validation preflight must not create guests for unverified input."""
from pathlib import Path
import hashlib
import io
import subprocess
import sys
import tarfile

from examples.multinode.validate_vms import payload


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


def test_cli_vm_tools_checked_before_guest_creation(tmp_path):
    image = tmp_path / 'image'
    image.write_bytes(b'verified fixture')
    output = tmp_path / 'state'
    result = subprocess.run([sys.executable, '-m', 'examples.multinode.validate_vms',
        '--image', str(image), '--image-sha256', hashlib.sha256(image.read_bytes()).hexdigest(),
        '--node-exporter', '/nonexistent/exporter', '--prometheus', '/nonexistent/prometheus',
        '--cli-tools-dir', str(tmp_path), '--output', str(output)],capture_output=True,text=True,timeout=5)
    assert result.returncode == 2 and 'requires executable' in result.stderr
    assert not output.exists()


def test_cli_payload_copies_symlinked_binary_instead_of_host_link(tmp_path):
    binary = tmp_path / 'real-alloy'
    binary.write_bytes(b'fixture binary')
    (tmp_path / 'alloy-linux-amd64').symlink_to(binary)
    exporter = tmp_path / 'exporter'
    exporter.write_bytes(b'fixture exporter')
    with tarfile.open(fileobj=io.BytesIO(payload(exporter,tmp_path)),mode='r:gz') as archive:
        assert archive.getmember('tools/alloy-linux-amd64').isfile()
        assert archive.extractfile('tools/alloy-linux-amd64').read() == b'fixture binary'
        assert archive.getmember('scripts/verl_local.sh').isfile()
