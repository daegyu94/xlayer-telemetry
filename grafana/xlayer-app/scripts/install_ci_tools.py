#!/usr/bin/env python3
"""Install only the three pinned Linux/amd64 demo tools, after SHA256 verification."""
import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile

# Publisher checksums, not hashes computed after accepting an unverified download:
# dl.grafana.com/oss/release/grafana-12.1.0.linux-amd64.tar.gz.sha256
# prometheus/prometheus v3.5.0 sha256sums.txt; grafana/loki v3.7.3 SHA256SUMS.
ASSETS = (
    ('grafana.tar.gz', 'https://dl.grafana.com/oss/release/grafana-12.1.0.linux-amd64.tar.gz',
     '69923cf95824008a6a7529f242295ecc2bc6b1dc4e5142c889971e88407d8712'),
    ('prometheus.tar.gz', 'https://github.com/prometheus/prometheus/releases/download/v3.5.0/prometheus-3.5.0.linux-amd64.tar.gz',
     'e811827af26d822afb09a4f28314f61b618b12cff5369835a67f674d8b46f39a'),
    ('loki.zip', 'https://github.com/grafana/loki/releases/download/v3.7.3/loki-linux-amd64.zip',
     'cd28bc1e12f005c39fdf3c49e6be793206749b0f16db5eed742727b484a027ae'),
)


def verified(path, expected):
    if not path.is_file():
        return False
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest() == expected


def safe_name(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts:
        raise ValueError('Unsafe release archive path')
    return path


def extract_target(output, name):
    target = output.joinpath(*safe_name(name).parts)
    if any(path.is_symlink() for path in (target, *target.parents)):
        raise ValueError('Release extraction path contains a symbolic link')
    return target


def unpack(archive, output):
    # Extract files/directories only; no links or devices from downloaded archives.
    if archive.suffix == '.zip':
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                target = extract_target(output, member.filename)
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Release archive contains a symbolic link')
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(member) as source, target.open('wb') as dest:
                        shutil.copyfileobj(source, dest)
        (output / 'loki-linux-amd64').chmod(0o755)
    else:
        with tarfile.open(archive) as package:
            for member in package:
                target = extract_target(output, member.name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.extractfile(member) as source, target.open('wb') as dest:
                        shutil.copyfileobj(source, dest)
                    target.chmod(member.mode & 0o777)
                else:
                    raise ValueError('Release archive contains unsupported link/device')


def install(output):
    output.mkdir(parents=True, exist_ok=True)
    for name, url, digest in ASSETS:
        archive = output / name
        if not verified(archive, digest):
            print('Downloading pinned release: ' + name, flush=True)
            descriptor, partial = tempfile.mkstemp(prefix=name + '.', suffix='.part', dir=output)
            try:
                with os.fdopen(descriptor, 'wb') as dest, urllib.request.urlopen(url, timeout=90) as source:
                    shutil.copyfileobj(source, dest)
                if not verified(Path(partial), digest):
                    raise ValueError('Publisher SHA256 mismatch: ' + name)
                os.replace(partial, archive)
            finally:
                Path(partial).unlink(missing_ok=True)
        unpack(archive, output)
        print('Verified and installed ' + name, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        parser.error('This isolated CI installer supports Linux amd64 only')
    install(args.output.resolve())


if __name__ == '__main__':
    main()
