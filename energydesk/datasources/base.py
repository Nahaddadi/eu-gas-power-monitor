"""Shared behaviour for the data feeds: disk cache with a maximum age,
bounded fetch retries, and fallback to a stale cache file when the live
fetch keeps failing."""

import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


class DataSource(ABC):
    """Base class for the data feeds used by the monitor."""

    name = "source"

    # Columns a cached file must contain to be usable. Sources with a stable
    # schema set this; a mismatch means the cache format is outdated.
    expected_columns: tuple[str, ...] = ()

    # Minimum number of data series a cached file must carry.
    min_cached_series: int = 1

    # Hosted IPs share rate limits with everybody else, so one refused
    # request often just needs a short pause and another go.
    fetch_attempts: int = 3
    retry_delay_secs: float = 8.0

    def __init__(self, cache_dir: Path, max_age_hours: float = 12.0,
                 fetch_attempts: int | None = None,
                 retry_delay_secs: float | None = None):
        self.cache_dir = Path(cache_dir)
        self.max_age_hours = max_age_hours
        if fetch_attempts is not None:
            self.fetch_attempts = fetch_attempts
        if retry_delay_secs is not None:
            self.retry_delay_secs = retry_delay_secs
        self.warnings: list[str] = []

    # -- public API ---------------------------------------------------------

    def load(self) -> pd.DataFrame:
        """Return the dataset, from cache when fresh enough, else live."""
        cached = self.read_cache()
        if cached is not None and self.cache_is_fresh():
            return cached
        last_exc: Exception | None = None
        for attempt in range(1, self.fetch_attempts + 1):
            try:
                df = self.fetch()
                self.write_cache(df)
                return df
            except Exception as exc:
                last_exc = exc
                if attempt < self.fetch_attempts:
                    print(f"[{self.name}] fetch attempt {attempt} failed "
                          f"({exc}); retrying in {self.retry_delay_secs:.0f}s")
                    time.sleep(self.retry_delay_secs)
        if cached is not None:
            self.warn(f"live fetch failed ({last_exc}); using cached file instead")
            return cached
        raise last_exc

    @abstractmethod
    def fetch(self) -> pd.DataFrame:
        """Hit the live API and return a cleaned dataframe."""

    # -- cache helpers ------------------------------------------------------

    @property
    def cache_path(self) -> Path:
        return self.cache_dir / f"{self.name}.csv"

    def read_cache(self) -> pd.DataFrame | None:
        if not self.cache_path.exists():
            return None
        try:
            df = pd.read_csv(self.cache_path)
        except Exception as exc:
            self.warn(f"cache file unreadable ({exc})")
            return None
        if self.expected_columns:
            # A cached frame is usable when its columns are all still known
            # (no format drift) and it carries enough data series. Futures
            # sources legitimately hold fewer columns than configured when
            # some listed contracts are not available yet.
            cached_labels = set(df.columns) - {"date"}
            known_labels = set(self.expected_columns) - {"date"}
            if not cached_labels <= known_labels \
                    or len(cached_labels) < self.min_cached_series:
                self.warn("cache file has an outdated format; it will be refreshed")
                return None
        return df

    def write_cache(self, df: pd.DataFrame) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.cache_path, index=False)

    def cache_is_fresh(self) -> bool:
        if not self.cache_path.exists():
            return False
        age = datetime.now() - datetime.fromtimestamp(self.cache_path.stat().st_mtime)
        return age < timedelta(hours=self.max_age_hours)

    def warn(self, message: str) -> None:
        text = f"[{self.name}] {message}"
        self.warnings.append(text)
        print(text)
