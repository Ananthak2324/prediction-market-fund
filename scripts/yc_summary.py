"""
scripts/yc_summary.py

Prints the clean, post-rebuild baseline dataset for external use (e.g. a YC
application) — sourced exclusively from the single-pipeline, desk-namespaced
data produced after the 2026-07-04 rebuild. Explicitly excludes the legacy
ingestion pipeline (disabled) and PAUSED (MONITOR-bug-contaminated) trades.

Usage:
    python scripts/yc_summary.py
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.desk_loader import get_active_desks

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fmt_pct(v) -> str:
    return f"{v * 100:.1f}%" if v is not None else "—"


def main() -> None:
    desks = get_active_desks()

    print("=" * 60)
    print("  EDGEFUND — CLEAN BASELINE DATASET (post 2026-07-04 rebuild)")
    print("=" * 60)
    print()
    print(f"Data source: {', '.join(os.path.join('data', d.desk_id.lower(), 'paper_trades.json') for d in desks)}")
    print("Pipeline: edge_discovery_agent only")
    print("Legacy pipeline trades: excluded (ingest_new_trades() disabled)")

    total_trades = 0
    total_paused = 0
    total_resolved = 0
    total_wins = 0
    tier_agg: dict[str, dict] = {}
    signal_agg: dict[str, dict] = {}

    for desk in desks:
        trades_path = os.path.join(BASE, desk.paper_trades_path)
        if not os.path.exists(trades_path):
            continue
        with open(trades_path) as f:
            trades = json.load(f)

        total_trades += len(trades)
        paused = [t for t in trades if t.get("status") == "PAUSED"]
        total_paused += len(paused)

        summary_path = os.path.join(BASE, desk.performance_summary_path)
        if os.path.exists(summary_path):
            with open(summary_path) as f:
                summary = json.load(f)
            total_resolved += summary.get("total_resolved", 0)
            total_wins += summary.get("total_valid_wins", 0)

            for tier, data in summary.get("tier_performance", {}).items():
                agg = tier_agg.setdefault(tier, {"resolved": 0, "wins": 0, "shadow_resolved": 0,
                                                   "shadow_wins": 0, "status": data.get("status"),
                                                   "kelly_multiplier": data.get("kelly_multiplier")})
                agg["resolved"] += data.get("resolved", 0)
                agg["wins"] += data.get("wins", 0)
                agg["shadow_resolved"] += data.get("shadow_resolved", 0)
                agg["shadow_wins"] += data.get("shadow_wins", 0)

            for sig, data in summary.get("signal_performance", {}).items():
                agg = signal_agg.setdefault(sig, {"resolved": 0, "wins": 0, "shadow_resolved": 0,
                                                    "shadow_wins": 0, "status": data.get("status")})
                agg["resolved"] += data.get("resolved", 0)
                if data.get("win_rate") is not None:
                    agg["wins"] += round(data["win_rate"] * data.get("resolved", 0))
                agg["shadow_resolved"] += data.get("shadow_resolved", 0)
                agg["shadow_wins"] += data.get("shadow_wins", 0)

    print(f"Contaminated trades excluded: {total_paused} (PAUSED)")
    print()

    n_clean = total_trades - total_paused
    if n_clean < 10:
        print(f"⚠ Only {n_clean} clean trade(s) logged so far — this baseline will "
              f"firm up as more resolve. Numbers below are directional, not final.")
        print()

    print("TIER BREAKDOWN:")
    for tier in ("A", "B", "C"):
        d = tier_agg.get(tier, {})
        if tier in ("A", "B"):
            wr = d["wins"] / d["resolved"] if d.get("resolved") else None
            label = f"Tier {tier} ({d.get('status','?')}, {d.get('kelly_multiplier',0):.0%}x Kelly)"
            print(f"  {label}:")
            print(f"    Resolved: {d.get('resolved',0)} | Win rate: {fmt_pct(wr)}")
            if tier == "B":
                print(f"    Upgrade threshold: 20 resolved trades at win rate > 55%")
        else:
            swr = d["shadow_wins"] / d["shadow_resolved"] if d.get("shadow_resolved") else None
            print(f"  Tier C ({d.get('status','SHADOW_ONLY')}):")
            print(f"    Shadow resolved: {d.get('shadow_resolved',0)} | Shadow win rate: {fmt_pct(swr)}")
    print()

    bn = signal_agg.get("BUY_NO", {})
    bn_swr = bn["shadow_wins"] / bn["shadow_resolved"] if bn.get("shadow_resolved") else None
    print(f"  BUY_NO ({bn.get('status','SHADOW_ONLY')}):")
    print(f"    Shadow resolved: {bn.get('shadow_resolved',0)} | Shadow win rate: {fmt_pct(bn_swr)}")
    print()

    # ── Sandbox ──
    db_path = os.path.join(BASE, "data", "paper_trades.db")
    print("SANDBOX:")
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cfg = conn.execute("SELECT * FROM sandbox_config WHERE id=1").fetchone()
        if cfg:
            realized = conn.execute(
                "SELECT COALESCE(SUM(pnl_dollars),0) FROM sandbox_trades WHERE status='CLOSED'"
            ).fetchone()[0] or 0.0
            current_bankroll = cfg["bankroll_start"] + realized
            print(f"  Start date: {cfg['start_date']}")
            print(f"  Current bankroll: ${current_bankroll:,.2f}")

        from execution.position_manager import count_open_positions, _current_drawdown, MAX_CONCURRENT_POSITIONS, MAX_DRAWDOWN_PAUSE
        open_count = count_open_positions(conn)
        drawdown = _current_drawdown(conn)
        print(f"  Max concurrent: {MAX_CONCURRENT_POSITIONS} (currently {open_count} open)")
        print(f"  Circuit breaker: {'ACTIVE' if drawdown >= MAX_DRAWDOWN_PAUSE else 'inactive'} "
              f"(current drawdown {fmt_pct(drawdown)})")
        conn.close()
    else:
        print("  No sandbox DB found.")
    print()

    print("Note:")
    print('  "Clean data collection began 2026-07-05.')
    print('   Prior contaminated data archived in data/*.bak.')
    print('   This is the YC application dataset."')


if __name__ == "__main__":
    main()
