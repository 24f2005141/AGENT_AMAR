"""Deterministic text cleaning helpers.

These implement the "clean and normalise" step from
``01-Agents/Mail Intake Agent.md``:

* HTML -> text conversion (so ``body`` never contains raw HTML)
* stripping quoted reply history and signatures
* collapsing whitespace
* extracting links from the final plain-text body

Nothing here uses an LLM. Every function is a pure transformation of its input.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# ---------------------------------------------------------------------------
# HTML -> text
# ---------------------------------------------------------------------------

_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "table", "section", "article", "header",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "blockquote",
    "pre",
}
_SKIP_TAGS = {"script", "style", "head", "title", "meta", "link"}


class _HTMLTextExtractor(HTMLParser):
    """Collect visible text from an HTML document.

    Block-level tags produce newlines so paragraph structure survives the
    conversion. ``<a href>`` targets are appended in parentheses so links are
    not lost when the markup is removed.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._chunks.append(f" ({href}) ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


def html_to_text(html: str) -> str:
    """Convert an HTML fragment/document to readable plain text.

    Deterministic: same input always yields the same output.
    """
    if not html:
        return ""
    parser = _HTMLTextExtractor()
    parser.feed(html)
    parser.close()
    return collapse_whitespace(parser.get_text())


# ---------------------------------------------------------------------------
# Whitespace / quote / signature cleanup
# ---------------------------------------------------------------------------

_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")
_MANY_SPACES_RE = re.compile(r"[ \t]{2,}")

# Common "start of quoted reply" markers.
_QUOTE_MARKERS = (
    re.compile(r"^On .+ wrote:\s*$", re.IGNORECASE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^_{5,}\s*$"),
    re.compile(r"^From:\s.+$", re.IGNORECASE),  # only when it starts a header block
)

# Signature delimiter per RFC 3676: a line containing exactly "-- ".
_SIGNATURE_RE = re.compile(r"^-- ?$")


def collapse_whitespace(text: str) -> str:
    """Normalise line endings and collapse runs of blank lines / spaces."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_SPACE_RE.sub("\n", text)
    text = _MANY_SPACES_RE.sub(" ", text)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


def strip_quoted_reply(text: str) -> str:
    """Remove a trailing quoted reply chain, if present.

    Conservative: only cuts at a recognised marker line, or at the first block
    of ``>``-prefixed quote lines that continues to the end of the message.
    """
    lines = text.split("\n")
    cut = len(lines)

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if any(marker.match(stripped) for marker in _QUOTE_MARKERS):
            cut = idx
            break

    # Also drop a contiguous ">"-quoted block that runs to the end.
    if cut == len(lines):
        for idx in range(len(lines) - 1, -1, -1):
            s = lines[idx].strip()
            if s == "" or s.startswith(">"):
                continue
            cut = idx + 1
            # only treat as a quote block if there was at least one ">" line
            if any(l.strip().startswith(">") for l in lines[cut:]):
                break
            cut = len(lines)
            break

    return "\n".join(lines[:cut]).strip()


def strip_signature(text: str) -> str:
    """Remove everything after an RFC 3676 ``-- `` signature delimiter."""
    lines = text.split("\n")
    for idx, line in enumerate(lines):
        if _SIGNATURE_RE.match(line):
            return "\n".join(lines[:idx]).strip()
    return text


def clean_body_text(text: str) -> str:
    """Full deterministic body cleanup pipeline."""
    if not text:
        return ""
    text = collapse_whitespace(text)
    text = strip_quoted_reply(text)
    text = strip_signature(text)
    return collapse_whitespace(text)


# ---------------------------------------------------------------------------
# Link extraction
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?)»\"'"


def extract_links(text: str) -> list[str]:
    """Return unique URLs found in ``text``, in first-seen order."""
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_TRAILING_PUNCT)
        if url and url not in seen:
            seen[url] = None
    return list(seen)
