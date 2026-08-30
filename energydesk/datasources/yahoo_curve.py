"""Futures prices from Yahoo Finance: the TTF gas curve and EUA carbon.

The TTF contracts are not fixed tickers: the listed months roll forward as
time passes. `ttf_contracts()` builds the front continuous contract plus the
next few bi-monthly deliveries from today's date, so the curve stays valid
without manual maintenance.
"""

from datetime import date, timedelta

import pandas as pd
import requests
import yfinance as yf

from energydesk.datasources.base import DataSource

# NYMEX month codes, as used in ICE TTF ticker suffixes on Yahoo.
MONTH_CODES = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}

# TTF monthly futures on Yahoo are listed for even delivery months only,
# so the cycle walks June -> August -> October -> December -> February -> April.
DELIVERY_CYCLE = [6, 8, 10, 12, 2, 4]

# A contract is kept while its delivery month starts at least this far ahead:
# closer than that it has almost no open interest left and just adds noise.
MIN_DAYS_TO_DELIVERY = 10


def ttf_contracts(as_of: date | None = None, forwards: int = 3) -> dict[str, str]:
    """Front continuous plus the next `forwards` bi-monthly TTF deliveries.

    Returns a mapping of human readable label to Yahoo ticker, ordered from
    front to back, e.g. {"Front": "TTF=F", "Jun 2026": "TTFM26.NYM", ...}.
    """
    as_of = as_of or date.today()
    cutoff = as_of + timedelta(days=MIN_DAYS_TO_DELIVERY)

    candidates = []
    for year in (as_of.year, as_of.year + 1):
        for month in DELIVERY_CYCLE:
            candidates.append((year, month))
    candidates.sort(key=lambda ym: (ym[0], ym[1]))

    contracts = {"Front": "TTF=F"}
    for year, month in candidates:
        if date(year, month, 1) <= cutoff:
            continue
        code = f"TTF{MONTH_CODES[month]}{year % 100:02d}.NYM"
        contracts[f"{date(year, month, 1):%b %Y}"] = code
        if len(contracts) > forwards:
            break
    return contracts


YAHOO_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126 Safari/537.36")


class YahooFuturesSource(DataSource):
    """Closing prices for a fixed set of futures contracts.

    One column per contract label. Dead or empty tickers are skipped with a
    warning instead of breaking the run; the caller decides which columns it
    really needs. When the yfinance library comes back empty or blocked -
    common on shared hosting IPs - the same closes are fetched from Yahoo's
    plain chart endpoint before giving up on a ticker.
    """

    def __init__(self, cache_dir, name: str, contracts: dict[str, str],
                 period: str = "6mo", **kwargs):
        super().__init__(cache_dir, **kwargs)
        self.name = name
        self.contracts = dict(contracts)
        self.period = period
        self.expected_columns = tuple(self.contracts)
        self.min_cached_series = 2 if len(self.contracts) > 1 else 1
        self.session = requests.Session()
        self.session.headers["User-Agent"] = YAHOO_UA

    def fetch(self) -> pd.DataFrame:
        frames = {}
        for label, ticker in self.contracts.items():
            close = self._close_series(ticker)
            if close is None or close.empty:
                self.warn(f"no data for {ticker}, skipping {label}")
                continue
            frames[label] = close.rename(label)

        if not frames:
            raise RuntimeError(f"none of the tickers returned data: {list(self.contracts.values())}")

        curve = pd.DataFrame(frames).sort_index()
        curve.index.name = "date"
        return curve.reset_index()

    def _close_series(self, ticker: str) -> pd.Series | None:
        try:
            df = yf.download(ticker, period=self.period,
                             progress=False, auto_adjust=True)
            if not df.empty:
                close = df["Close"]
                if isinstance(close, pd.DataFrame):  # newer yfinance wraps in one column
                    close = close.iloc[:, 0]
                return close
            self.warn(f"yfinance returned nothing for {ticker}; "
                      "trying the plain chart endpoint")
        except Exception as exc:
            self.warn(f"yfinance failed for {ticker} ({exc}); "
                      "trying the plain chart endpoint")
        try:
            return self._chart_close(ticker)
        except Exception as exc:
            self.warn(f"chart endpoint failed for {ticker} ({exc})")
            return None

    def _chart_close(self, ticker: str) -> pd.Series | None:
        """Daily closes from the public chart endpoint, no library involved."""
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        response = self.session.get(url, params={"range": self.period,
                                                 "interval": "1d"}, timeout=20)
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        stamps = result.get("timestamp") or []
        quotes = result.get("indicators", {}).get("quote", [{}])
        closes = quotes[0].get("close") or []
        series = pd.Series(closes, index=pd.to_datetime(stamps, unit="s"),
                           name=ticker).dropna()
        return series if not series.empty else None

    def last_prices(self, df: pd.DataFrame) -> dict[str, float]:
        """Latest non-NaN close per label.

        The latest settlement is not always published on the most recent
        trading day, so the last valid value is used rather than the last row.
        """
        prices = {}
        for label in df.columns:
            if label == "date":
                continue
            series = df[label].dropna()
            if not series.empty:
                prices[label] = float(series.iloc[-1])
        return prices


def carbon_contract() -> dict[str, str]:
    """ICE EUA carbon front, quoted in EUR per tonne of CO2."""
    return {"EUA": "CO2.L"}
