#!/usr/bin/env python3
"""Render repository D2 sources to committed SVGs; no runtime dependency."""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import io
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.9.0"
# Official immutable v0.9.0 release SHA256SUMS; verify before executing binaries.
DIGESTS = {
    "linux-amd64": "5669ddc46b99e942cc96078f4a4e36d5e62103348f4c05179ede27802fdd87a9",
    "linux-arm64": "ac2c028697199479acb321db1e3d68caee9f2ba492ed73caa3cd13f3829bf913",
    "macos-amd64": "cad39576a480d6bb02ea142fef1726647914b0d2da51ccc9b30b660a2b1babf0",
    "macos-arm64": "eaf6c0c143e56dd9fa97bfb6df25ea9c1ebce40245f056a0768cf1a6c15d3064",
}
LOCAL_BINARY = ROOT / "artifacts" / "docs-tools" / f"d2-{VERSION}" / "d2"


def install() -> Path:
    system = {"Linux": "linux", "Darwin": "macos"}.get(platform.system())
    arch = {"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine())
    key = f"{system}-{arch}"
    if key not in DIGESTS:
        raise ValueError("Automatic D2 install supports Linux/macOS x86_64 and ARM64. Use --d2 PATH on other platforms.")
    if LOCAL_BINARY.is_file():
        return LOCAL_BINARY
    url = f"https://github.com/d2lang/d2/releases/download/v{VERSION}/d2-v{VERSION}-{key}.tar.gz"
    with urllib.request.urlopen(url, timeout=60) as response:
        archive = response.read(100 * 1024 * 1024 + 1)
    if len(archive) > 100 * 1024 * 1024 or hashlib.sha256(archive).hexdigest() != DIGESTS[key]:
        raise ValueError("D2 release checksum mismatch; no binary installed.")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        member = tar.getmember(f"d2-v{VERSION}/bin/d2")
        if not member.isfile() or member.size > 150 * 1024 * 1024:
            raise ValueError("Invalid D2 binary in release archive.")
        with tar.extractfile(member) as source:
            binary = source.read()
    LOCAL_BINARY.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=LOCAL_BINARY.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(binary)
    try:
        temporary.chmod(0o755)
        os.replace(temporary, LOCAL_BINARY)
    finally:
        temporary.unlink(missing_ok=True)
    return LOCAL_BINARY


def annotate(svg: str, source: Path, shared_style: bytes) -> str:
    text = source.read_text()
    metadata = {}
    for key in ("Title", "Description"):
        match = re.search(rf"^# {key}: (.+)$", text, re.M)
        if not match:
            raise ValueError(f"{source.name}: missing # {key}: metadata")
        metadata[key] = escape(match[1])
    digest = hashlib.sha256(source.read_bytes() + shared_style + VERSION.encode()).hexdigest()
    opening = re.search(r"<svg\b[^>]*>", svg)
    if not opening:
        raise ValueError(f"{source.name}: D2 did not produce SVG")
    prefix = f"xlayer-{source.stem}"
    tag = opening[0][:-1] + f' role="img" aria-labelledby="{prefix}-title {prefix}-desc">'
    description = (f'<title id="{prefix}-title">{metadata["Title"]}</title>'
                   f'<desc id="{prefix}-desc">{metadata["Description"]}</desc>'
                   f'<!-- Generated from docs/diagrams/{source.name}; D2 {VERSION}; source-sha256:{digest} -->')
    return svg[:opening.start()] + tag + description + svg[opening.end():]


def render(binary: str, *, check: bool = False, root: Path = ROOT) -> int:
    version = subprocess.run([binary, "--version"], check=True, capture_output=True, text=True, timeout=10)
    if version.stdout.strip().removeprefix("v") != VERSION:
        raise ValueError(f"Use D2 {VERSION} for reproducible SVGs (python scripts/render_diagrams.py --install).")
    sources = root / "docs" / "diagrams"
    destination = root / "docs" / "figures" / "diagrams"
    style = (sources / "_style.d2").read_bytes()
    changes = []
    # Ignore user layout/theme overrides so local generation and CI agree.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("D2_") and key not in {"DEBUG", "OMIT_VERSION"}}
    with tempfile.TemporaryDirectory(prefix="xlayer-d2-render-") as directory:
        for source in sorted(sources.glob("*.d2")):
            if source.name.startswith("_"):
                continue
            target = Path(directory) / f"{source.stem}.svg"
            subprocess.run([binary, "--layout=elk", "--theme=0", "--pad=12", "--elk-nodeNodeBetweenLayers=40",
                            "--elk-padding=[top=24,left=24,bottom=24,right=24]",
                            "--elk-edgeNodeBetweenLayers=24", "--elk-nodeSelfLoop=32", "--scale=1", "--salt=" + source.stem,
                            "--timeout=60", str(source), str(target)],
                           check=True, capture_output=True, text=True, timeout=70, env=env)
            result = annotate(target.read_text(), source, style)
            saved = destination / target.name
            if not saved.exists() or saved.read_text() != result:
                changes.append((saved, result))
    if check and changes:
        names = ", ".join(path.name for path, _ in changes)
        raise ValueError(f"SVGs need regeneration: {names}. Run python scripts/render_diagrams.py.")
    if not check:
        destination.mkdir(parents=True, exist_ok=True)
        for path, text in changes:
            path.write_text(text)
    print(f"D2 {VERSION}: {len(list(sources.glob('[!_]*.d2')))} diagrams {'checked' if check else 'rendered'}, {len(changes)} changed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Install pinned D2 in artifacts/docs-tools and render diagrams")
    parser.add_argument("--check", action="store_true", help="Fail if committed SVGs differ from regenerated sources")
    parser.add_argument("--d2", help="Path to pinned D2 binary")
    args = parser.parse_args()
    try:
        binary = str(install()) if args.install else args.d2 or (str(LOCAL_BINARY) if LOCAL_BINARY.exists() else shutil.which("d2"))
        if not binary:
            raise ValueError("D2 is missing. Run python scripts/render_diagrams.py --install.")
        return render(binary, check=args.check)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", "") or ""
        parser.exit(1, f"Diagram generation failed: {error}\n{detail}")


if __name__ == "__main__":
    raise SystemExit(main())
