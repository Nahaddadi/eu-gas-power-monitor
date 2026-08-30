"""Citation rendering for drafted notes.

The writer marks claims with [n], n referring to the numbered research
sources collected during the run. These helpers turn the markers into
superscript links (HTML for the app, reportlab markup for the PDF) or
inline markdown links (for the export), leaving unknown numbers
untouched so nothing is silently lost. The PDF variant also reduces the
text to glyphs the built-in fonts actually have.
"""

import re

_MARKER = re.compile(r"\[(\d+)\]")

# Built-in Helvetica covers only CP1252; models like to emit typographic
# variants (non-breaking hyphen, math minus, smart punctuation) that have
# no glyph there and print as black squares. Map them to plain ASCII.
_PDF_CHAR_MAP = {
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-",
    "\u2013": "-", "\u2014": "--",
    "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\u200b": "",
    "\u2018": "'", "\u2019": "'", "\u02bc": "'",
    "\u201c": '"', "\u201d": '"',
    "\u2026": "...",
    "\u2022": "-", "\u25aa": "-", "\u25a0": "-", "\u25cf": "-",
    "\u2192": "->", "\u2190": "<-", "\u2265": ">=", "\u2264": "<=",
}


def pdf_safe(text: str) -> str:
    """Text reduced to what the built-in PDF fonts can draw."""
    out = text
    for src, dst in _PDF_CHAR_MAP.items():
        out = out.replace(src, dst)
    return out.encode("cp1252", errors="replace").decode("cp1252")


def pdf_escape(text: str) -> str:
    escaped = pdf_safe(text)
    return (escaped.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _safe(url: str) -> str:
    return url.replace('"', "%22")


def cited_html(text: str, sources: list[dict]) -> str:
    """[n] -> clickable superscript for in-app display."""
    def replace(match: re.Match) -> str:
        number = int(match.group(1))
        if 1 <= number <= len(sources):
            url = _safe(sources[number - 1]["url"])
            return (
                f'<sup><a href="{url}" target="_blank" '
                f'style="text-decoration:none;color:#1f3a68;">[{number}]'
                f"</a></sup>"
            )
        return match.group(0)

    return _MARKER.sub(replace, text)


def cited_markdown(text: str, sources: list[dict]) -> str:
    """[n] -> markdown link for exported files."""
    def replace(match: re.Match) -> str:
        number = int(match.group(1))
        if 1 <= number <= len(sources):
            return f"[[{number}]]({_safe(sources[number - 1]['url'])})"
        return match.group(0)

    return _MARKER.sub(replace, text)


def cited_pdf(text: str, sources: list[dict]) -> str:
    """[n] -> superscript link for reportlab paragraphs."""
    def replace(match: re.Match) -> str:
        number = int(match.group(1))
        if 1 <= number <= len(sources):
            url = _safe(sources[number - 1]["url"])
            return (
                f'<super><a href="{url}" color="#1f3a68">[{number}]</a></super>'
            )
        return match.group(0)

    escaped = pdf_escape(text)
    return _MARKER.sub(replace, escaped)
