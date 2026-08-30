"""Turns raw market data into a `MarketSnapshot`.

Everything here is pure: dataframes and series in, metrics out. No network,
no filesystem, which is what makes the calculator easy to test.
"""

from datetime import date

import pandas as pd

from energydesk.config import SparkConventions
from energydesk.market.models import (
    CarbonStats, GasCurve, MarketSnapshot, PowerStats, SparkResult, StorageStats,
)


class MetricCalculator:
    """Computes each monitor metric, then assembles them into a snapshot."""

    def __init__(self, conventions: SparkConventions):
        self.conventions = conventions

    # -- individual metrics --------------------------------------------------

    def compute_storage(self, storage: pd.DataFrame) -> StorageStats:
        """Fill level now versus the average over the loaded window."""
        fill = storage["fill_pct"].dropna()
        latest = fill.iloc[-1]
        return StorageStats(
            fill_pct=float(latest),
            period_avg_pct=float(fill.mean()),
            gap_pp=float(latest - fill.mean()),
            as_of_date=pd.Timestamp(storage["date"].iloc[-1]).strftime("%Y-%m-%d"),
        )

    def compute_curve(self, prices: dict[str, float],
                      labels: list[str]) -> GasCurve:
        """Curve levels and shape from the latest close of each contract."""
        available = [label for label in labels if label in prices]
        if "Front" not in available or len(available) < 2:
            raise ValueError(
                "need at least the TTF front and one forward to read the curve"
            )
        return GasCurve(prices={k: prices[k] for k in available}, labels=available)

    def compute_carbon(self, eua: pd.Series) -> CarbonStats:
        """EUA spot and its ~20 trading day momentum."""
        series = eua.dropna()
        if series.empty:
            raise ValueError("EUA price series is empty")
        spot = float(series.iloc[-1])
        reference = float(series.iloc[-21] if len(series) > 20 else series.iloc[0])
        momentum = (spot / reference - 1) * 100
        return CarbonStats(spot=spot, momentum_20d_pct=momentum)

    def compute_power(self, power_hourly: pd.DataFrame) -> PowerStats:
        """Hourly day-ahead prints averaged to daily, then 7d/30d means."""
        hourly = power_hourly.dropna(subset=["price"]).copy()
        if hourly.empty:
            raise ValueError("power price frame is empty")
        hourly["date"] = pd.to_datetime(hourly["timestamp"]).dt.date
        daily = hourly.groupby("date")["price"].mean()
        return PowerStats(
            daily=daily,
            avg_7d=float(daily.tail(7).mean()),
            avg_30d=float(daily.tail(30).mean()),
        )

    def compute_spark(self, gas_front: float, carbon: float,
                      power_7d: float) -> SparkResult:
        """Clean spark spread of a benchmark CCGT running on TTF + EUA.

        SRMC = fuel cost at plant efficiency + carbon cost + fixed variable
        opex. The spread is what the plant earns per MWh burned at the last
        7-day average power price.
        """
        c = self.conventions
        srmc = gas_front / c.efficiency + carbon * c.emission_factor + c.variable_opex
        spread = power_7d - srmc
        regime = "in the money" if spread >= 0 else "out of the money"
        return SparkResult(srmc=srmc, spread=spread, regime=f"gas {regime}")

    # -- assembly -------------------------------------------------------------

    @staticmethod
    def _try(label: str, warns: list[str], fn):
        """Run one metric; a failure degrades that metric instead of the run."""
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            warns.append(f"{label} unavailable ({exc})")
            return None

    def build(self, as_of: date, storage: pd.DataFrame, curve_prices: dict[str, float],
              curve_labels: list[str], eua: pd.Series,
              power_hourly: pd.DataFrame) -> MarketSnapshot:
        """Run every metric and bundle the results.

        Each metric degrades independently: a missing forward quote or an
        empty series costs one warning, never the whole snapshot.
        """
        c = self.conventions
        warns: list[str] = []
        storage_stats = self._try(
            "EU gas storage", warns, lambda: self.compute_storage(storage))
        curve = self._try(
            "TTF curve", warns,
            lambda: self.compute_curve(curve_prices, curve_labels))
        carbon = self._try(
            "EUA carbon", warns, lambda: self.compute_carbon(eua))
        power = self._try(
            "DE day-ahead power", warns,
            lambda: self.compute_power(power_hourly))

        spark = None
        if curve and carbon and power:
            spark = self._try(
                "clean spark spread", warns,
                lambda: self.compute_spark(curve.front, carbon.spot,
                                           power.avg_7d))
        elif curve is None or carbon is None or power is None:
            missing = ", ".join(
                name for name, stat in (("TTF", curve), ("EUA", carbon),
                                        ("power", power)) if stat is None)
            warns.append(f"clean spark spread skipped (needs {missing})")

        return MarketSnapshot(
            as_of=as_of.strftime("%Y-%m-%d"),
            storage=storage_stats,
            gas=curve,
            carbon=carbon,
            power=power,
            spark=spark,
            assumptions={
                "gas_efficiency": c.efficiency,
                "gas_emissions_tco2_mwh": c.emission_factor,
                "vom_eur_mwh": c.variable_opex,
            },
            warnings=warns,
        )
