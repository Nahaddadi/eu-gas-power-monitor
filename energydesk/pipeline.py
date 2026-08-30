"""One monitor run, end to end: data in, metrics computed, charts rendered,
desk note drafted when an LLM key is available."""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from energydesk.config import Settings
from energydesk.datasources.base import DataSource
from energydesk.datasources.energy_charts import PowerPriceSource
from energydesk.datasources.gie_storage import GieStorageSource
from energydesk.datasources.yahoo_curve import (
    YahooFuturesSource, carbon_contract, ttf_contracts,
)
from energydesk.llm.client import GeminiClient, QuotaExceeded
from energydesk.llm.note import DeskNote
from energydesk.llm.research_agent import DraftWriter, ResearchAgent, ResearchContext
from energydesk.llm.web_tools import WebTools, UrlScope, parse_user_sources
from energydesk.market.calculator import MetricCalculator
from energydesk.market.models import MarketSnapshot
from energydesk.reporting.charts import ChartBuilder
from energydesk.reporting.snapshot import format_snapshot_text, snapshot_table


class DataError(RuntimeError):
    """A required dataset could not be loaded live or from cache."""


@dataclass
class RunResult:
    """Everything one run produced, plus anything worth telling the user."""

    snapshot: MarketSnapshot
    note: DeskNote | None = None
    research: ResearchContext | None = None
    artifacts: dict[str, Path] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class SavedRun:
    """A previously generated run folder, readable without any network."""

    directory: Path
    date: str


class DeskMonitor:
    """Orchestrates sources, calculator, charts and the LLM workflow."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()

    # -- public API -------------------------------------------------------------

    def latest_saved_run(self) -> SavedRun | None:
        """Newest run folder that actually contains results, if any."""
        runs_dir = self.settings.runs_dir
        if not runs_dir.exists():
            return None
        candidates = sorted(
            (d for d in runs_dir.iterdir() if d.is_dir()), reverse=True,
        )
        for folder in candidates:
            if (folder / "metrics.json").exists():
                return SavedRun(directory=folder, date=folder.name)
        return None

    def run(self, do_research: bool = True,
            force_refresh: bool = False, custom_sources: str = "",
            unrestricted: bool = False) -> RunResult:
        settings = self.settings
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        run_dir = self._run_dir()
        warnings: list[str] = []

        print(f"== EU gas/power monitor - {date.today():%Y-%m-%d} ==")
        max_age = 0.0 if force_refresh else self.settings.cache_max_age_hours

        storage = self._load(self.storage_source(max_age), "EU gas storage")
        curve_src = self.curve_source(max_age)
        curve = self._load(curve_src, "TTF curve")
        eua = self._load(self.carbon_source(max_age), "EUA carbon")
        power = self._load(self.power_source(max_age), "DE day-ahead power")

        curve_prices = curve_src.last_prices(curve)
        labels = [label for label in curve_src.contracts if label in curve_prices]

        print("computing metrics...")
        calculator = MetricCalculator(settings.conventions)
        snapshot = calculator.build(
            as_of=date.today(),
            storage=storage,
            curve_prices=curve_prices,
            curve_labels=labels,
            eua=eua.set_index("date")["EUA"],
            power_hourly=power,
        )
        # A failed source costs one metric, never the run.
        for warn in snapshot.warnings:
            print(f"[market] {warn}")
        warnings.extend(snapshot.warnings)

        print("rendering charts...")
        chart_paths = ChartBuilder(
            snapshot, storage, curve, run_dir / "charts",
        ).render_all()

        artifacts = {
            "metrics": self._save_json(run_dir / "metrics.json", snapshot.to_dict()),
            # Flat metric rows, so the UI can rebuild its display from a
            # saved run without touching the market data again.
            "table": self._save_json(run_dir / "snapshot_table.json",
                                     snapshot_table(snapshot)),
            "snapshot": run_dir / "snapshot.txt",
            **{p.name: p for p in chart_paths},
        }
        artifacts["snapshot"].write_text(format_snapshot_text(snapshot),
                                        encoding="utf-8")

        note, research = None, None
        if settings.has_llm:
            print("drafting desk note...")
            note, research, llm_warnings = self._draft(
                snapshot, do_research, custom_sources, unrestricted)
            warnings += llm_warnings
            if note is not None:
                artifacts["note"] = run_dir / "desk_note.txt"
                artifacts["note"].write_text(note.render_plain(), encoding="utf-8")
                if research is not None:
                    artifacts["research_log"] = self._save_json(
                        run_dir / "research_log.json", research.to_log(),
                    )
                    # The carnet itself: per-source notes with their exact
                    # links, written incrementally during the run.
                    ledger = research.ledger_text()
                    if ledger:
                        artifacts["research_notes"] = run_dir / "research_notes.txt"
                        artifacts["research_notes"].write_text(
                            ledger, encoding="utf-8")
        else:
            msg = "no GEMINI_API_KEY set - skipped the desk note draft"
            warnings.append(msg)
            print(f"[llm] {msg}")

        for path in artifacts.values():
            print(f"  wrote {path}")
        return RunResult(snapshot=snapshot, note=note, research=research,
                         artifacts=artifacts, warnings=warnings)

    # -- source wiring -----------------------------------------------------------

    def storage_source(self, max_age_hours: float) -> GieStorageSource:
        s = self.settings
        return GieStorageSource(s.cache_dir, area=s.storage_area,
                                api_key=s.gie_api_key,
                                max_age_hours=max_age_hours)

    def curve_source(self, max_age_hours: float) -> YahooFuturesSource:
        return YahooFuturesSource(self.settings.cache_dir, name="ttf_curve",
                                  contracts=ttf_contracts(),
                                  max_age_hours=max_age_hours)

    def carbon_source(self, max_age_hours: float) -> YahooFuturesSource:
        return YahooFuturesSource(self.settings.cache_dir, name="eua",
                                  contracts=carbon_contract(),
                                  max_age_hours=max_age_hours)

    def power_source(self, max_age_hours: float) -> PowerPriceSource:
        return PowerPriceSource(self.settings.cache_dir,
                                bidding_zone=self.settings.power_bidding_zone,
                                max_age_hours=max_age_hours)

    # -- internals -----------------------------------------------------------------

    def _load(self, source: DataSource, label: str):
        try:
            df = source.load()
        except Exception as exc:
            hint = ""
            if isinstance(source, GieStorageSource) and not source.api_key:
                hint = " (GIE needs a free API key, see .env.example)"
            raise DataError(f"{label}: could not load data{hint}: {exc}") from exc
        print(f"  {label}: {len(df)} rows")
        return df

    def _draft(self, snapshot: MarketSnapshot, do_research: bool,
               custom_sources: str = "",
               unrestricted: bool = False) -> tuple[DeskNote | None,
                                                    ResearchContext | None,
                                                    list[str]]:
        """Run research + drafting; any failure degrades to 'no note today'."""
        client = GeminiClient.from_settings(self.settings)
        metrics_json = json.dumps(snapshot.to_dict(), indent=2)

        tools = WebTools()
        seeds: list[str] = []
        source_notes: list[str] = []
        if unrestricted:
            tools = WebTools(scopes=[UrlScope(host="*", path_prefix="/")])
            source_notes.append(
                "unrestricted research active - source zones are ignored"
            )
        elif custom_sources.strip():
            scopes, links, invalid = parse_user_sources(custom_sources)
            if scopes:
                # Custom sources replace the built-in allowlist entirely.
                tools = WebTools(scopes=scopes)
                seeds = links[:6]
                note = (f"custom sources active: {len(scopes)} zone(s) "
                        f"replace the built-in allowlist")
                if invalid:
                    note += f"; {invalid} unusable link(s) ignored"
                source_notes.append(note)
            elif invalid:
                source_notes.append(
                    "custom sources ignored: no usable link was provided"
                )

        research = None
        if do_research:
            try:
                research = ResearchAgent(client, tools, seeds=seeds).gather(
                    metrics_json)
                if not research.sources:
                    source_notes.append(
                        "no readable web source this run - the draft is "
                        "written from the metrics alone"
                    )
                elif research.coverage_note:
                    source_notes.append(research.coverage_note)
            except QuotaExceeded:
                return None, None, [
                    "web research skipped: the daily free quota is spent "
                    "(resets midnight UTC)",
                    *source_notes,
                ]
            except Exception as exc:
                return None, None, [
                    f"web research failed ({exc}); continuing without it",
                    *source_notes,
                ]

        try:
            # The writer works from the notes ledger (one distilled block per
            # source, each with its exact link); raw observations are only a
            # fallback for the rare run where note taking failed everywhere.
            digest = ""
            if research is not None:
                digest = research.ledger_text() or research.digest
            sources_block = "\n".join(
                f"{i}. {src['title']} - {src['url']}"
                for i, src in enumerate(research.sources, start=1)
            ) if research else ""
            writer = DraftWriter(client)
            note = writer.write(
                metrics_json, digest, snapshot.as_of, sources_block)
            if research is not None and len(research.sources) > 1 \
                    and writer.last_raw:
                unused = [
                    number for number in range(1, len(research.sources) + 1)
                    if f"[{number}]" not in writer.last_raw
                ]
                if unused:
                    tags = ", ".join(f"[{n}]" for n in unused)
                    source_notes.append(f"sources never cited in the draft: {tags}")
            return note, research, source_notes
        except QuotaExceeded:
            return None, research, [
                "note drafting skipped: the daily free quota is spent "
                "(resets midnight UTC)",
                *source_notes,
            ]
        except Exception as exc:
            return None, research, [f"desk note drafting failed: {exc}",
                                    *source_notes]

    def _run_dir(self) -> Path:
        run_dir = self.settings.runs_dir / f"{date.today():%Y-%m-%d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    @staticmethod
    def _save_json(path: Path, payload: dict) -> Path:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
