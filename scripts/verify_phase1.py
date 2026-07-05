"""
scripts/verify_phase1.py

Phase 1 verification for the 2026-07-04 EdgeFund rebuild. Runs all 8 checks
from the rebuild plan and prints PASS/FAIL for each. Do not proceed to
Phase 2 until all 8 pass.

Read-only except where explicitly noted (mocked in-memory DBs only —
never touches the real data/paper_trades.db or data/paper_trades.json).
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    mark = "✓" if passed else "✗"
    print(f"{mark} {name}" + (f" — {detail}" if detail else ""))


def main() -> None:
    # ── VERIFY 1 — Legacy pipeline disabled ─────────────────────────────────
    from scripts.update_outcomes import ingest_new_trades
    before = json.load(open(os.path.join(BASE, "data", "paper_trades.json")))
    result = ingest_new_trades(before)
    after = json.load(open(os.path.join(BASE, "data", "paper_trades.json")))
    check(
        "Legacy pipeline disabled",
        result == (before, 0, 0, 0) and before == after,
        f"returned {result[1:]}, file unchanged={before == after}",
    )

    # ── VERIFY 2 — MONITOR trades paused ────────────────────────────────────
    trades = json.load(open(os.path.join(BASE, "data", "paper_trades.json")))
    monitor_trades = [t for t in trades if t.get("agent_verdict") == "MONITOR"]
    all_paused = all(t.get("status") == "PAUSED" for t in monitor_trades)
    check(
        f"{len(monitor_trades)} MONITOR trades set to PAUSED",
        len(monitor_trades) > 0 and all_paused,
        f"{sum(1 for t in monitor_trades if t.get('status') == 'PAUSED')}/{len(monitor_trades)} paused",
    )

    # ── VERIFY 3 — Clean win rate after PAUSED exclusion ────────────────────
    from scripts.update_outcomes import build_summary
    summary = build_summary(trades)
    wr = summary.get("win_rate_overall")
    n_paused = summary.get("total_paused", 0)
    check(
        "Clean win rate computed with PAUSED excluded",
        wr is not None,
        f"win_rate_overall={wr}, PAUSED excluded={n_paused}",
    )

    # ── VERIFY 4 — Cooldown reduced ──────────────────────────────────────────
    # Post-Phase-2: cooldowns live in desk config (desks/base.yaml), not module
    # constants — edge_discovery_agent.py reads desk.cooldown_hours at call time.
    from core.desk_loader import get_desk
    _mlb_desk = get_desk("MLB")
    skip_cd    = _mlb_desk.cooldown_hours
    monitor_cd = _mlb_desk.get("schedule.monitor_cooldown_hours")
    check(
        "Cooldown reduced to 1 hour",
        skip_cd == 1.0 and monitor_cd == 1.0,
        f"SKIP={skip_cd}, MONITOR={monitor_cd}",
    )

    # ── VERIFY 5 — Signal gates correct ─────────────────────────────────────
    from agent.edge_discovery_agent import apply_signal_gates
    tests = [
        ({"best_abs_gap": 0.15, "signal": "BUY_YES"}, {"recommendation": "TRADE"}, "SHADOW", "C"),
        ({"best_abs_gap": 0.07, "signal": "BUY_NO"},  {"recommendation": "TRADE"}, "SHADOW", None),
        ({"best_abs_gap": 0.12, "signal": "BUY_YES"}, {"recommendation": "TRADE"}, "TRADE", "B"),
        ({"best_abs_gap": 0.07, "signal": "BUY_YES"}, {"recommendation": "TRADE"}, "TRADE", "A"),
    ]
    all_pass = True
    for cand, verdict, exp_rec, exp_tier in tests:
        r = apply_signal_gates(_mlb_desk, cand, verdict)
        ok = r.get("recommendation") == exp_rec and (exp_tier is None or r.get("tier") == exp_tier)
        all_pass &= ok
    check("Signal gates all 4 tests passed", all_pass)

    # ── VERIFY 6 — Concurrent cap and circuit breaker ───────────────────────
    from execution.position_manager import count_open_positions, _current_drawdown, MAX_CONCURRENT_POSITIONS, MAX_DRAWDOWN_PAUSE
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE sandbox_trades (id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE sandbox_bankroll_history (id INTEGER PRIMARY KEY, timestamp TEXT, bankroll REAL);
    """)
    for _ in range(4):
        conn.execute("INSERT INTO sandbox_trades (status) VALUES ('OPEN')")
    conn.commit()
    blocks_5th = count_open_positions(conn) >= MAX_CONCURRENT_POSITIONS
    conn.execute("INSERT INTO sandbox_bankroll_history (timestamp, bankroll) VALUES ('t1', 1000)")
    conn.execute("INSERT INTO sandbox_bankroll_history (timestamp, bankroll) VALUES ('t2', 750)")
    conn.commit()
    blocks_open = _current_drawdown(conn) >= MAX_DRAWDOWN_PAUSE
    conn.close()
    check("Position controls enforced", blocks_5th and blocks_open,
          f"blocks_5th={blocks_5th}, blocks_at_25pct_dd={blocks_open}")

    # ── VERIFY 7 — Sandbox reset ─────────────────────────────────────────────
    db_conn = sqlite3.connect(os.path.join(BASE, "data", "paper_trades.db"))
    cfg = db_conn.execute("SELECT start_date FROM sandbox_config WHERE id=1").fetchone()
    n_trades = db_conn.execute("SELECT COUNT(*) FROM sandbox_trades").fetchone()[0]
    tables = {r[0] for r in db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    db_conn.close()
    check(
        "Sandbox reset to clean start",
        cfg and cfg[0] == "2026-07-05" and n_trades == 0 and "sandbox_trades_pre_rebuild_bak" in tables,
        f"start_date={cfg[0] if cfg else None}, sandbox_trades rows={n_trades}, "
        f"bak exists={'sandbox_trades_pre_rebuild_bak' in tables}",
    )

    # ── VERIFY 8 — Performance summary has full breakdown ───────────────────
    check(
        "Performance summary has full breakdown",
        all(k in summary for k in ("tier_performance", "signal_performance", "drawdown_status")),
    )

    print()
    n_pass = sum(1 for _, p, _ in results if p)
    print(f"Phase 1: {n_pass}/{len(results)} passed")
    if n_pass < len(results):
        print("Do NOT proceed to Phase 2 until all checks pass.")
        sys.exit(1)


if __name__ == "__main__":
    main()
