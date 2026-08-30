"""The research loop and the drafting step.

Two separate calls keep things reliable on free models: first the model acts
as a research planner (it only ever emits one small JSON decision per round),
then a second call writes the desk note from the metrics plus the gathered
context.
"""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from energydesk.llm.client import GeminiClient
from energydesk.llm.note import DeskNote
from energydesk.llm.prompts import (
    DRAFT_SYSTEM, NOTE_SYSTEM, RESEARCH_SYSTEM, draft_user_prompt,
    research_user_prompt, synth_user_prompt,
)
from energydesk.llm.web_tools import (
    PARSE_FRIENDLY_HOSTS, WebTools, describe_scopes, in_scope,
    zone_query_hints,
)

# Generic reference sites parse beautifully and carry zero market value;
# they only inflate the source count when the fallback pass reads hits.
GENERIC_READ_HOSTS = {
    "wikipedia.org", "britannica.com", "wiktionary.org", "fandom.com",
    "dictionary.cambridge.org",
}


# Coverage targets: a note resting on a single page is exactly how one
# narrative ends up cited four times. The planning loop refuses to stop
# until these are met or the round budget runs out.
MIN_SOURCES = 4
MIN_HOSTS = 3


def _host_of(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def clean_title(title: str, limit: int = 90) -> str:
    """One-line, bounded titles for the sources list and the ledger.

    Scraped <title> tags often carry collapsed whitespace or SEO slugs;
    anything past the limit is cut on a word boundary with an ellipsis.
    """
    text = " ".join(str(title).split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def _distinct_hosts(sources: list[dict]) -> set[str]:
    return {_host_of(s["url"]) for s in sources}


@dataclass
class ResearchContext:
    """What the research phase collected, ready to be fed into the draft.

    `sources` is the citation registry (order fixes the [n] numbers);
    `notes` holds the structured per-page notes keyed by final URL, so the
    writer works from a compact ledger instead of raw page dumps.
    """

    digest: str = ""
    queries: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)  # [{url, title}], cited as [n]
    notes: dict[str, dict] = field(default_factory=dict)  # url -> {theme, facts}
    coverage_note: str = ""  # set when the run ended below the targets

    def to_log(self) -> dict:
        return {"queries": self.queries, "pages_visited": self.pages,
                "sources": self.sources,
                "notes": [
                    {"url": s["url"], **self.notes.get(s["url"], {})}
                    for s in self.sources
                ]}

    def ledger_text(self) -> str:
        """The research carnet: one numbered block of notes per source."""
        if not self.sources:
            return ""
        blocks = []
        for number, source in enumerate(self.sources, start=1):
            note = self.notes.get(source["url"], {})
            host = _host_of(source["url"])
            head = (f"[{number}] {source['title']} ({host})"
                    f" - theme: {note.get('theme', 'macro')}")
            facts = "\n".join(f"  - {fact}" for fact in note.get("facts", []))
            blocks.append(f"{head}\n{facts}" if facts else head)
        return "RESEARCH NOTES LEDGER\n" + "\n".join(blocks)


class ResearchAgent:
    """Runs the bounded plan -> observe loop that feeds the note writer."""

    def __init__(self, client: GeminiClient, tools: WebTools,
                 max_rounds: int = 6, max_digest_chars: int = 24000,
                 seeds: list[str] | None = None,
                 min_sources: int | None = None,
                 min_hosts: int | None = None):
        self.client = client
        self.tools = tools
        self.max_rounds = max_rounds
        self.max_digest_chars = max_digest_chars
        self.min_sources = MIN_SOURCES if min_sources is None else min_sources
        self.min_hosts = MIN_HOSTS if min_hosts is None else min_hosts
        # Custom source links are read up front, so they feed the note even
        # if the planner never picks them on its own.
        zones = getattr(tools, "scopes", ())
        self.seeds = [u for u in (seeds or []) if in_scope(u, zones)][:6]
        self.zones_text = describe_scopes(zones)
        # In-scope URLs surfaced by searches, for the end-of-run fallback.
        self.recent_hits: list[str] = []

    def gather(self, metrics_json: str) -> ResearchContext:
        observations: list[str] = []
        context = ResearchContext()

        if self.seeds:
            print(f"[research] reading {len(self.seeds)} custom source page(s)")
            self._run_fetches(self.seeds, observations, context)

        for round_number in range(1, self.max_rounds + 1):
            log_text = "\n\n".join(observations)[-4000:]
            prompt = research_user_prompt(
                metrics_json, round_number, self.max_rounds, log_text,
                self.zones_text, coverage=self._coverage(context),
            )
            # Reasoning models spend completion tokens thinking before they
            # emit the JSON decision, so the budget has to be generous here.
            reply = self.client.complete(RESEARCH_SYSTEM, prompt, max_tokens=700)

            decision = extract_json(reply)
            if decision is None:
                print(f"[research] unparsable reply: {reply[:150]!r}")
                print("[research] stopping research")
                break
            action = decision.get("action")

            if action == "finish":
                # A single readable page is not research. Refuse the early
                # exit and tell the planner exactly what is still missing.
                if self._targets_met(context) \
                        or round_number >= self.max_rounds:
                    break
                print(f"[research] finish refused: thin coverage "
                      f"({self._coverage(context)})")
                observations.append(
                    "SUPERVISOR: finish refused - the note cannot rest on "
                    f"{self._coverage(context)}. Targets: at least "
                    f"{self.min_sources} pages from {self.min_hosts} different"
                    " sites, spanning gas storage, the TTF curve, EUA carbon,"
                    " German power and LNG/supply context. Keep researching"
                    " whichever theme is still missing."
                )
                continue
            if action == "search":
                self._run_searches(decision.get("queries", []), observations, context)
            elif action == "fetch":
                self._run_fetches(decision.get("urls", []), observations, context)
            else:
                print(f"[research] unknown action '{action}', stopping research")
                break

            if sum(len(o) for o in observations) > self.max_digest_chars:
                print("[research] digest limit reached, stopping research")
                break

        # The open web drifts: on a given day most search hits sit outside
        # the allowed zones, which would starve the fallback reading list.
        # Bias searches back toward the configured sources so candidates
        # exist in-zone, then read the freshest zone roots as a last resort.
        if not self._targets_met(context) and not self.recent_hits:
            self._bias_toward_zones(observations, context)

        # Coverage below target: read the best search hits directly rather
        # than leave sections of the note unsupported. Wire services and PDF
        # links often fail to parse, so order candidates with known
        # scrape-friendly hosts first and keep walking until the targets are
        # met or the candidate list runs dry.
        if self.recent_hits and not self._targets_met(context):
            print("[research] coverage below target - "
                  "reading top search results directly")

            def parse_rank(url: str) -> int:
                host = _host_of(url)
                if any(host == g or host.endswith("." + g)
                       for g in GENERIC_READ_HOSTS):
                    return 2  # generic reference sites: only as a last resort
                friendly = any(
                    host == h or host.endswith("." + h)
                    for h in PARSE_FRIENDLY_HOSTS
                )
                return 0 if friendly else 1

            for url in sorted(self.recent_hits, key=parse_rank)[:12]:
                if self._targets_met(context):
                    break
                before = len(context.sources)
                self._run_fetches([url], observations, context)
                if len(context.sources) == before:
                    print(f"[research] unreadable hit skipped: {url}")

        # Last resort: the zone roots themselves. News and market home
        # pages list fresh articles, so they carry citable current content.
        if not self._targets_met(context):
            hints = zone_query_hints(getattr(self.tools, "scopes", ()))
            for root_url, _host, _topic in hints[:5]:
                if self._targets_met(context):
                    break
                print(f"[research] reading zone root: {root_url}")
                self._run_fetches([root_url], observations, context)

        context.digest = "\n\n".join(observations)[: self.max_digest_chars]
        if not self._targets_met(context):
            context.coverage_note = (
                f"web research stayed thin: {self._coverage(context)} "
                f"(target was {self.min_sources} pages from "
                f"{self.min_hosts} different sites)"
            )
            print(f"[research] {context.coverage_note}")
        return context

    # -- coverage -----------------------------------------------------------

    def _coverage(self, context: ResearchContext) -> str:
        hosts = len(_distinct_hosts(context.sources))
        return f"{len(context.sources)} page(s) from {hosts} host(s)"

    def _targets_met(self, context: ResearchContext) -> bool:
        return (len(context.sources) >= self.min_sources
                and len(_distinct_hosts(context.sources)) >= self.min_hosts)

    # -- actions -----------------------------------------------------------------

    def _bias_toward_zones(self, observations: list[str],
                           context: ResearchContext) -> None:
        """Search with site:<host> over the allowed sources themselves.

        Every candidate these queries surface is inside the configured
        zones by construction, so containment stays untouched.
        """
        hints = zone_query_hints(getattr(self.tools, "scopes", ()))
        if not hints:
            return
        print("[research] open web served nothing in-zone - "
              "searching allowed sources directly")
        for _root, host, topic in hints[:5]:
            query = f"site:{host} {topic}"
            try:
                hits = self.tools.search(query)
            except Exception as exc:  # noqa: BLE001
                observations.append(f"SEARCH '{query}' failed: {exc}")
                continue
            context.queries.append(query)
            for hit in hits:
                url = hit["url"]
                known_scopes = getattr(self.tools, "scopes", ())
                if url not in self.recent_hits and (
                    not known_scopes or in_scope(url, known_scopes)
                ):
                    self.recent_hits.append(url)
        self.recent_hits[:] = self.recent_hits[:8]
        listing = "\n".join(f"- {u}" for u in self.recent_hits) or "- none"
        observations.append(f"ZONE-SCOPED SEARCH results:\n{listing}")

    def _run_searches(self, queries: list[str], observations: list[str],
                      context: ResearchContext) -> None:
        for query in queries[:3]:
            query = str(query).strip()[:120]
            if not query:
                continue
            context.queries.append(query)
            print(f"[research] searching: {query}")
            try:
                hits = self.tools.search(query)
            except Exception as exc:
                observations.append(f"SEARCH '{query}' failed: {exc}")
                continue
            lines = [
                f"- {h['title']} | {h['url']}\n  {h['snippet']}" for h in hits
            ]
            # Only in-zone hits join the fallback reading list; anything
            # else would just be rejected by fetch later anyway.
            for hit in hits:
                url = hit["url"]
                known_scopes = getattr(self.tools, "scopes", ())
                if url not in self.recent_hits and (
                    not known_scopes or in_scope(url, known_scopes)
                ):
                    self.recent_hits.append(url)
            self.recent_hits[:] = self.recent_hits[:8]
            observations.append(
                f"SEARCH '{query}':\n" + ("\n".join(lines) or "- no results")
            )

    def _run_fetches(self, urls: list[str], observations: list[str],
                     context: ResearchContext) -> None:
        for url in urls[:2]:
            url = str(url).strip()
            if not url.startswith("http"):
                continue
            context.pages.append(url)
            print(f"[research] fetching: {url}")
            try:
                page = self.tools.fetch(url)
            except Exception as exc:
                observations.append(f"PAGE {url} failed: {exc}")
                continue

            final_url = page["url"]
            if all(s["url"] != final_url for s in context.sources):
                title = clean_title(page.get("title")
                                    or urlparse(final_url).netloc)
                source_number = len(context.sources) + 1
                context.sources.append({"url": final_url, "title": title})
                self._take_notes(source_number, title, final_url,
                                 page.get("text", ""), context, observations)

            suffix = " (truncated)" if page["truncated"] else ""
            observations.append(
                f"PAGE {page['url']}{suffix}:\n{page['text']}"
            )

    # -- note taking ---------------------------------------------------------

    def _take_notes(self, number: int, title: str, url: str, text: str,
                    context: ResearchContext, observations: list[str]) -> None:
        """Compress one freshly read page into ledger notes with its link.

        This is the incremental carnet: each source is read once and
        immediately distilled into theme-tagged facts, so the drafting step
        later works from these notes instead of raw page dumps.
        """
        entry: dict = {"theme": "macro", "facts": []}
        try:
            reply = self.client.complete(
                NOTE_SYSTEM, synth_user_prompt(title, url, text),
                max_tokens=600)
            data = extract_json(reply)
            if data and isinstance(data.get("facts"), list):
                entry["theme"] = str(data.get("theme") or "macro")
                entry["facts"] = [str(f) for f in data["facts"]][:5]
        except Exception as exc:  # noqa: BLE001
            print(f"[research] note taking failed for {url}: {exc}")
        if not entry["facts"]:
            # The synthesis step is best effort: a stubborn page still
            # joins the ledger with its opening lines as coarse notes.
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            entry["facts"] = [f"(auto-extract) {ln}" for ln in lines[:3]]

        context.notes[url] = entry
        host = _host_of(url)
        first = entry["facts"][0] if entry["facts"] else "-"
        observations.append(
            f"NOTED [{number}] {host} (theme: {entry['theme']}): {first}"
        )
        print(f"[research] noted [{number}] {host} - "
              f"{len(entry['facts'])} fact(s)")


class DraftWriter:
    """Turns metrics + research into the tagged-block desk note."""

    CITATION_REMINDER = (

        "\n\nREMINDER: every fact drawn from the RESEARCH CONTEXT must end "
        "with its bracketed source number, like [1] or [2]. A draft without "
        "these inline markers will be rejected."

    )

    def __init__(self, client: GeminiClient):
        self.client = client
        self.last_raw: str | None = None

    def write(self, metrics_json: str, digest: str, as_of: str,
              sources_block: str = "") -> DeskNote:
        base_prompt = draft_user_prompt(metrics_json, digest, as_of,
                                        sources_block)
        # Small models sometimes forget the inline markers; when sources
        # exist, one strict redraft is cheaper than shipping an uncited note.
        prompts = [base_prompt]
        if sources_block:
            prompts.append(base_prompt + self.CITATION_REMINDER)

        fallback = None
        seen: list[str] = []
        last_error: Exception | None = None
        for attempt, prompt in enumerate(prompts):
            try:
                raw = self.client.complete(
                    DRAFT_SYSTEM, prompt,
                    # Headroom for hidden reasoning tokens that some
                    # Gemini generations spend before the visible note.
                    max_tokens=8000,
                )
                # Some models tag metric-derived facts as if METRICS were a
                # numbered source; those facts carry no marker by design.
                raw = re.sub(r"\s*\[(?:METRICS|N/A)\]", "", raw)
                self.last_raw = raw
                seen.append(",".join(DeskNote.found_tags(raw)) or "none")
                note = DeskNote.parse(raw)
                if not note.bottom_line or not note.power:
                    missing = [name for name, value in (
                        ("BOTTOM LINE", note.bottom_line), ("POWER", note.power))
                        if not value]
                    last_error = ValueError(
                        f"drafted note is missing {missing}")
                    continue
                if attempt == 0 and sources_block \
                        and not re.search(r"\[\d+\]", raw):
                    fallback = note
                    continue
                return note
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if fallback is not None:
            print("[llm] draft kept without inline citation markers")
            return fallback
        detail = "; ".join(f"attempt saw tags [{t}]" for t in seen) \
            or "no parsable reply"
        raise ValueError(f"{last_error} ({detail})")


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a reply, wherever it hides.

    Free models often wrap their decision in reasoning prose, and the
    object itself can nest arrays. A fenced block is tried first, then a
    incremental decode scan over every opening brace.
    """
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            data = json.loads(fenced.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    start = 0
    while True:
        idx = text.find("{", start)
        if idx == -1:
            return None
        try:
            data, _ = decoder.raw_decode(text[idx:])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        start = idx + 1
