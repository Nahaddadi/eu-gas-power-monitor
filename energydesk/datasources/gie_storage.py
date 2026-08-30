"""EU gas storage from the GIE AGSI+ API (https://agsi.gie.eu).

Works with a free API key (see `.env.example`); without one, `DataSource.load`
falls back to the cached file.
"""

from datetime import datetime, timedelta

import pandas as pd
import requests

from energydesk.datasources.base import DataSource

AGSI_URL = "https://agsi.gie.eu/api"


class GieStorageSource(DataSource):
    """Aggregated EU gas storage fill level, one row per gas day."""

    name = "gas_storage_eu"
    expected_columns = ("date", "fill_pct")

    def __init__(self, cache_dir, area: str = "eu", days: int = 300,
                 api_key: str = "", **kwargs):
        super().__init__(cache_dir, **kwargs)
        self.area = area
        self.days = days
        self.api_key = api_key

    def fetch(self) -> pd.DataFrame:
        today = datetime.now()
        params = {
            "type": self.area,
            "from": (today - timedelta(days=self.days)).strftime("%Y-%m-%d"),
            "to": today.strftime("%Y-%m-%d"),
            "size": 300,
        }
        response = requests.get(
            AGSI_URL, params=params, timeout=30,
            headers={"x-key": self.api_key},
        )
        response.raise_for_status()
        rows = response.json().get("data", [])
        if not rows:
            raise RuntimeError("GIE returned no data - check the API key or parameters")
        return self.clean(pd.DataFrame(rows))

    @staticmethod
    def clean(raw: pd.DataFrame) -> pd.DataFrame:
        """Keep only what the monitor uses: gas day and fill level."""
        df = raw[["gasDayStart", "full"]].copy()
        df["gasDayStart"] = pd.to_datetime(df["gasDayStart"])
        df["full"] = pd.to_numeric(df["full"], errors="coerce")
        df = df.dropna().sort_values("gasDayStart").reset_index(drop=True)
        return df.rename(columns={"gasDayStart": "date", "full": "fill_pct"})
