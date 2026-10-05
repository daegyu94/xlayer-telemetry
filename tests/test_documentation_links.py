"""Resolve guide and contributor Markdown links without a backend or network."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/daegyu94/xlayer-telemetry/blob/main/"


def prose(text):
    return re.sub(r"^(`{3,}|~{3,})[^\n]*\n.*?^\1\s*$", "", text, flags=re.M | re.S)


def anchors(text):
    counts = {}
    result = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", prose(text), re.M):
        # GitHub and MyST heading anchors preserve Unicode letters and underscores.
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        result.add(f"{slug}-{count}" if count else slug)
        counts[slug] = count + 1
    return result


def broken_links(document):
    failures = []
    for link in re.findall(r"!?\[[^\]]*\]\((<[^>]+>|[^\s()]+)(?:\s+\"[^\"]*\")?\)", prose(document.read_text())):
        link = link.strip("<>")
        if link.startswith(REPOSITORY):
            target = ROOT / unquote(urlsplit(link[len(REPOSITORY):]).path)
        else:
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc:
                continue
            target = document.parent / unquote(parsed.path) if parsed.path else document
        if not target.exists():
            failures.append(f"{document.relative_to(ROOT) if document.is_relative_to(ROOT) else document.name}: {link} (missing path)")
        elif target.suffix == ".md" and urlsplit(link).fragment:
            fragment = unquote(urlsplit(link).fragment)
            if fragment not in anchors(target.read_text()):
                failures.append(f"{document.name}: {link} (missing heading)")
    return failures


def test_documentation_file_and_heading_links_resolve():
    documents = [*ROOT.glob("*.md"), ROOT / "scripts/README.md", ROOT / "config/README.md"]
    documents.extend(ROOT.glob("examples/**/README.md"))
    documents.extend(path for path in (ROOT / "docs").rglob("*.md") if "_build" not in path.parts)
    failures = [failure for document in sorted(set(documents)) for failure in broken_links(document)]
    assert not failures, "\n".join(failures)


def test_link_checker_finds_missing_paths_and_fragments_but_ignores_code(tmp_path):
    destination = tmp_path / "destination.md"
    destination.write_text("# Target\n\n## 한글 Heading\n")
    document = tmp_path / "README.md"
    document.write_text("[ok](destination.md#한글-heading)\n[bad](destination.md#gone)\n"
                        "[missing](missing.md)\n```md\n[sample](not-a-real-file.md)\n```\n")
    failures = broken_links(document)
    assert len(failures) == 2
    assert any("missing heading" in item for item in failures)
    assert any("missing path" in item for item in failures)
