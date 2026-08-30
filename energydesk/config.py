"""Project settings: paths, API keys and market conventions.

Everything configurable lives here so the rest of the package never touches
os.environ directly. Keys are read from a `.env` file at the project root;
they are optional, the pipeline degrades gracefully without them.
"""

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Project root = one level above this file (energydesk/config.py).
PROJECT_ROOT = Path(__file__).resolve().parents[1]


# Gemini models, tried in order. The -latest aliases follow Google's
# current flash generation, so catalog rotations do not break the note.
DEFAULT_GEMINI_MODELS = ("gemini-flash-latest", "gemini-flash-lite-latest")


def writable_root() -> Path:
    """Directory where the monitor may write its cache and run outputs.

    Locally that is simply the project folder. Hosted environments such as
    Streamlit Community Cloud mount the repo read-only, so there the temp
    directory is used instead - everything regenerates anyway.
    """
    probe = PROJECT_ROOT / ".write_probe"
    try:
        probe.touch()
        probe.unlink()
        return PROJECT_ROOT
    except OSError:
        base = Path(tempfile.gettempdir()) / "energydesk"
        base.mkdir(parents=True, exist_ok=True)
        return base


# Market conventions for the clean spark spread. A 50% efficient CCGT is the
# benchmark the desk quotes against, and 0.37 tCO2/MWh is the emissions rate
# that follows from that efficiency.
DEFAULT_EFFICIENCY = 0.50
DEFAULT_EMISSION_FACTOR = 0.37  # tCO2 per MWh of power
DEFAULT_VARIABLE_OPEX = 2.0     # EUR/MWh


@dataclass(frozen=True)
class SparkConventions:
    """Assumptions behind the benchmark gas plant used in the clean spark."""

    efficiency: float = DEFAULT_EFFICIENCY
    emission_factor: float = DEFAULT_EMISSION_FACTOR
    variable_opex: float = DEFAULT_VARIABLE_OPEX

    def describe(self) -> str:
        return (
            f"gas efficiency {self.efficiency:.0%}, "
            f"emissions {self.emission_factor} tCO2/MWh, "
            f"VOM EUR {self.variable_opex:.0f}/MWh"
        )


@dataclass
class Settings:
    """Runtime configuration assembled from `.env` with sane defaults."""

    gie_api_key: str = ""
    gemini_api_key: str = ""
    gemini_models: list[str] = field(
        default_factory=lambda: list(DEFAULT_GEMINI_MODELS)
    )
    conventions: SparkConventions = field(default_factory=SparkConventions)
    power_bidding_zone: str = "DE-LU"
    storage_area: str = "eu"
    cache_dir: Path = field(default_factory=lambda: writable_root() / "data" / "cache")
    runs_dir: Path = field(default_factory=lambda: writable_root() / "runs")
    request_timeout: int = 30
    cache_max_age_hours: float = 12.0

    @classmethod
    def from_env(cls, root: Path = PROJECT_ROOT) -> "Settings":
        load_dotenv(root / ".env")
        models = [
            m.strip() for m in os.getenv("GEMINI_MODELS", "").split(",")
            if m.strip()
        ] or list(DEFAULT_GEMINI_MODELS)
        return cls(
            gie_api_key=os.getenv("GIE_API_KEY", "").strip(),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            gemini_models=models,
        )

    @property
    def has_llm(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_gie(self) -> bool:
        return bool(self.gie_api_key)
