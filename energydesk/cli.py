"""Command line entry points.

    python -m energydesk check          verify keys, model chain and cache
    python -m energydesk run            full run (data + charts + desk note)
        --no-research                   skip the web research loop
        --force-refresh                 refetch everything even if cache is fresh
"""

import argparse
import sys

from energydesk.config import Settings
from energydesk.pipeline import DataError, DeskMonitor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="energydesk",
        description="European gas / carbon / German power daily monitor",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="run today's monitor")
    run_parser.add_argument("--no-research", action="store_true",
                            help="draft the note without web research")
    run_parser.add_argument("--force-refresh", action="store_true",
                            help="ignore fresh cache and refetch all sources")

    sub.add_parser("check", help="show configuration and key status")

    args = parser.parse_args(argv)
    if args.command == "check":
        return _check()

    monitor = DeskMonitor()
    try:
        result = monitor.run(do_research=not args.no_research,
                             force_refresh=args.force_refresh)
    except DataError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    print("\n" + "=" * 60)
    for warning in result.warnings:
        print(f"warning: {warning}")
    print(f"done. outputs in {monitor.settings.runs_dir}")
    return 0


def _check() -> int:
    settings = Settings.from_env()
    print("configuration")
    print(f"  GIE API key       : {'set' if settings.has_gie else 'MISSING (storage falls back to cache)'}")
    print(f"  Gemini API key    : {'set' if settings.has_llm else 'MISSING (desk note will be skipped)'}")
    print(f"  models            : {' -> '.join(settings.gemini_models)}")
    print(f"  bidding zone      : {settings.power_bidding_zone}")
    print(f"  conventions       : {settings.conventions.describe()}")
    print(f"  cache dir         : {settings.cache_dir}")
    print(f"  runs dir          : {settings.runs_dir}")
    if not settings.has_gie or not settings.has_llm:
        print("\ntip: put your free keys in a .env file at the project root "
              "(GIE_API_KEY, GEMINI_API_KEY)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
