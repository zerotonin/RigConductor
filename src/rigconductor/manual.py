# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — manual                                          ║
# ║  « one Markdown file, read in the app and printed to PDF »       ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Locates the user manual (docs/manual/user_manual.md) and parses ║
# ║  its headings into sections, so the in-app Help window, F1 on    ║
# ║  every wizard page and the PDF share one source of truth.        ║
# ╚══════════════════════════════════════════════════════════════════╝
"""User-manual helpers that need no Qt.

The manual is written in the Markdown that Qt's ``QTextDocument.setMarkdown``
understands (CommonMark and GitHub tables): no admonitions, attribute lists
or tabs. Slugs follow Python-Markdown's ``toc`` so ``#links`` also work on a
docs site.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANUAL_DIR = REPO_ROOT / "docs" / "manual"
MANUAL_PATH = MANUAL_DIR / "user_manual.md"

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_LINK = re.compile(r"(!?)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


@dataclass(frozen=True)
class Section:
    level: int
    title: str
    slug: str
    line: int  # 1-based line in the source


def load_manual(path: Path = MANUAL_PATH) -> str:
    """The manual's Markdown source."""
    return path.read_text(encoding="utf-8")


def slugify(title: str) -> str:
    """Python-Markdown's default ``toc`` slug."""
    text = re.sub(r"[*_`]", "", title)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def parse_sections(markdown: str) -> list[Section]:
    """ATX headings in document order, skipping fenced code blocks."""
    sections: list[Section] = []
    in_fence = False
    for number, line in enumerate(markdown.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        m = None if in_fence else _HEADING.match(line)
        if m:
            sections.append(Section(len(m.group(1)), m.group(2), slugify(m.group(2)), number))
    return sections


def image_targets(markdown: str) -> list[str]:
    """Relative paths of every ``![alt](path)`` picture."""
    return [target for bang, target in _LINK.findall(markdown) if bang]


def anchor_targets(markdown: str) -> list[str]:
    """Slugs of every in-page ``[text](#slug)`` link."""
    return [t[1:] for bang, t in _LINK.findall(markdown) if not bang and t.startswith("#")]
