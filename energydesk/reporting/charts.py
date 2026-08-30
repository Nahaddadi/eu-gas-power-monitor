"""Chart rendering for the daily pack: storage vs period average,
TTF curve today vs one month ago, power vs gas SRMC with the spark below."""

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from energydesk.market.models import MarketSnapshot

CHART_STYLE = {
    "figure.figsize": (10, 5),
    "savefig.dpi": 150,
    "savefig.bbox": "tight",
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "legend.frameon": False,
}

NAVY, RED, GREEN, GREY = "#1f3a68", "#c44d4d", "#3a9b5c", "#888888"


def apply_chart_style() -> None:
    plt.rcParams.update(CHART_STYLE)


def _close_n_sessions(series: pd.Series, n: int = 22) -> float:
    """Value roughly n sessions back; falls back to the first point."""
    s = series.dropna()
    return float(s.iloc[-n] if len(s) > n else s.iloc[0])


class ChartBuilder:
    """Renders the three pack charts into a run's output folder."""

    def __init__(self, snapshot: MarketSnapshot, storage: pd.DataFrame,
                 curve: pd.DataFrame, out_dir: Path):
        self.snapshot = snapshot
        self.storage = storage
        self.curve = curve
        self.out_dir = Path(out_dir)

    def render_all(self) -> list[Path]:
        apply_chart_style()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for label, guard, render in (
            ("storage chart", lambda: self.snapshot.storage is not None,
             self.render_storage),
            ("TTF curve chart", lambda: self.snapshot.gas is not None,
             self.render_curve),
            ("power/spark chart",
             lambda: self.snapshot.power is not None
             and self.snapshot.spark is not None,
             self.render_power_spark),
        ):
            if not guard():
                print(f"[charts] {label} skipped - metric unavailable")
                continue
            try:
                paths.append(render())
            except Exception as exc:  # noqa: BLE001
                print(f"[charts] {label} skipped ({exc})")
        return paths

    # -- individual charts -----------------------------------------------------

    def render_storage(self) -> Path:
        s = self.snapshot.storage
        dates = pd.to_datetime(self.storage["date"])
        fill = self.storage["fill_pct"]

        fig, ax = plt.subplots()
        ax.plot(dates, fill, color=NAVY, lw=1.8, label="EU storage")
        ax.axhline(s.period_avg_pct, color=GREY, ls="--", lw=1.2,
                   label=f"Period average ({s.period_avg_pct:.0f}%)")

        # Shade the gap against the average so the deficit is obvious.
        ax.fill_between(dates, fill, s.period_avg_pct,
                        where=fill < s.period_avg_pct, color=RED,
                        alpha=0.12, interpolate=True)
        ax.fill_between(dates, fill, s.period_avg_pct,
                        where=fill >= s.period_avg_pct, color=GREEN,
                        alpha=0.10, interpolate=True)

        last_date = dates.iloc[-1]
        ax.annotate(
            f"{s.fill_pct:.0f}%  ({s.gap_pp:+.0f} pp vs avg)",
            xy=(last_date, s.fill_pct), xytext=(-110, 20),
            textcoords="offset points", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec=GREY, lw=0.5),
            arrowprops=dict(arrowstyle="-", color=GREY),
        )

        ax.set_title(f"EU Gas Storage vs Period Average  ({self.snapshot.as_of})")
        ax.set_ylabel("% full")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        ax.legend(loc="upper right")
        fig.text(0.9, 0.02, "Source: GIE AGSI+", ha="right",
                 fontsize=8, color=GREY, style="italic")

        path = self.out_dir / "1_storage.png"
        fig.savefig(path)
        plt.close(fig)
        return path

    def render_curve(self) -> Path:
        snap = self.snapshot.gas
        labels = snap.labels
        today = [snap.prices[label] for label in labels]
        month_ago = [
            _close_n_sessions(self.curve[label]) if label in self.curve else today[i]
            for i, label in enumerate(labels)
        ]
        x = range(len(labels))

        fig, ax = plt.subplots()
        ax.plot(x, today, "o-", color=NAVY, lw=2, ms=8,
                label=f"Today ({self.snapshot.as_of})")
        ax.plot(x, month_ago, "s--", color=GREY, lw=1.5, ms=7,
                label="1 month ago", alpha=0.8)
        for xi, v in zip(x, today):
            ax.annotate(f"{v:.1f}", (xi, v), textcoords="offset points",
                        xytext=(0, 10), ha="center", fontsize=9,
                        color=NAVY, fontweight="bold")

        ax.set_xticks(list(x))
        ax.set_xticklabels(labels)
        ax.set_ylabel("EUR/MWh")
        ax.set_title(
            f"TTF Forward Curve - {snap.shape} "
            f"({snap.slope:+.1f} front->{snap.back_label.split()[0]})  "
            f"({self.snapshot.as_of})"
        )
        ax.legend(loc="upper right")
        fig.text(0.9, 0.02, "Source: ICE TTF via Yahoo Finance", ha="right",
                 fontsize=8, color=GREY, style="italic")

        path = self.out_dir / "2_ttf_curve.png"
        fig.savefig(path)
        plt.close(fig)
        return path

    def render_power_spark(self) -> Path:
        power = self.power_daily()
        spark = power - self.snapshot.spark.srmc

        fig, (top, bot) = plt.subplots(
            2, 1, figsize=(10, 7),
            gridspec_kw={"height_ratios": [2, 1]}, sharex=True,
        )
        top.plot(power.index, power.values, color=NAVY, lw=1.6,
                 label="DE power Day-Ahead (daily avg)")
        top.axhline(self.snapshot.spark.srmc, color=RED, ls="--", lw=1.2,
                    label=f"Gas SRMC ({self.snapshot.spark.srmc:.0f})")
        top.set_ylabel("EUR/MWh")
        top.set_title(
            f"German Power vs Gas Marginal Cost - 7d avg "
            f"{self.snapshot.power.avg_7d:.0f} EUR/MWh  ({self.snapshot.as_of})"
        )
        top.legend(loc="upper left", fontsize=9)
        fig.text(0.9, 0.52, "Source: Energy-Charts, GIE, ICE", ha="right",
                 fontsize=8, color=GREY, style="italic")

        bot.bar(power.index, spark.values, width=1.0,
                color=[GREEN if v > 0 else RED for v in spark.values], alpha=0.7)
        bot.axhline(0, color="black", lw=0.5)
        bot.set_ylabel("Clean spark\nEUR/MWh")
        bot.set_title(
            f"Clean Spark Spread - 7d avg {self.snapshot.spark.spread:+.0f} EUR/MWh",
            fontsize=10,
        )
        bot.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))

        fig.tight_layout()
        path = self.out_dir / "3_power_spark.png"
        fig.savefig(path)
        plt.close(fig)
        return path

    # -- helpers ---------------------------------------------------------------

    def power_daily(self) -> pd.Series:
        """Daily averages indexed by date, ready to plot."""
        daily = self.snapshot.power.daily
        index = pd.to_datetime(pd.Series(list(daily.index)))
        return pd.Series(daily.values, index=index.values)
