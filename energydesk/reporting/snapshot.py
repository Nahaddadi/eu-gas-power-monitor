"""Plain text rendering of a snapshot, for the console and the run folder."""

from energydesk.market.models import MarketSnapshot


def format_snapshot_text(snapshot: MarketSnapshot) -> str:
    """Render the snapshot as one compact block of metric lines."""
    m = snapshot
    lines = [f"SNAPSHOT - {m.as_of}", ""]

    if m.storage:
        lines.append(
            f"EU gas storage   : {m.storage.fill_pct:.0f}%  "
            f"({m.storage.gap_pp:+.0f} pp vs period avg "
            f"{m.storage.period_avg_pct:.0f}%)")
    else:
        lines.append("EU gas storage   : n/a (source unavailable)")
    if m.gas:
        lines.append(
            f"TTF front        : EUR {m.gas.front:.1f}/MWh  "
            f"(curve {m.gas.shape}, {m.gas.slope:+.1f} "
            f"front->{m.gas.back_label.split()[0]})")
    else:
        lines.append("TTF front        : n/a (curve quotes unavailable)")
    if m.carbon:
        lines.append(
            f"EUA              : EUR {m.carbon.spot:.1f}/t  "
            f"(20d momentum {m.carbon.momentum_20d_pct:+.1f}%)")
    else:
        lines.append("EUA              : n/a (source unavailable)")
    if m.power:
        lines.append(
            f"DE power 7d avg  : EUR {m.power.avg_7d:.0f}/MWh  "
            f"(30d avg EUR {m.power.avg_30d:.0f})")
    else:
        lines.append("DE power 7d avg  : n/a (source unavailable)")
    if m.spark:
        lines += [
            f"Clean spark 7d   : EUR {m.spark.spread:+.0f}/MWh "
            f"({m.spark.regime})",
            f"Gas SRMC         : EUR {m.spark.srmc:.0f}/MWh",
        ]
    else:
        lines.append("Clean spark 7d   : n/a (needs gas, carbon and power)")
    lines += ["", f"Assumptions: {', '.join(_assumption_parts(m))}."]
    if m.warnings:
        lines.append("Warnings: " + "; ".join(m.warnings))
    return "\n".join(lines)


def _assumption_parts(m: MarketSnapshot) -> list[str]:
    a = m.assumptions
    return [
        f"gas efficiency {a['gas_efficiency'] * 100:.0f}%",
        f"emissions {a['gas_emissions_tco2_mwh']} tCO2/MWh",
        f"VOM EUR {a['vom_eur_mwh']:.0f}/MWh",
    ]


def snapshot_table(snapshot: MarketSnapshot) -> list[dict[str, str]]:
    """Metric / value / context rows, handy for a UI table."""
    m = snapshot
    rows = []
    if m.storage:
        rows.append(
            {"metric": "EU gas storage", "value": f"{m.storage.fill_pct:.0f}%",
             "context": f"{m.storage.gap_pp:+.0f} pp vs period avg "
                        f"({m.storage.period_avg_pct:.0f}%)"},
        )
    else:
        rows.append({"metric": "EU gas storage", "value": "n/a",
                     "context": "source unavailable this run"})
    if m.gas:
        rows.append(
            {"metric": "TTF front month", "value": f"EUR {m.gas.front:.1f}/MWh",
             "context": f"{m.gas.shape.capitalize()} {m.gas.slope:+.1f} "
                        f"front->{m.gas.back_label.split()[0]}"},
        )
    else:
        rows.append({"metric": "TTF front month", "value": "n/a",
                     "context": "curve quotes unavailable this run"})
    if m.carbon:
        rows.append(
            {"metric": "EUA (CO2)", "value": f"EUR {m.carbon.spot:.1f}/t",
             "context": f"20d momentum: {m.carbon.momentum_20d_pct:+.1f}%"},
        )
    else:
        rows.append({"metric": "EUA (CO2)", "value": "n/a",
                     "context": "source unavailable this run"})
    if m.power:
        rows.append(
            {"metric": "DE power 7d avg",
             "value": f"EUR {m.power.avg_7d:.0f}/MWh",
             "context": f"30d avg EUR {m.power.avg_30d:.0f}/MWh"},
        )
    else:
        rows.append({"metric": "DE power 7d avg", "value": "n/a",
                     "context": "source unavailable this run"})
    if m.spark:
        rows.append(
            {"metric": "Clean spark spread 7d",
             "value": f"EUR {m.spark.spread:+.0f}/MWh",
             "context": m.spark.regime},
        )
        rows.append(
            {"metric": "Gas SRMC", "value": f"EUR {m.spark.srmc:.0f}/MWh",
             "context": ", ".join(_assumption_parts(m))},
        )
    else:
        rows.append({"metric": "Clean spark spread 7d", "value": "n/a",
                     "context": "needs gas, carbon and power quotes"})
    return rows
