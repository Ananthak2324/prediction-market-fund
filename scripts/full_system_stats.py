"""
scripts/full_system_stats.py

Generates a single comprehensive stats report covering every metric the
system tracks — combined across all active desks. Unlike yc_summary.py
(tier/signal/sandbox only, for external use) this dumps everything:
agent stats, portfolio metrics, gap buckets, book breakdown, cost log
totals, funnel counts, and gap-curve tracker coverage.

Usage:
    python scripts/full_system_stats.py > reports/full_system_stats.md
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.desk_loader import get_active_desks
from scripts.update_outcomes import build_summary

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fmt_pct(v) -> str:
    return f"{v * 100:.1f}%" if v is not None else "—"


def fmt_money(v) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def main() -> None:
    desks = get_active_desks()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    all_trades, all_shadow, all_skipped = [], [], []
    cost_rows = []
    funnel_by_desk: dict[str, list] = {}
    for desk in desks:
        tp = os.path.join(BASE, desk.paper_trades_path)
        if os.path.exists(tp):
            with open(tp) as f:
                all_trades.extend(json.load(f))
        sp = os.path.join(BASE, desk.shadow_trades_path)
        if os.path.exists(sp):
            with open(sp) as f:
                all_shadow.extend(json.load(f))
        skp = os.path.join(BASE, desk.skipped_trades_path)
        if os.path.exists(skp):
            with open(skp) as f:
                all_skipped.extend(json.load(f))
        cp = os.path.join(BASE, desk.agent_cost_log_path)
        if os.path.exists(cp):
            import csv
            with open(cp, newline="") as f:
                cost_rows.extend(list(csv.DictReader(f)))
        fp = os.path.join(BASE, desk.funnel_log_path)
        if os.path.exists(fp):
            with open(fp) as f:
                try:
                    funnel_by_desk[desk.desk_id] = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    funnel_by_desk[desk.desk_id] = []

    summary = build_summary(all_trades, shadow_entries=all_shadow)

    print(f"# EdgeFund — Full System Stats\n")
    print(f"**Generated:** {now} · Desks: {', '.join(d.desk_id for d in desks)}\n")
    print("---\n")

    # ── Headline ────────────────────────────────────────────────────────
    print("## Headline\n")
    print(f"| Metric | Value |")
    print(f"|---|---|")
    print(f"| Trades logged | {summary['total_logged']} |")
    print(f"| Paused (excluded, pre-rebuild contamination) | {summary['total_paused']} |")
    print(f"| Resolved | {summary['total_resolved']} |")
    print(f"| Valid (for analysis) | {summary['total_valid']} |")
    print(f"| Open | {summary['total_open']} |")
    print(f"| Win rate (overall, valid) | {fmt_pct(summary['win_rate_overall'])} |")
    print(f"| P-value (vs. 50% null) | {summary['p_value'] if summary['p_value'] is not None else '—'} |")
    print(f"| Avg gap — winners | {fmt_pct(summary['avg_gap_winners'])} |")
    print(f"| Avg gap — losers | {fmt_pct(summary['avg_gap_losers'])} |")
    print()

    # ── Tier performance ───────────────────────────────────────────────
    print("## Tier Performance\n")
    tp = summary["tier_performance"]
    print("| Tier | Status | Kelly x | Resolved | Wins | Win Rate | P-value | EV/$ |")
    print("|---|---|---|---|---|---|---|---|")
    for t in ("A", "B"):
        d = tp[t]
        print(f"| {t} | {d['status']} | {d['kelly_multiplier']:.0%} | {d['resolved']} | "
              f"{d['wins']} | {fmt_pct(d['win_rate'])} | "
              f"{d['p_value'] if d['p_value'] is not None else '—'} | "
              f"{d['ev_per_dollar'] if d['ev_per_dollar'] is not None else '—'} |")
    c = tp["C"]
    print(f"| C | {c['status']} | 0% | {c.get('shadow_resolved', 0)} (shadow) | "
          f"{c.get('shadow_wins', 0)} | {fmt_pct(c.get('shadow_win_rate'))} | — | — |")
    print()
    print(f"*Tier B validation: {tp['B']['resolved']}/20 resolved trades needed. "
          f"{tp['B']['upgrade_threshold']}; {tp['B']['downgrade_threshold']}*")
    print(f"\n*Tier C note: {c.get('note', '')}*\n")

    # ── Signal performance ──────────────────────────────────────────────
    print("## Signal Performance\n")
    sp = summary["signal_performance"]
    print("| Signal | Status | Resolved | Win Rate |")
    print("|---|---|---|---|")
    by = sp["BUY_YES"]
    print(f"| BUY_YES | {by['status']} | {by['resolved']} | {fmt_pct(by['win_rate'])} |")
    bn = sp["BUY_NO"]
    print(f"| BUY_NO | {bn['status']} | {bn.get('shadow_resolved', 0)} (shadow) | "
          f"{fmt_pct(bn.get('shadow_win_rate'))} |")
    print(f"\n*BUY_NO note: {bn.get('note', '')}*\n")

    # ── By gap bucket / by book ──────────────────────────────────────────
    print("## Win Rate by Gap Bucket\n")
    print("| Bucket | Trades | Win Rate |")
    print("|---|---|---|")
    for b, d in sorted(summary["by_gap_bucket"].items()):
        print(f"| {b.replace('_', '-')}% | {d['trades']} | {fmt_pct(d['win_rate'])} |")
    print()

    print("## Win Rate by Book\n")
    print("| Book | Trades | Win Rate |")
    print("|---|---|---|")
    for b, d in sorted(summary["by_book"].items()):
        print(f"| {b} | {d['trades']} | {fmt_pct(d['win_rate'])} |")
    print()

    # ── Clean vs suspect timing ──────────────────────────────────────────
    print("## Clean vs. Timing-Suspect Trades\n")
    for label, key in (("Clean (≤3h before game)", "clean_trades"), ("Suspect (>3h before game)", "suspect_trades")):
        d = summary[key]
        print(f"**{label}:** {d['resolved']} resolved ({d['wins']}W/{d['losses']}L), "
              f"win rate {fmt_pct(d['win_rate'])}, {d['open']} open, "
              f"p={d['p_value'] if d['p_value'] is not None else '—'}")
    print()

    # ── Agent stats ───────────────────────────────────────────────────────
    print("## Research Agent Stats\n")
    a = summary["agent_stats"]
    print(f"| Metric | Value |")
    print(f"|---|---|")
    print(f"| Total evaluated | {a.get('total_evaluated', 0)} |")
    print(f"| Trade recommendations | {a.get('trade_recommendations', 0)} |")
    print(f"| Skip recommendations | {a.get('skip_recommendations', 0)} |")
    print(f"| Skip rate | {fmt_pct(a.get('skip_rate'))} |")
    print(f"| Win rate (agent-approved) | {fmt_pct(a.get('win_rate_after_agent'))} |")
    print(f"| Win rate (unvetted / suspect) | {fmt_pct(a.get('win_rate_without_agent'))} |")
    print(f"| High confidence win rate | {fmt_pct(a.get('high_confidence_win_rate'))} |")
    print(f"| Medium confidence win rate | {fmt_pct(a.get('medium_confidence_win_rate'))} |")
    print(f"| News found rate | {fmt_pct(a.get('news_found_rate'))} |")
    print(f"| Pinnacle unstable rate | {fmt_pct(a.get('pinnacle_unstable_rate'))} |")
    print(f"| Shadow resolved (skip-decision audit) | {a.get('shadow_resolved', 0)} |")
    print(f"| Shadow win rate (what skipped trades would've done) | {fmt_pct(a.get('shadow_win_rate'))} |")
    print()

    # ── Portfolio metrics ─────────────────────────────────────────────────
    print("## Portfolio Metrics\n")
    pm = summary["portfolio_metrics"]
    print(f"| Metric | Value |")
    print(f"|---|---|")
    print(f"| Avg EV per trade | {pm.get('avg_ev_per_trade')} |")
    print(f"| Sandbox Sharpe | {pm.get('sandbox_sharpe') if pm.get('sandbox_sharpe') is not None else '—'} |")
    print(f"| Sandbox max drawdown | {fmt_pct(pm.get('sandbox_max_drawdown'))} |")
    print(f"| Sandbox total return | {fmt_pct(pm.get('sandbox_total_return_pct'))} |")
    print(f"| Sandbox closed trades | {pm.get('sandbox_closed_trades', 0)} |")
    ev_bucket = pm.get("ev_by_gap_bucket", {})
    if ev_bucket:
        print(f"\n**EV by gap bucket:**")
        for b, v in sorted(ev_bucket.items()):
            print(f"- {b.replace('_', '-')}%: {v}")
    print()

    # ── Drawdown / risk controls ───────────────────────────────────────────
    print("## Risk Controls\n")
    ds = summary["drawdown_status"]
    print(f"| Metric | Value |")
    print(f"|---|---|")
    print(f"| Current drawdown | {fmt_pct(ds.get('current_drawdown_pct'))} |")
    print(f"| Circuit breaker active | {ds.get('circuit_breaker_active')} |")
    print(f"| Max concurrent positions | {ds.get('max_concurrent_positions')} |")
    print(f"| Concurrent open now | {ds.get('concurrent_open_now')} |")
    print()

    # ── Sandbox ───────────────────────────────────────────────────────────
    print("## Sandbox\n")
    db_path = os.path.join(BASE, "data", "paper_trades.db")
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cfg = conn.execute("SELECT * FROM sandbox_config WHERE id=1").fetchone()
        if cfg:
            realized = conn.execute(
                "SELECT COALESCE(SUM(pnl_dollars),0) FROM sandbox_trades WHERE status='CLOSED'"
            ).fetchone()[0] or 0.0
            n_open = conn.execute("SELECT COUNT(*) FROM sandbox_trades WHERE status='OPEN'").fetchone()[0]
            n_closed = conn.execute("SELECT COUNT(*) FROM sandbox_trades WHERE status='CLOSED'").fetchone()[0]
            print(f"| Metric | Value |")
            print(f"|---|---|")
            print(f"| Start date | {cfg['start_date']} |")
            print(f"| Starting bankroll | {fmt_money(cfg['bankroll_start'])} |")
            print(f"| Current bankroll | {fmt_money(cfg['bankroll_start'] + realized)} |")
            print(f"| Realized P&L | {fmt_money(realized)} |")
            print(f"| Open positions | {n_open} |")
            print(f"| Closed positions | {n_closed} |")
        conn.close()
    else:
        print("No sandbox DB found.")
    print()

    # ── Cost log ──────────────────────────────────────────────────────────
    print("## Agent Cost\n")
    total_cost = sum(float(r.get("estimated_cost_usd", 0) or 0) for r in cost_rows)
    total_calls = len(cost_rows)
    print(f"| Metric | Value |")
    print(f"|---|---|")
    print(f"| Total Anthropic calls logged | {total_calls} |")
    print(f"| Total estimated cost | {fmt_money(total_cost)} |")
    print(f"| Avg cost per call | {fmt_money(total_cost / total_calls) if total_calls else '—'} |")
    print()

    # ── Funnel ────────────────────────────────────────────────────────────
    print("## Edge Discovery Funnel (most recent cycle per desk)\n")
    for desk_id, entries in funnel_by_desk.items():
        if not entries:
            print(f"**{desk_id}:** no funnel data yet")
            continue
        last = entries[-1]
        print(f"**{desk_id}** (as of {last.get('timestamp', '—')}):")
        for k, v in last.items():
            if k == "timestamp":
                continue
            print(f"- {k}: {v}")
        print()

    # ── Skipped trades ────────────────────────────────────────────────────
    print("## Skipped Trades\n")
    print(f"Total logged: {len(all_skipped)}\n")

    # ── Shadow trades ─────────────────────────────────────────────────────
    print("## Shadow Trades\n")
    print(f"Total logged: {len(all_shadow)}")
    shadow_resolved = [s for s in all_shadow if s.get("shadow_outcome") in ("WIN", "LOSS")]
    print(f"Resolved: {len(shadow_resolved)}")
    if shadow_resolved:
        wins = sum(1 for s in shadow_resolved if s["shadow_outcome"] == "WIN")
        print(f"Win rate: {fmt_pct(wins / len(shadow_resolved))}")
    print()


if __name__ == "__main__":
    main()
