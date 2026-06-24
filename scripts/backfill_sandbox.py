"""
scripts/backfill_sandbox.py

One-time setup script:
  1. Creates sandbox DB tables (idempotent)
  2. Inserts sandbox_config row ($1,000 starting bankroll)
  3. Opens sandbox positions for all paper trades dated 2026-06-25 or later

Run once:
    python scripts/backfill_sandbox.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.position_manager import init_db, open_sandbox_position
from execution.position_sizer import get_available_cash, DB_PATH
import sqlite3

TRADES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "paper_trades.json")


def main() -> None:
    print("─" * 60)
    print("SANDBOX BACKFILL")
    print("─" * 60)

    # 1. Init DB
    init_db()

    # 2. Load paper trades
    with open(TRADES_FILE) as f:
        trades = json.load(f)

    print(f"\nLoaded {len(trades)} paper trades.")

    # 3. Open positions for Jun 25+ trades
    opened = 0
    skipped_date = 0
    skipped_other = 0

    for trade in trades:
        start_utc = trade.get("start_utc", "")
        if not start_utc:
            skipped_other += 1
            continue

        from datetime import datetime
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        try:
            start_dt   = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
            entry_date = start_dt.astimezone(ET).date().isoformat()
        except Exception:
            skipped_other += 1
            continue

        if entry_date < "2026-06-25":
            skipped_date += 1
            continue

        result = open_sandbox_position(trade)
        if result:
            opened += 1
        else:
            skipped_other += 1

    print(f"\n{'─'*60}")
    print(f"Positions opened : {opened}")
    print(f"Skipped (pre-25) : {skipped_date}")
    print(f"Skipped (other)  : {skipped_other}")

    # 4. Show current bankroll state
    conn = sqlite3.connect(DB_PATH)
    available_cash, total_bankroll = get_available_cash(conn)
    conn.close()
    print(f"\nBankroll : ${total_bankroll:,.2f}")
    print(f"Cash     : ${available_cash:,.2f}")
    print(f"Deployed : ${total_bankroll - available_cash:,.2f}")
    print("─" * 60)


if __name__ == "__main__":
    main()
