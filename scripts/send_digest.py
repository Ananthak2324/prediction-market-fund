"""
send_digest.py

Sends a brief nightly iMessage summary of trading activity.
Run once daily at 2 AM via its own LaunchAgent (independent of the
15-min outcomes cycle so it fires exactly once per day).

Usage:
    python scripts/send_digest.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from core.notifications import send_imessage
from execution.position_manager import get_db, get_available_cash

DATA_DIR        = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PAPER_TRADES    = os.path.join(DATA_DIR, "paper_trades.json")
PERF_SUMMARY    = os.path.join(DATA_DIR, "performance_summary.json")
DIGEST_STATE    = os.path.join(DATA_DIR, "last_digest_state.json")

ET = ZoneInfo("America/New_York")


def _et_date(iso_or_snapshot: str) -> str:
    """Convert a UTC ISO timestamp or 'YYYY-MM-DD_HHMM' snapshot string to an ET date string."""
    if "T" in iso_or_snapshot:
        dt = datetime.fromisoformat(iso_or_snapshot.replace("Z", "+00:00"))
    else:
        dt = datetime.strptime(iso_or_snapshot, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).date().isoformat()


def today_activity(trades: list[dict], today: str) -> tuple[int, int, int, int]:
    """Returns (logged_today, wins_today, losses_today, open_today)."""
    logged = [t for t in trades if _et_date(t["snapshot_time"]) == today]
    wins   = sum(1 for t in logged if t.get("outcome") == "WIN")
    losses = sum(1 for t in logged if t.get("outcome") == "LOSS")
    open_  = sum(1 for t in logged if t.get("outcome") is None)
    return len(logged), wins, losses, open_


def format_digest(today: str, logged: int, wins: int, losses: int, open_: int,
                   perf: dict, bankroll: float) -> str:
    date_label = datetime.strptime(today, "%Y-%m-%d").strftime("%b %-d")
    win_rate   = perf.get("win_rate_overall")
    valid      = perf.get("total_valid", 0)
    valid_wins = perf.get("total_valid_wins", 0)
    ev         = perf.get("portfolio_metrics", {}).get("avg_ev_per_trade")
    ret_pct    = perf.get("portfolio_metrics", {}).get("sandbox_total_return_pct", 0.0)

    win_rate_str = f"{win_rate:.1%} ({valid_wins}/{valid})" if win_rate is not None else "n/a"
    ev_str       = f"+${ev:.2f}" if ev is not None else "n/a"

    return (
        f"\U0001F4CA DAILY SUMMARY — {date_label}\n"
        f"Today: {logged} trades logged ({wins}W / {losses}L / {open_} open)\n"
        f"All-time: {win_rate_str} win rate  |  EV/trade {ev_str}\n"
        f"Sandbox bankroll: ${bankroll:,.2f} ({ret_pct:+.1%} all-time)"
    )


def main() -> None:
    today = datetime.now(ET).date().isoformat()

    trades = []
    if os.path.exists(PAPER_TRADES):
        with open(PAPER_TRADES) as f:
            trades = json.load(f)

    perf = {}
    if os.path.exists(PERF_SUMMARY):
        with open(PERF_SUMMARY) as f:
            perf = json.load(f)

    logged, wins, losses, open_ = today_activity(trades, today)

    conn = get_db()
    try:
        _, bankroll = get_available_cash(conn)
    finally:
        conn.close()

    message = format_digest(today, logged, wins, losses, open_, perf, bankroll)
    print(message)

    sent = send_imessage(message)
    print(f"  [DIGEST] sent={sent}")

    state = {
        "last_sent_date": today,
        "total_logged":   perf.get("total_logged", 0),
        "total_valid":    perf.get("total_valid", 0),
        "total_valid_wins": perf.get("total_valid_wins", 0),
        "bankroll":       bankroll,
    }
    with open(DIGEST_STATE, "w") as f:
        json.dump(state, f, indent=2)


if __name__ == "__main__":
    main()
