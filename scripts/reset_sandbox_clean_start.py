"""
scripts/reset_sandbox_clean_start.py

One-time sandbox reset (Phase 1 Bug Fix 6 of the 2026-07-04 rebuild).

The sandbox equity curve from 2026-06-25 is contaminated by the dual-pipeline
bug and the MONITOR-relabeling bug (both fixed elsewhere in this rebuild).
Rather than trying to retroactively clean it, this archives the old
sandbox_trades table and starts fresh from 2026-07-05 with a $1,000 bankroll.

Run once, locally and then again on the VPS against its live paper_trades.db.
Safe to re-run — if sandbox_trades_pre_rebuild_bak already exists, it is left
untouched and sandbox_trades is only reset if it isn't already empty+fresh.
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "paper_trades.db")
NEW_START_DATE = "2026-07-05"
NEW_BANKROLL   = 1000.00


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        if "sandbox_trades_pre_rebuild_bak" in tables:
            print("sandbox_trades_pre_rebuild_bak already exists — skipping archive step.")
        else:
            n_archived = conn.execute("SELECT COUNT(*) FROM sandbox_trades").fetchone()[0]
            conn.execute("ALTER TABLE sandbox_trades RENAME TO sandbox_trades_pre_rebuild_bak")
            conn.commit()
            print(f"Archived {n_archived} pre-rebuild sandbox trade(s) to "
                  f"sandbox_trades_pre_rebuild_bak")

        # Fresh empty sandbox_trades table (same schema as position_manager.init_db)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sandbox_trades (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_trade_id   TEXT,
                entry_date       DATE,
                game             TEXT,
                home_team        TEXT,
                away_team        TEXT,
                kalshi_ticker    TEXT,
                signal           TEXT,
                tier             TEXT,
                gap              REAL,
                entry_price      REAL,
                pinnacle_prob    REAL,
                full_kelly       REAL,
                quarter_kelly    REAL,
                position_fraction REAL,
                shares           INTEGER,
                actual_cost      REAL,
                bankroll_before  REAL,
                start_utc        TEXT,
                exit_price       REAL,
                exit_type        TEXT,
                exit_time        DATETIME,
                resolution_price REAL,
                pnl_dollars      REAL,
                pnl_pct          REAL,
                bankroll_after   REAL,
                status           TEXT DEFAULT 'OPEN',
                created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()

        # Reset sandbox_config to the clean start date/bankroll
        conn.execute(
            "UPDATE sandbox_config SET bankroll_start = ?, start_date = ? WHERE id = 1",
            (NEW_BANKROLL, NEW_START_DATE),
        )
        if conn.execute("SELECT id FROM sandbox_config WHERE id = 1").fetchone() is None:
            conn.execute(
                "INSERT INTO sandbox_config (id, bankroll_start, start_date, created_at) "
                "VALUES (1, ?, ?, ?)",
                (NEW_BANKROLL, NEW_START_DATE, datetime.now(timezone.utc).isoformat()),
            )
        conn.commit()

        # Clear bankroll history too — it's tied to the old, contaminated equity curve
        n_hist = conn.execute("SELECT COUNT(*) FROM sandbox_bankroll_history").fetchone()[0]
        if n_hist:
            conn.execute("ALTER TABLE sandbox_bankroll_history RENAME TO sandbox_bankroll_history_pre_rebuild_bak")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sandbox_bankroll_history (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp  DATETIME,
                    bankroll   REAL,
                    event_type TEXT,
                    trade_id   INTEGER,
                    note       TEXT
                );
            """)
            conn.commit()
            print(f"Archived {n_hist} pre-rebuild bankroll history row(s).")

        cfg = dict(conn.execute("SELECT * FROM sandbox_config WHERE id = 1").fetchone())
        print(f"\nSandbox reset complete:")
        print(f"  start_date:     {cfg['start_date']}")
        print(f"  bankroll_start: ${cfg['bankroll_start']:,.2f}")
        print(f"  sandbox_trades: 0 rows (fresh)")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
