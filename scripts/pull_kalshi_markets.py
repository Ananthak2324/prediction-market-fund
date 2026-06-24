"""
Pull all settled Kalshi markets for NBA, NFL, and MLB and save to JSON.

Usage:
    python scripts/pull_kalshi_markets.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from data.clients.kalshi_client import SPORT_SERIES, get_settled_markets
from core.logger import logger

OUTPUT_DIR = "data/raw/kalshi"


def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for sport, series_ticker in SPORT_SERIES.items():
        logger.info(f"Pulling {sport.upper()} markets ({series_ticker})...")
        markets = get_settled_markets(series_ticker)

        out_path = os.path.join(OUTPUT_DIR, f"{sport}_markets.json")
        with open(out_path, "w") as f:
            json.dump(markets, f, indent=2)

        print(f"\n{sport.upper()}: {len(markets)} markets saved to {out_path}")

        if markets:
            print(f"Example market:")
            print(json.dumps(markets[0], indent=2))

    print("\nDone. All three sports saved.")


if __name__ == "__main__":
    main()
