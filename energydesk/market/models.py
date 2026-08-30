"""Typed containers for the monitor metrics.

A `MarketSnapshot` is what everything downstream consumes: the charts, the
text summary, the LLM prompt. `to_dict()` produces the flat JSON structure
that gets cached and embedded in prompts.
"""

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class StorageStats:
    """EU gas storage fill versus its own period average."""

    fill_pct: float
    period_avg_pct: float
    gap_pp: float
    as_of_date: str

    def to_dict(self) -> dict:
        return {
            "eu_fill_pct": round(self.fill_pct, 1),
            "period_avg_pct": round(self.period_avg_pct, 1),
            "gap_pp": round(self.gap_pp, 1),
            "as_of_date": self.as_of_date,
        }


@dataclass
class GasCurve:
    """TTF price levels along the curve, from front to last listed forward."""

    prices: dict[str, float]          # label -> latest close, EUR/MWh
    labels: list[str] = field(default_factory=list)  # ordered front -> back

    @property
    def front(self) -> float:
        return self.prices["Front"]

    @property
    def back_label(self) -> str:
        forwards = [l for l in self.labels if l != "Front"]
        return forwards[-1]

    @property
    def slope(self) -> float:
        """How much the furthest forward trades above the front (EUR/MWh).

        Positive means contango (far above front), negative backwardation.
        """
        return self.prices[self.back_label] - self.front

    @property
    def shape(self) -> str:
        if self.slope > 0:
            return "contango"
        if self.slope < 0:
            return "backwardation"
        return "flat"

    def to_dict(self) -> dict:
        return {
            "front_eur_mwh": round(self.front, 2),
            f"{self.back_label.lower().replace(' ', '_')}_eur_mwh":
                round(self.prices[self.back_label], 2),
            "slope_front_to_back_eur_mwh": round(self.slope, 2),
            "shape": self.shape,
        }


@dataclass
class CarbonStats:
    """EUA carbon price and its recent direction."""

    spot: float                       # EUR/t
    momentum_20d_pct: float

    def to_dict(self) -> dict:
        return {
            "spot_eur_t": round(self.spot, 2),
            "momentum_20d_pct": round(self.momentum_20d_pct, 1),
        }


@dataclass
class PowerStats:
    """German day-ahead power, aggregated from hourly prints."""

    daily: pd.Series                  # date -> daily average, EUR/MWh
    avg_7d: float
    avg_30d: float

    def to_dict(self) -> dict:
        return {
            "da_7d_eur_mwh": round(self.avg_7d, 1),
            "da_30d_eur_mwh": round(self.avg_30d, 1),
        }


@dataclass
class SparkResult:
    """Clean spark spread of the benchmark gas plant against 7-day power."""

    srmc: float                       # EUR/MWh
    spread: float                     # power 7d avg minus SRMC
    regime: str

    def to_dict(self) -> dict:
        return {
            "srmc_gas_eur_mwh": round(self.srmc, 1),
            "spread_eur_mwh": round(self.spread, 1),
            "gas_regime": self.regime,
        }


@dataclass
class MarketSnapshot:
    """All monitor metrics for one run date.

    Any metric may be None when its data source failed; the run then
    carries a warning instead of crashing, and renderers print n/a.
    """

    as_of: str
    storage: StorageStats | None
    gas: GasCurve | None
    carbon: CarbonStats | None
    power: PowerStats | None
    spark: SparkResult | None
    assumptions: dict
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {"as_of": self.as_of}
        if self.storage is not None:
            d["gas_storage"] = self.storage.to_dict()
        if self.gas is not None:
            d["ttf"] = self.gas.to_dict()
        if self.carbon is not None:
            d["eua"] = self.carbon.to_dict()
        if self.power is not None:
            d["power_de"] = self.power.to_dict()
        if self.spark is not None:
            d["clean_spark"] = self.spark.to_dict()
        d["assumptions"] = dict(self.assumptions)
        return d
