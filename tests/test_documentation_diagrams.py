"""Keep diagram sources, published images and documentation references together."""
import hashlib
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

import pytest

from scripts import render_diagrams as diagrams

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "docs/diagrams"
SVG = ROOT / "docs/figures/diagrams"
NS = {"svg": "http://www.w3.org/2000/svg"}


def test_all_diagram_sources_have_accessible_current_svg():
    sources = {path.stem: path for path in SOURCES.glob("[!_]*.d2")}
    assert sources and set(sources) == {path.stem for path in SVG.glob("*.svg")}
    for name, source in sources.items():
        text = (SVG / f"{name}.svg").read_text()
        image = ET.fromstring(text)
        assert image.tag == "{http://www.w3.org/2000/svg}svg"
        assert image.attrib["role"] == "img"
        assert image.find("svg:title", NS).text
        assert image.find("svg:desc", NS).text
        assert image.attrib["aria-labelledby"].split() == [
            image.find("svg:title", NS).attrib["id"], image.find("svg:desc", NS).attrib["id"]]
        assert float(image.attrib["width"]) > 0 and float(image.attrib["height"]) > 0
        for label in image.findall(".//svg:text", NS):
            font = re.search(r"font-size:(\d+(?:\.\d+)?)px", label.attrib.get("style", ""))
            assert font and float(font[1]) >= 16, (name, label.text)
        digest = hashlib.sha256(source.read_bytes() + (SOURCES / "_style.d2").read_bytes()
                                + diagrams.VERSION.encode()).hexdigest()
        assert f"source-sha256:{digest}" in text, f"Regenerate {name}.svg"
        assert not image.findall(".//svg:script", NS)
        assert not image.findall(".//svg:foreignObject", NS)


def markdown_files():
    return [ROOT / path for path in subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.md"], cwd=ROOT, text=True).splitlines()]


def test_diagram_references_have_alt_text_and_resolve_from_each_document():
    referenced = set()
    for document in markdown_files():
        for alt, link in re.findall(r"!\[([^\]]*)\]\(([^)]+\.svg)\)", document.read_text()):
            assert alt.strip(), document
            target = (document.parent / link).resolve()
            assert target.is_file(), (document, link)
            referenced.add(target)
    assert referenced == {path.resolve() for path in SVG.glob("*.svg")}


def test_markdown_text_blocks_do_not_reintroduce_ascii_diagrams():
    for document in markdown_files():
        for tag, block in re.findall(r"^```([^\n]*)\n(.*?)^```", document.read_text(), re.M | re.S):
            if tag.strip() in {"", "text", "ascii", "mermaid"}:
                assert not re.search(r"\+--|-->|[└├│]|^\s*[|v]\s*$", block, re.M), document


def test_svg_metadata_escapes_text_and_requires_description(tmp_path):
    source = tmp_path / "test.d2"
    source.write_text('# Title: A & B\n# Description: x < y\na -> b\n')
    result = diagrams.annotate('<svg xmlns="http://www.w3.org/2000/svg"></svg>', source, b"style")
    assert "A &amp; B" in result and "x &lt; y" in result
    assert ET.fromstring(result).find("svg:title", NS).text == "A & B"
    source.write_text('# Title: Example\na -> b\n')
    with pytest.raises(ValueError, match="missing # Description"):
        diagrams.annotate("<svg></svg>", source, b"style")


def test_check_detects_generated_image_drift_without_writing(tmp_path, monkeypatch):
    sources = tmp_path / "docs/diagrams"
    sources.mkdir(parents=True)
    (sources / "_style.d2").write_text("")
    (sources / "example.d2").write_text('# Title: Example\n# Description: A to B\na -> b\n')

    def fake_d2(args, **kwargs):
        if "--version" in args:
            return subprocess.CompletedProcess(args, 0, stdout="v" + diagrams.VERSION)
        Path(args[-1]).write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(diagrams.subprocess, "run", fake_d2)
    diagrams.render("d2", root=tmp_path)
    saved = tmp_path / "docs/figures/diagrams/example.svg"
    saved.write_text("stale output")
    with pytest.raises(ValueError, match="SVGs need regeneration"):
        diagrams.render("d2", check=True, root=tmp_path)
    assert saved.read_text() == "stale output"


def test_install_rejects_archive_checksum_before_writing(tmp_path, monkeypatch):
    import io

    monkeypatch.setattr(diagrams, "LOCAL_BINARY", tmp_path / "d2")
    monkeypatch.setattr(diagrams.platform, "system", lambda: "Linux")
    monkeypatch.setattr(diagrams.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(diagrams.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"corrupt archive"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        diagrams.install()
    assert not (tmp_path / "d2").exists()
