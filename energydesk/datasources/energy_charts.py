"""German day-ahead power prices from Energy-Charts (Fraunhofer ISE).

Free public API, no key required: https://api.energy-charts.info/price
Prices come back hourly; the market layer aggregates them to daily averages.
"""

from datetime import datetime, timedelta

import pandas as pd
import requests

from energydesk.datasources.base import DataSource

PRICE_URL = "https://api.energy-charts.info/price"


class PowerPriceSource(DataSource):
    """Hourly day-ahead prices for one bidding zone."""

    name = "power_de_hourly"
    expected_columns = ("timestamp", "price")

    def __init__(self, cache_dir, bidding_zone: str = "DE-LU", days: int = 60,
                 **kwargs):
        super().__init__(cache_dir, **kwargs)
        self.bidding_zone = bidding_zone
        self.days = days

    def fetch(self) -> pd.DataFrame:
        today = datetime.now()
        params = {
            "bzn": self.bidding_zone,
            "start": (today - timedelta(days=self.days)).strftime("%Y-%m-%d"),
            "end": today.strftime("%Y-%m-%d"),
        }
        response = requests.get(PRICE_URL, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()

        hourly = pd.DataFrame({
            "timestamp": pd.to_datetime(payload["unix_seconds"], unit="s"),
            "price": payload["price"],
        })
        # Missing hours come back as None entries, not NaN.
        hourly = hourly.dropna().reset_index(drop=True)
        if hourly.empty:
            raise RuntimeError(f"Energy-Charts returned no prices for {self.bidding_zone}")
        return hourly
