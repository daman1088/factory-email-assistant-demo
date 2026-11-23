"""Turn provider messages into clean text. Pure functions, no I/O."""
import hashlib
import re
from html.parser import HTMLParser

_QUOTE_HEADER = re.compile(r"^\s*On .{5,200} wrote:\s*$", re.MULTILINE)
_FORWARD_MARKERS = ("-----Original Message-----", "________________________________")


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def strip_quoted(text: str) -> str:
    """Remove quoted replies ('> ...', 'On X wrote:', forwarded originals). Old thread content would
    otherwise be re-classified and re-sent to the LLM with every reply."""
    cut = len(text)
    match = _QUOTE_HEADER.search(text)
    if match:
        cut = min(cut, match.start())
    for marker in _FORWARD_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    kept = [line for line in text[:cut].splitlines() if not line.lstrip().startswith(">")]
    return "\n".join(kept).strip()


def body_to_text(body_text: str | None, body_html: str | None) -> str:
    if body_text and body_text.strip():
        return body_text.strip()
    if body_html:
        return html_to_text(body_html)
    return ""


def snippet(text: str, length: int = 180) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= length else flat[: length - 1].rstrip() + "…"


def content_hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
