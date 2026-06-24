"""
Pull candlestick data for every Kalshi market in the saved markets JSON files.

Reads:  data/raw/kalshi/{sport}_markets.json
Writes: data/raw/kalshi/{sport}_candles.json

Usage:
    python scripts/pull_kalshi_candles.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from data.clients.kalshi_client import get_market_candlesticks
from core.logger import logger

DATA_DIR = "data/raw/kalshi"
SPORTS = ["nba", "nfl", "mlb"]
SLEEP_BETWEEN = 0.2
PROGRESS_EVERY = 50


def main() -> None:
    for sport in SPORTS:
        markets_path = os.path.join(DATA_DIR, f"{sport}_markets.json")
        if not os.path.exists(markets_path):
            logger.warning(f"{markets_path} not found — run pull_kalshi_markets.py first")
            continue

        with open(markets_path) as f:
            markets = json.load(f)

        tickers = [m["ticker"] for m in markets if m.get("ticker")]
        logger.info(f"{sport.upper()}: pulling candles for {len(tickers)} markets")

        candles: dict[str, list] = {}
        for i, ticker in enumerate(tickers, 1):
            try:
                data = get_market_candlesticks(ticker)
                candles[ticker] = data
            except Exception as e:
                logger.warning(f"Failed candles for {ticker}: {e}")
                candles[ticker] = []

            if i % PROGRESS_EVERY == 0:
                print(f"  {sport.upper()}: {i}/{len(tickers)} complete")

            time.sleep(SLEEP_BETWEEN)

        out_path = os.path.join(DATA_DIR, f"{sport}_candles.json")
        with open(out_path, "w") as f:
            json.dump(candles, f, indent=2)

        print(f"{sport.upper()}: candles saved to {out_path} ({len(candles)} markets)")

    print("\nDone.")


if __name__ == "__main__":
    main()
