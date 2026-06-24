"""
Pull pre-game Vegas h2h moneylines from TheOddsAPI for every game start time
slot found in the Kalshi market files (MLB and NBA).

Strategy:
  - Groups Kalshi markets by occurrence_datetime (game start time, truncated to hour)
  - Queries a 4-hour window ending at game start time for each slot
  - TheOddsAPI returns rows newest-first; page 1 = latest pre-game prices
  - Saves one JSON file per (sport, date_hour) slot

Output:
    data/raw/vegas/mlb/2026-06-20T23.json   (h2h rows for games starting ~23:xx UTC)
    data/raw/vegas/nba/2026-06-20T23.json

Coverage: TheOddsAPI historical data starts ~2026-05-13.
          Games before that date will yield empty files and are skipped on re-run.

IMPORTANT: stops immediately if credits drop below 100.
           MLB first (larger dataset), then NBA.

Usage:
    python scripts/fetch_odds_history.py
    python scripts/fetch_odds_history.py --sport mlb
    python scripts/fetch_odds_history.py --dry-run
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from data.clients.odds_client import SPORT_KEYS, get_historical_odds, get_remaining_credits
from core.logger import logger

KALSHI_DIR = "data/raw/kalshi"
OUTPUT_BASE = "data/raw/vegas"
SLEEP_BETWEEN = 1.2
MIN_DATE = "2026-05-13"  # Earliest date with TheOddsAPI coverage

SPORT_CONFIG = {
    "mlb": {"kalshi_file": "mlb_markets.json", "sport_key": SPORT_KEYS["mlb"]},
    "nba": {"kalshi_file": "nba_markets.json", "sport_key": SPORT_KEYS["nba"]},
}


def extract_slots(markets_path: str) -> list[str]:
    """Return sorted list of unique occurrence_datetime truncated to the hour.

    Each slot becomes one API query covering a 4-hour window ending at that hour.
    Only includes slots from MIN_DATE onwards (API coverage boundary).
    """
    if not os.path.exists(markets_path):
        logger.warning(f"{markets_path} not found")
        return []
    with open(markets_path) as f:
        markets = json.load(f)
    slots = set()
    for m in markets:
        occ = m.get("occurrence_datetime", "")
        if occ and occ[:10] >= MIN_DATE:
            slots.add(occ[:14] + "00:00Z")
    return sorted(slots)


def slot_to_tag(slot_iso: str) -> str:
    """'2026-06-20T23:00:00Z' → '2026-06-20T23'"""
    return slot_iso[:13]


def already_saved(sport: str, tag: str) -> bool:
    return os.path.exists(os.path.join(OUTPUT_BASE, sport, f"{tag}.json"))


def save(sport: str, tag: str, rows: list) -> None:
    out_dir = os.path.join(OUTPUT_BASE, sport)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{tag}.json"), "w") as f:
        json.dump(rows, f, indent=2)


def pull_sport(sport: str, dry_run: bool = False) -> None:
    cfg = SPORT_CONFIG[sport]
    slots = extract_slots(os.path.join(KALSHI_DIR, cfg["kalshi_file"]))

    if not slots:
        logger.warning(f"{sport.upper()}: no game slots found")
        return

    logger.info(f"{sport.upper()}: {len(slots)} time slots to fetch")

    try:
        remaining = get_remaining_credits()
        logger.info(f"Credits remaining before {sport.upper()} fetch: {remaining}")
        if remaining < 100:
            logger.error(f"Credits below 100 ({remaining}). Skipping {sport.upper()}.")
            return
    except Exception as e:
        logger.warning(f"Could not check credits: {e}")

    done = skipped = empty = 0

    for slot_iso in slots:
        tag = slot_to_tag(slot_iso)

        if already_saved(sport, tag):
            skipped += 1
            continue

        # 4-hour window ending at game start time
        slot_dt = datetime.fromisoformat(slot_iso.replace("Z", "+00:00"))
        from_dt = slot_dt - timedelta(hours=4)
        from_iso = from_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        to_iso = slot_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        if dry_run:
            print(f"[dry-run] {sport} {tag}: GET /historical/odds?from={from_iso}&to={to_iso}")
            done += 1
            continue

        try:
            rows = get_historical_odds(cfg["sport_key"], from_iso, to_iso)
            save(sport, tag, rows)
            done += 1
            if not rows:
                empty += 1
            if done % 20 == 0:
                logger.info(f"{sport.upper()}: {done} slots fetched, {skipped} skipped, {empty} empty")
        except RuntimeError as e:
            logger.error(str(e))
            print(f"\nWARNING: Stopping {sport.upper()} — credits critical.")
            return
        except Exception as e:
            logger.warning(f"{sport.upper()} {tag}: {e}")

        time.sleep(SLEEP_BETWEEN)

    logger.info(
        f"{sport.upper()} done: {done} fetched, {skipped} already saved, {empty} empty"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull historical Vegas odds from TheOddsAPI.")
    parser.add_argument("--sport", choices=["mlb", "nba"], help="Pull one sport only")
    parser.add_argument("--dry-run", action="store_true", help="Print calls without executing")
    args = parser.parse_args()

    sports = [args.sport] if args.sport else ["mlb", "nba"]
    for sport in sports:
        pull_sport(sport, dry_run=args.dry_run)

    print("\nFetch complete.")


if __name__ == "__main__":
    main()
