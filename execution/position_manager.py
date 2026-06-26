"""
execution/position_manager.py

Manages sandbox portfolio positions: open, poll, and settle.

DB: data/paper_trades.db  (three tables added by init_db())

Entry prices are always the contract we're buying:
  BUY_YES → entry_price = k_prob,     pinnacle_prob = v_prob
  BUY_NO  → entry_price = 1 - k_prob, pinnacle_prob = 1 - v_prob

Run as a standalone process for the live poll loop:
  python execution/position_manager.py
"""
import os
import sys
import time
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from dotenv import load_dotenv

load_dotenv()

from execution.position_sizer import calculate_position, get_available_cash

DB_PATH      = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "paper_trades.db")
KALSHI_BASE  = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
ET           = ZoneInfo("America/New_York")
SANDBOX_START_DATE = "2026-06-25"


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create sandbox tables and insert the config row if not present."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sandbox_config (
                id               INTEGER PRIMARY KEY DEFAULT 1,
                bankroll_start   REAL    DEFAULT 1000.00,
                start_date       DATE    DEFAULT '2026-06-25',
                created_at       DATETIME
            );

            CREATE TABLE IF NOT EXISTS sandbox_trades (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_trade_id   TEXT,
                entry_date       DATE,
                game             TEXT,
                home_team        TEXT,
                away_team        TEXT,
                kalshi_ticker    TEXT,
                signal           TEXT,
                tier             INTEGER,
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

            CREATE TABLE IF NOT EXISTS sandbox_bankroll_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  DATETIME,
                bankroll   REAL,
                event_type TEXT,
                trade_id   INTEGER,
                note       TEXT
            );
        """)

        # Insert config row only if absent
        exists = conn.execute("SELECT id FROM sandbox_config WHERE id = 1").fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO sandbox_config (id, bankroll_start, start_date, created_at) VALUES (1, 1000.00, ?, ?)",
                (SANDBOX_START_DATE, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            print(f"[SANDBOX] Initialized — $1,000.00 bankroll starting {SANDBOX_START_DATE}")
        else:
            conn.commit()
    finally:
        conn.close()


# ── Kalshi price fetch ────────────────────────────────────────────────────────

def _fetch_kalshi_yes_mid(kalshi_ticker: str) -> float | None:
    """Return current YES mid-price (0-1) for a Kalshi market, or None on error."""
    try:
        resp = requests.get(f"{KALSHI_BASE}/markets/{kalshi_ticker}", timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        m   = resp.json().get("market", {})
        bid = float(m.get("yes_bid_dollars") or 0)
        ask = float(m.get("yes_ask_dollars") or 1)
        if bid == 0 and ask >= 0.99:
            return None
        return round((bid + ask) / 2.0, 4)
    except Exception:
        return None


# ── Open position ─────────────────────────────────────────────────────────────

def open_sandbox_position(paper_trade: dict) -> bool:
    """
    Open a sandbox position for a newly ingested paper trade.
    Returns True if a position was opened, False if skipped.

    Skipped when:
      - entry_date < 2026-06-25
      - shares == 0 (position too small)
      - actual_cost > available_cash
      - paper_trade_id already has an open position
    """
    # Compute entry_date in ET from start_utc
    start_utc_str = paper_trade.get("start_utc", "")
    try:
        start_dt  = datetime.fromisoformat(start_utc_str.replace("Z", "+00:00"))
        entry_date = start_dt.astimezone(ET).date().isoformat()
    except (ValueError, AttributeError):
        return False

    if entry_date < SANDBOX_START_DATE:
        return False

    trade_id = paper_trade.get("trade_id", "")
    signal   = paper_trade.get("signal", "")
    k_prob   = paper_trade.get("k_prob")
    v_prob   = paper_trade.get("v_prob")

    if k_prob is None or v_prob is None or not signal:
        return False

    # Direction-aware pricing
    if signal == "BUY_YES":
        entry_price   = round(k_prob, 4)
        pinnacle_prob = round(v_prob, 4)
    elif signal == "BUY_NO":
        entry_price   = round(1.0 - k_prob, 4)
        pinnacle_prob = round(1.0 - v_prob, 4)
    else:
        return False

    if entry_price <= 0 or pinnacle_prob <= 0:
        return False

    game      = paper_trade.get("game", "")
    parts     = game.split(" @ ")
    away_team = parts[0].strip() if len(parts) == 2 else ""
    home_team = parts[1].strip() if len(parts) == 2 else ""
    abs_gap   = paper_trade.get("abs_gap", 0) or abs(paper_trade.get("gap", 0))
    tier      = 1 if abs_gap >= 0.10 else 2

    conn = get_db()
    try:
        # Idempotency guard
        if conn.execute(
            "SELECT id FROM sandbox_trades WHERE paper_trade_id = ? AND status = 'OPEN'",
            (trade_id,)
        ).fetchone():
            return False

        available_cash, total_bankroll = get_available_cash(conn)
        sizing = calculate_position(total_bankroll, entry_price, pinnacle_prob)

        if sizing["shares"] == 0:
            print(f"[SANDBOX SKIP] {game} — position too small (0 shares)")
            return False
        if sizing["actual_cost"] > available_cash:
            print(f"[SANDBOX SKIP] {game} — insufficient cash "
                  f"(need ${sizing['actual_cost']:.2f}, have ${available_cash:.2f})")
            return False

        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            """INSERT INTO sandbox_trades
               (paper_trade_id, entry_date, game, home_team, away_team, kalshi_ticker,
                signal, tier, gap, entry_price, pinnacle_prob,
                full_kelly, quarter_kelly, position_fraction,
                shares, actual_cost, bankroll_before, start_utc, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_id, entry_date, game, home_team, away_team,
             paper_trade.get("kalshi_ticker", ""),
             signal, tier, paper_trade.get("gap", 0),
             entry_price, pinnacle_prob,
             sizing["full_kelly"], sizing["quarter_kelly"], sizing["position_fraction"],
             sizing["shares"], sizing["actual_cost"],
             available_cash, start_utc_str, "OPEN", now),
        )
        sandbox_id = cur.lastrowid

        post_open_cash = available_cash - sizing["actual_cost"]
        conn.execute(
            "INSERT INTO sandbox_bankroll_history (timestamp, bankroll, event_type, trade_id, note) VALUES (?,?,?,?,?)",
            (now, round(post_open_cash, 2), "TRADE_OPEN", sandbox_id,
             f"{signal} {game} — {sizing['shares']} shares @ ${entry_price:.3f}"),
        )
        conn.commit()

        print(
            f"[SANDBOX OPEN] {game}\n"
            f"  Signal: {signal}  Tier {tier}  entry=${entry_price:.3f}  "
            f"pinnacle={pinnacle_prob:.1%}  gap={abs_gap:.1%}\n"
            f"  Kelly: full={sizing['full_kelly']:.1%}  "
            f"quarter={sizing['quarter_kelly']:.1%}  "
            f"fraction={sizing['position_fraction']:.1%}\n"
            f"  {sizing['shares']} shares × ${entry_price:.3f} = ${sizing['actual_cost']:.2f}  "
            f"cash remaining: ${post_open_cash:.2f}"
        )
        return True
    finally:
        conn.close()


# ── Exit helpers ──────────────────────────────────────────────────────────────

def _close_position(conn: sqlite3.Connection, row: sqlite3.Row,
                    current_price: float, exit_type: str) -> None:
    """Write exit fields, update bankroll history, and print the exit event."""
    entry_price = row["entry_price"]
    shares      = row["shares"]
    actual_cost = row["actual_cost"]

    pnl_dollars = round((current_price - entry_price) * shares, 2)
    pnl_pct     = round((current_price - entry_price) / entry_price, 4) if entry_price else 0.0

    available_cash, total_bankroll = get_available_cash(conn)
    bankroll_after = round(total_bankroll + pnl_dollars, 2)

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE sandbox_trades
           SET exit_price=?, exit_type=?, exit_time=?,
               pnl_dollars=?, pnl_pct=?, bankroll_after=?, status='CLOSED'
           WHERE id=?""",
        (current_price, exit_type, now, pnl_dollars, pnl_pct, bankroll_after, row["id"]),
    )
    conn.execute(
        "INSERT INTO sandbox_bankroll_history (timestamp, bankroll, event_type, trade_id, note) VALUES (?,?,?,?,?)",
        (now, bankroll_after, "TRADE_CLOSE", row["id"],
         f"{exit_type} — {row['game']} P&L ${pnl_dollars:+.2f}"),
    )
    pnl_sign = "+" if pnl_dollars >= 0 else ""
    print(
        f"[SANDBOX EXIT] {row['game']} — {exit_type}\n"
        f"  Bought: {shares} shares @ ${entry_price:.3f}  |  Exit: ${current_price:.3f}\n"
        f"  P&L: {pnl_sign}${pnl_dollars:.2f} ({pnl_pct * 100:+.1f}%)  |  Bankroll: ${bankroll_after:,.2f}"
    )


# ── Poll open positions ───────────────────────────────────────────────────────

def poll_open_positions() -> int:
    """
    Check all OPEN sandbox positions against current Kalshi prices.
    Applies exit rules in priority order. Returns number of positions closed.
    """
    conn = get_db()
    closed = 0
    try:
        rows = conn.execute(
            "SELECT * FROM sandbox_trades WHERE status = 'OPEN'"
        ).fetchall()

        if not rows:
            return 0

        now = datetime.now(timezone.utc)

        for row in rows:
            signal       = row["signal"]
            yes_mid      = _fetch_kalshi_yes_mid(row["kalshi_ticker"])
            if yes_mid is None:
                continue

            current_price = yes_mid if signal == "BUY_YES" else round(1.0 - yes_mid, 4)
            entry_price   = row["entry_price"]
            pinnacle_prob = row["pinnacle_prob"]

            pnl_pct = (current_price - entry_price) / entry_price if entry_price else 0.0

            # Hours to estimated game end (avg 3h game)
            hours_to_game_end = None
            try:
                start_dt = datetime.fromisoformat(row["start_utc"].replace("Z", "+00:00"))
                estimated_end   = start_dt + timedelta(hours=3)
                hours_to_game_end = (estimated_end - now).total_seconds() / 3600
            except Exception:
                pass

            # Apply exit rules in priority order
            exit_type = None
            if current_price >= pinnacle_prob:
                exit_type = "FAIR_VALUE"
            elif pnl_pct <= -0.40:
                exit_type = "STOP_LOSS"
            elif pnl_pct >= 0.80:
                exit_type = "PROFIT_TARGET"
            elif hours_to_game_end is not None and hours_to_game_end < 2 and pnl_pct > 0.10:
                exit_type = "NEAR_RESOLUTION"

            if exit_type:
                _close_position(conn, row, current_price, exit_type)
                conn.commit()
                closed += 1

    finally:
        conn.close()
    return closed


# ── Settle resolved positions ─────────────────────────────────────────────────

def settle_resolved_positions(paper_trades: list[dict]) -> int:
    """
    Called by update_outcomes.py after resolution.
    Closes any OPEN sandbox positions whose paper trade has resolved.
    Returns number settled.
    """
    resolved_map = {
        t["trade_id"]: t
        for t in paper_trades
        if t.get("outcome") in ("WIN", "LOSS")
    }
    if not resolved_map:
        return 0

    conn = get_db()
    settled = 0
    try:
        rows = conn.execute(
            "SELECT * FROM sandbox_trades WHERE status = 'OPEN'"
        ).fetchall()

        for row in rows:
            pt = resolved_map.get(row["paper_trade_id"])
            if not pt:
                continue

            resolution_price = 1.0 if pt["outcome"] == "WIN" else 0.0
            _close_position(conn, row, resolution_price, "RESOLUTION")

            # Also record resolution_price on the sandbox_trade row
            conn.execute(
                "UPDATE sandbox_trades SET resolution_price = ? WHERE id = ?",
                (resolution_price, row["id"]),
            )
            conn.commit()
            settled += 1

    finally:
        conn.close()

    if settled:
        print(f"[SANDBOX] Settled {settled} position(s) at resolution.")
    return settled


# ── Standalone poll loop ──────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("[SANDBOX] Poll loop started — checking every 60s during 12PM–11PM ET")
    last_outside_hour = None
    while True:
        now_et = datetime.now(ET)
        if 12 <= now_et.hour < 23:
            last_outside_hour = None
            n = poll_open_positions()
            if n:
                print(f"[SANDBOX] Closed {n} position(s) this cycle.")
        else:
            hour_key = now_et.strftime('%Y-%m-%d-%H')
            if last_outside_hour != hour_key:
                print(f"[SANDBOX] Outside game hours ({now_et.strftime('%H:%M ET')}) — sleeping.")
                last_outside_hour = hour_key
        time.sleep(60)
