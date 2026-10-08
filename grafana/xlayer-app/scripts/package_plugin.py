#!/usr/bin/env python3
"""Package the built unsigned App under its Grafana plugin ID; never publish or sign."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

APP = Path(__file__).resolve().parents[1]
PLUGIN_ID = 'xlayer-telemetry-app'


def package(dist, output):
    if output.resolve().is_relative_to(dist.resolve()):
        raise ValueError('Package output must be outside the built distribution')
    metadata = json.loads((dist / 'plugin.json').read_text())
    if metadata.get('id') != PLUGIN_ID or metadata.get('type') != 'app':
        raise ValueError('Unexpected Grafana plugin identity')
    for required in ('plugin.json', 'module.js', 'img/logo.svg'):
        if not (dist / required).is_file():
            raise ValueError('Build artifact missing: ' + required)
    if (dist / 'MANIFEST.txt').exists():
        raise ValueError('This unsigned packaging path does not modify/package signed distributions')
    members = sorted(path for path in dist.rglob('*') if path.is_file() or path.is_symlink())
    if any(path.is_symlink() for path in members):
        raise ValueError('Plugin package must not contain symbolic links')
    if len(members) > 1000 or sum(path.stat().st_size for path in members) > 100 * 1024 * 1024:
        raise ValueError('Plugin package exceeds bounded file/size budget')
    output.mkdir(parents=True, exist_ok=True)
    version = metadata.get('info', {}).get('version')
    if not isinstance(version, str) or not version or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_' for c in version):
        raise ValueError('Invalid plugin version')
    destination = output / f'{PLUGIN_ID}-{version}-unsigned.zip'
    if destination.exists():
        raise ValueError('Archive already exists; choose a new output directory')
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in members:
            info = zipfile.ZipInfo(f'{PLUGIN_ID}/{path.relative_to(dist).as_posix()}', date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    (output / (destination.name + '.sha256')).write_text(f'{digest}  {destination.name}\n')
    print(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=APP / 'dist')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package(args.dist.resolve(), args.output.resolve())


if __name__ == '__main__':
    main()
