"""
backfill_skipped_tickers.py

One-time recovery script. Records written to data/skipped_trades.json before
commit 2ee092c are missing event_ticker/kalshi_ticker, so
resolve_skipped_trades() in update_outcomes.py can never resolve them.

event_ticker is recoverable from trade_id ("{snapshot_time}|{event_ticker}").
kalshi_ticker is recovered by re-opening the original snapshot file and
matching on (event_ticker, team).

Usage:
    python scripts/backfill_skipped_tickers.py
    python scripts/backfill_skipped_tickers.py --dry-run
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKIPPED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "skipped_trades.json")
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "snapshots")


def event_ticker_from_trade_id(trade_id: str) -> str:
    return trade_id.split("|", 1)[1] if "|" in trade_id else ""


def lookup_kalshi_ticker(snapshot_time: str, event_ticker: str, team: str) -> str | None:
    path = os.path.join(SNAPSHOT_DIR, f"{snapshot_time}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        snap = json.load(f)
    for row in snap.get("rows", []):
        if row.get("event_ticker") == event_ticker and row.get("team") == team:
            return row.get("kalshi_ticker")
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(SKIPPED_FILE) as f:
        skipped: list[dict] = json.load(f)

    patched = 0
    unrecoverable = 0

    for entry in skipped:
        if entry.get("kalshi_ticker"):
            continue

        event_ticker = entry.get("event_ticker") or event_ticker_from_trade_id(entry.get("trade_id", ""))
        if not event_ticker:
            unrecoverable += 1
            print(f"  [UNRECOVERABLE] no event_ticker for trade_id={entry.get('trade_id')}")
            continue

        kalshi_ticker = lookup_kalshi_ticker(entry.get("snapshot_time", ""), event_ticker, entry.get("team", ""))
        if not kalshi_ticker:
            unrecoverable += 1
            print(f"  [UNRECOVERABLE] no matching row for {event_ticker} / {entry.get('team')}")
            continue

        entry["event_ticker"] = event_ticker
        entry["kalshi_ticker"] = kalshi_ticker
        patched += 1
        print(f"  [PATCHED] {entry.get('game')} — {entry.get('team')} → {kalshi_ticker}")

    print(f"\nPatched: {patched}  Unrecoverable: {unrecoverable}  Total: {len(skipped)}")

    if args.dry_run:
        print("[dry-run] Nothing written.")
        return

    if patched:
        with open(SKIPPED_FILE, "w") as f:
            json.dump(skipped, f, indent=2)
        print(f"Saved → {SKIPPED_FILE}")


if __name__ == "__main__":
    main()
