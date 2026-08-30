"""The desk note as an ordered list of typed display blocks.

Building the block list is pure logic over the run's metrics and the
drafted note, so the interface only renders and never re-derives content.
Without a model draft, computed summaries carry the note so it is always
complete.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from energydesk.llm.note import DeskNote

PENDING, CONFIRMED, EDITED, REJECTED = "pending", "confirmed", "edited", "rejected"

STATUS_LABELS = {
    PENDING: ("needs review", "st-pending"),
    CONFIRMED: ("reviewed", "st-confirmed"),
    EDITED: ("edited", "st-edited"),
    REJECTED: ("rejected", "st-rejected"),
}

CHART_FILES = {
    "storage": ("1_storage.png",
                "Figure 1. EU gas storage vs period average (GIE AGSI+)."),
    "curve": ("2_ttf_curve.png",
              "Figure 2. TTF forward curve, today vs one month ago "
              "(ICE TTF via Yahoo Finance)."),
    "spark": ("3_power_spark.png",
              "Figure 3. DE day-ahead vs gas SRMC, clean spark spread below "
              "(Energy-Charts, GIE, ICE)."),
}

FALLBACK_RISKS = [
    "Storage injection pace through the refill season.",
    "Renewable output swings pulling gas back into the merit order.",
    "Carbon price momentum resuming to the upside.",
    "LNG supply shocks transmitted straight into TTF.",
]


@dataclass
class Block:
    """One element of the rendered note, optionally subject to review."""

    kind: str                    # heading | prose | chart | callout | finding | meta
    content: object              # text, or (path, caption) for charts
    origin: str = "derived"      # drafted | computed
    status: str = CONFIRMED
    reviewable: bool = False
    edited_text: str | None = None

    @property
    def display_text(self) -> str:
        return self.edited_text if self.edited_text is not None else str(self.content)


def build_blocks(metrics: dict, note: DeskNote | None,
                 charts: dict[str, Path]) -> list[Block]:
    """Assemble the note as an ordered list of typed blocks.

    With a model draft, its paragraphs are marked drafted and start as
    pending review. Without one - no key, or every model unavailable -
    computed summaries carry the note instead.
    """
    gas = metrics.get("gas_storage", {})
    ttf = metrics.get("ttf", {})
    eua = metrics.get("eua", {})
    power = metrics.get("power_de", {})
    spark = metrics.get("clean_spark", {})

    def derived(section: str) -> str:
        if section == "bottom_line":
            if not spark:
                return ("The clean spark spread is unavailable this run - "
                        "it needs TTF, EUA and German power quotes together.")
            return (
                f"German power averaged EUR {power.get('da_7d_eur_mwh', 0):.0f}/MWh "
                f"over seven days against a gas SRMC of "
                f"EUR {spark.get('srmc_gas_eur_mwh', 0):.0f}/MWh, leaving the clean "
                f"spark at EUR {spark.get('spread_eur_mwh', 0):+.0f}/MWh - "
                f"{spark.get('gas_regime', 'n/a')}."
            )
        if section == "gas":
            parts = []
            if gas:
                parts.append(
                    f"EU storage stands at {gas.get('eu_fill_pct', 0):.1f} percent, "
                    f"{gas.get('gap_pp', 0):+.1f} points versus its period average "
                    f"of {gas.get('period_avg_pct', 0):.1f}.")
            if ttf:
                parts.append(
                    f"TTF front trades at EUR {ttf.get('front_eur_mwh', 0):.1f}/MWh "
                    f"and the curve is in {ttf.get('shape', 'n/a')} "
                    f"({ttf.get('slope_front_to_back_eur_mwh', 0):+.1f} "
                    f"front to back).")
            return " ".join(parts) or "Gas market data is unavailable this run."
        if section == "carbon":
            if not eua:
                return "EUA carbon data is unavailable this run."
            return (
                f"EUA prints EUR {eua.get('spot_eur_t', 0):.1f}/t with 20-day "
                f"momentum at {eua.get('momentum_20d_pct', 0):+.1f} percent."
            )
        if not power:
            return ("German power data is unavailable this run. Forward "
                    "power prices are not available in public data.")
        if spark:
            spark_part = (f"; the clean spark sits at "
                          f"EUR {spark.get('spread_eur_mwh', 0):+.0f}/MWh, "
                          f"{spark.get('gas_regime', 'n/a')}")
        else:
            spark_part = "; the clean spark is unavailable this run"
        return (
            f"Seven-day average power is EUR {power.get('da_7d_eur_mwh', 0):.0f}/MWh "
            f"(30-day EUR {power.get('da_30d_eur_mwh', 0):.0f})"
            f"{spark_part}. Forward power prices are not available in "
            f"public data."
        )

    def drafted(text: str) -> Block:
        return Block("prose", text, origin="drafted", status=PENDING,
                     reviewable=True)

    blocks: list[Block] = []
    bottom = note.bottom_line if note else derived("bottom_line")
    blocks.append(Block(
        "callout", bottom,
        origin="drafted" if note else "derived",
        status=PENDING if note else CONFIRMED, reviewable=bool(note),
    ))

    blocks.append(Block("heading", "Gas"))
    blocks.append(drafted(note.gas) if note else Block("prose", derived("gas")))
    if "storage" in charts:
        blocks.append(Block(
            "chart", (charts["storage"], CHART_FILES["storage"][1])))
    if "curve" in charts:
        blocks.append(Block(
            "chart", (charts["curve"], CHART_FILES["curve"][1])))

    blocks.append(Block("heading", "Carbon"))
    blocks.append(drafted(note.carbon) if note else Block("prose", derived("carbon")))

    blocks.append(Block("heading", "Power"))
    blocks.append(drafted(note.power) if note else Block("prose", derived("power")))
    if "spark" in charts:
        blocks.append(Block(
            "chart", (charts["spark"], CHART_FILES["spark"][1])))

    risks = note.risks if note else FALLBACK_RISKS
    blocks.append(Block("heading", "Key risks"))
    for risk in risks:
        blocks.append(Block(
            "finding", risk,
            origin="drafted" if note else "derived",
            status=PENDING if note else CONFIRMED, reviewable=bool(note),
        ))

    if note is not None and note.analyst_line:
        blocks.append(Block("meta", f"ANALYST: {note.analyst_line}"))
    return blocks


def load_saved_note(folder: Path) -> tuple[dict, DeskNote | None,
                                            dict[str, Path]]:
    """Rebuild the display inputs from a run folder, no network involved."""
    metrics = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))

    note_file = folder / "desk_note.txt"
    note = (DeskNote.parse(note_file.read_text(encoding="utf-8"))
            if note_file.exists() else None)

    charts_dir = folder / "charts"
    charts = {}
    for key, (filename, _) in CHART_FILES.items():
        path = charts_dir / filename
        if path.exists():
            charts[key] = path
    return metrics, note, charts
