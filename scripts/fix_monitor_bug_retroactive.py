"""
scripts/fix_monitor_bug_retroactive.py

One-time retroactive fix (Phase 1 Bug Fix 2 of the 2026-07-04 rebuild).

Finds every trade in data/paper_trades.json where agent_verdict == "MONITOR"
(these were never supposed to be live trades — see the MONITOR-relabeling bug
in update_outcomes.py's cleaner-snapshot re-evaluation path, now fixed) and
sets status="PAUSED" so they're excluded from all win-rate/EV calculations
going forward while remaining visible in the raw Trade Log for audit.

Does not touch outcome/correct/resolution_price/resolved_at — those are
preserved exactly as they were.

Run once, locally and then again on the VPS against its live paper_trades.json.
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES_FILE  = os.path.join(BASE, "data", "paper_trades.json")


def main() -> None:
    with open(TRADES_FILE) as f:
        trades = json.load(f)

    fixed = 0
    for t in trades:
        if t.get("agent_verdict") == "MONITOR" and t.get("status") != "PAUSED":
            t["status"] = "PAUSED"
            t["paused_reason"] = "MONITOR_BUG_RETROACTIVE_2026-07-04"
            fixed += 1
            print(f"  [PAUSED] {t.get('game')} — {t.get('team')} "
                  f"(trade_id={t.get('trade_id')})")

    with open(TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2)

    print(f"\nFixed {fixed} MONITOR-contaminated trade(s). "
          f"Written to {TRADES_FILE}")


if __name__ == "__main__":
    main()
