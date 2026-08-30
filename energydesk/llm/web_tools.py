"""Web research tools, executed locally.

The model never browses by itself. It asks for pages or searches; the tools
run them and hand back plain text. Every URL is checked against the
configured source zones before the download starts and again on the final
redirect target, so what the model can read is exactly what the
configuration allows - nothing more.

A zone is one host plus a path prefix. A site root opens the whole site, a
section link opens that section and everything nested deeper under it, an
article link allows only that page.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
import trafilatura
from ddgs import DDGS

# Default research sources, one whole-site zone per entry. Any custom source
# supplied at run time replaces this list entirely.
DEFAULT_SOURCE_URLS = (
    "https://agsi.gie.eu",
    "https://www.energy-charts.info",
    "https://www.entsog.eu",
    "https://www.acer.europa.eu",
    "https://ec.europa.eu",
    "https://commission.europa.eu",
    "https://www.bruegel.org",
    "https://www.imf.org",
    "https://www.ecb.europa.eu",
    "https://www.reuters.com",
    "https://www.ft.com",
    "https://www.montelnews.com",
    "https://www.lngprime.com",
    "https://www.eex.com",
    "https://www.edf.fr",
    "https://www.yale.edu",
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) gas-power-monitor/0.1 "
    "(personal research project)"
)


class WebToolsError(RuntimeError):
    """Raised when a search or page fetch cannot be completed."""


@dataclass(frozen=True)
class UrlScope:
    """One navigable zone: a host plus the deepest allowed path prefix."""

    host: str          # normalized, without www
    path_prefix: str   # "/" means the whole site


def normalize_link(url: str) -> str | None:
    """Canonical https form of a user-supplied link, or None if unusable."""
    url = (url or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parts = urlparse(url)
    host = parts.netloc.lower().removeprefix("www.")
    if "." not in host:
        return None
    return f"https://{host}{parts.path.rstrip('/')}"


def parse_user_sources(text: str) -> tuple[list[UrlScope], list[str], int]:
    """Split free-form input into zones, their canonical links and a count
    of tokens that could not be used."""
    scopes: list[UrlScope] = []
    links: list[str] = []
    seen: set[tuple[str, str]] = set()
    invalid = 0
    for token in re.split(r"[\s;,]+", text or ""):
        normalized = normalize_link(token)
        if normalized is None:
            invalid += bool(token)
            continue
        parts = urlparse(normalized)
        scope = UrlScope(host=parts.netloc, path_prefix=parts.path or "/")
        if (scope.host, scope.path_prefix) in seen:
            continue
        seen.add((scope.host, scope.path_prefix))
        scopes.append(scope)
        links.append(normalized)
    return scopes, links, invalid


def in_scope(url: str, scopes: Sequence[UrlScope]) -> bool:
    """True when url sits inside one of the zones.

    Only equal or deeper paths count: the agent may move down a hierarchy,
    never sideways or up out of it. Query strings and fragments never widen
    a zone, and a zone covers its host's subdomains. An empty scope list
    means unrestricted mode - everything is allowed. The wildcard host "*"
    allows the whole public web - used only by that same explicit mode,
    never produced from user input.
    """
    if not scopes:
        return True
    parts = urlparse(url)
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path or "/"
    for scope in scopes:
        if scope.host == "*":
            return True
        if host != scope.host and not host.endswith("." + scope.host):
            continue
        prefix = scope.path_prefix
        if prefix == "/" or path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def describe_scopes(scopes: Sequence[UrlScope]) -> str:
    """Human-readable zone list for the planner prompt."""
    lines = []
    for scope in scopes:
        if scope.host == "*":
            lines.append("- anywhere on the public web (unrestricted mode)")
            continue
        base = f"https://{scope.host}"
        if scope.path_prefix == "/":
            lines.append(f"- {base} (the whole site)")
        else:
            lines.append(
                f"- {base}{scope.path_prefix} (this address and anything "
                f"under it)"
            )
    return "\n".join(lines)


DEFAULT_SCOPES = parse_user_sources("\n".join(DEFAULT_SOURCE_URLS))[0]

# Hosts whose article pages reliably yield clean text. They only reorder
# the fallback reads - they never widen what the zones allow.
PARSE_FRIENDLY_HOSTS = (
    "lngprime.com", "energy-charts.info", "imf.org", "ecb.europa.eu",
    "ec.europa.eu", "commission.europa.eu", "edf.fr", "yale.edu",
    "eex.com", "montelnews.com",
)

# Generic topics used to bias searches back toward the allowed zones when
# the open web serves nothing in-scope. Keys are hosts from
# PARSE_FRIENDLY_HOSTS that carry fresh editorial content.
ZONE_QUERY_TOPICS = {
    "lngprime.com": "Europe LNG import terminal news",
    "montelnews.com": "European gas power market",
    "eex.com": "European carbon EUA natural gas market",
    "ecb.europa.eu": "energy prices euro area economy",
    "imf.org": "Europe energy prices outlook",
    "ec.europa.eu": "energy market security of supply",
    "commission.europa.eu": "energy market security of supply",
    "edf.fr": "electricity nuclear production",
    "yale.edu": "climate energy policy",
    "energy-charts.info": "German electricity day-ahead prices",
}


def zone_query_hints(scopes: Sequence[UrlScope]) -> list[tuple[str, str, str]]:
    """(root_url, host, topic) for scrape-friendly allowed zones.

    Used to bias searches with site:<host> so candidates exist inside the
    configured zones even when the open web serves other domains.
    """
    hints: list[tuple[str, str, str]] = []
    for scope in scopes:
        if scope.host not in ZONE_QUERY_TOPICS:
            continue
        root = f"https://{scope.host}{'' if scope.path_prefix == '/' else scope.path_prefix}"
        hints.append((root, scope.host, ZONE_QUERY_TOPICS[scope.host]))
    return hints


class WebTools:
    """Search the web and extract readable text from configured zones."""

    def __init__(self, max_chars_per_page: int = 4000,
                 scopes: Sequence[UrlScope] | None = None):
        self.max_chars = max_chars_per_page
        self.scopes = tuple(scopes) if scopes is not None else DEFAULT_SCOPES
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    # -- search ---------------------------------------------------------------

    def _raw_search(self, query: str, max_results: int,
                    backend: str | None = None) -> list[dict]:
        with DDGS() as ddgs:
            if backend is None:
                return list(ddgs.text(query, max_results=max_results))
            return list(ddgs.text(query, max_results=max_results,
                                  backend=backend))

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        """Web search returning [{title, url, snippet}].

        Discovery is deliberately unfiltered - titles and snippets carry no
        page content, and the planner is told which zones exist. Reading
        itself is where containment bites: every fetch is zone-checked.
        """
        try:
            hits = self._raw_search(query, max_results)
            if not hits:
                hits = self._raw_search(query, max_results, backend="lite")
        except TypeError:
            hits = self._raw_search(query, max_results)
        except Exception as exc:
            raise WebToolsError(f"search failed for '{query}': {exc}") from exc

        return [
            {
                "title": hit.get("title", ""),
                "url": hit.get("href") or hit.get("url", ""),
                "snippet": hit.get("body", "")[:300],
            }
            for hit in hits
        ]

    # -- page extraction ------------------------------------------------------

    def fetch(self, url: str) -> dict:
        """Download an in-zone page and extract its main text."""
        if not in_scope(url, self.scopes):
            raise WebToolsError(
                f"{url} is outside the configured source zones"
            )

        downloaded = self.session.get(url, timeout=30)
        downloaded.raise_for_status()
        if not in_scope(downloaded.url, self.scopes):
            raise WebToolsError(
                f"{url} redirected outside the configured source zones"
            )
        content_type = downloaded.headers.get("content-type", "").lower()
        if content_type and "html" not in content_type:
            kind = content_type.split(";")[0]
            raise WebToolsError(
                f"{downloaded.url} is not an HTML page ({kind})"
            )

        text = trafilatura.extract(
            downloaded.text, include_comments=False, include_tables=False,
        )
        if not text:
            raise WebToolsError(f"could not extract readable text from {url}")

        title = ""
        try:
            meta = trafilatura.extract_metadata(downloaded.text)
            title = (meta.title or "").strip() if meta else ""
        except Exception:
            title = ""

        return {
            "url": downloaded.url,
            "title": title,
            "text": text[: self.max_chars],
            "truncated": len(text) > self.max_chars,
        }
