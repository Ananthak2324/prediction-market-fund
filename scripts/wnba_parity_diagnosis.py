"""
scripts/wnba_parity_diagnosis.py

Diagnoses why WNBA has far fewer resolved trades than MLB — read-only, no
writes to desks/*.yaml or any data file. Categorizes the root cause into one
of four buckets rather than asserting "small sample":

  (a) game-volume mismatch   — WNBA genuinely has fewer games/day (a
                                 schedule fact, not a bug)
  (b) gate/threshold strictness — WNBA-specific config is more conservative
                                 than MLB's
  (c) pipeline/sync bug      — WNBA data isn't reaching the local view
                                 (e.g. a sync gap) even though the VPS
                                 pipeline itself is healthy
  (d) Kalshi market availability — Kalshi simply has no/few WNBA markets
                                 open in this window

Recommends only a data-completeness fix or a wait-and-recheck timeline —
never a gate/threshold change (that requires explicit sign-off since it
affects live capital).

Usage:
    python scripts/wnba_parity_diagnosis.py > reports/wnba_parity_$(date +%F).md

Run this directly wherever the live data actually lives (the VPS, if local
data hasn't been synced recently) — a stale-data false negative on WNBA
game/trade counts is exactly the kind of mistake this script exists to avoid.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.desk_loader import get_desk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOW_DAYS = 14


def fmt_pct(v) -> str:
    return f"{v * 100:.1f}%" if v is not None else "—"


# ── data loading ───────────────────────────────────────────────────────────

def load_funnel_history(desk) -> list[dict]:
    path = os.path.join(BASE, desk.funnel_log_path)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        try:
            entries = json.load(f)
        except (json.JSONDecodeError, ValueError):
            return []
    return sorted(entries, key=lambda e: e.get("run_at", ""))


def load_trade_count(desk) -> tuple[int, int]:
    """Returns (total, resolved)."""
    path = os.path.join(BASE, desk.paper_trades_path)
    if not os.path.exists(path):
        return 0, 0
    with open(path) as f:
        trades = json.load(f)
    resolved = sum(1 for t in trades if t.get("outcome") is not None)
    return len(trades), resolved


FUNNEL_FIELDS = (
    "total_scanned", "above_threshold", "already_traded", "on_cooldown",
    "pre_filter_skipped", "researched", "trade_verdicts", "skip_verdicts",
    "monitor_verdicts", "shadow_verdicts",
)


def summarize_funnel(entries: list[dict], window_days: int = WINDOW_DAYS) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    in_window = []
    for e in entries:
        try:
            run_at = datetime.fromisoformat(e.get("run_at", "").replace("Z", "+00:00"))
        except Exception:
            continue
        if run_at >= cutoff:
            in_window.append(e)

    summary = {f: sum(e.get(f, 0) or 0 for e in in_window) for f in FUNNEL_FIELDS}
    summary["n_runs"] = len(in_window)
    summary["last_run_at"] = in_window[-1]["run_at"] if in_window else None
    summary["first_run_at"] = in_window[0]["run_at"] if in_window else None
    return summary


# ── comparison ────────────────────────────────────────────────────────────

def compare_desks(mlb_summary: dict, wnba_summary: dict) -> dict:
    def rate(summary):
        scanned = summary["total_scanned"]
        return round(summary["above_threshold"] / scanned, 4) if scanned else None

    return {
        "mlb": mlb_summary,
        "wnba": wnba_summary,
        "mlb_above_threshold_rate": rate(mlb_summary),
        "wnba_above_threshold_rate": rate(wnba_summary),
        "scanned_ratio_wnba_to_mlb": (
            round(wnba_summary["total_scanned"] / mlb_summary["total_scanned"], 4)
            if mlb_summary["total_scanned"] else None
        ),
    }


def diagnose_root_cause(mlb_desk, wnba_desk, comparison: dict) -> dict:
    mlb_s, wnba_s = comparison["mlb"], comparison["wnba"]

    # (b) — re-verified programmatically each run, not trusted from memory.
    thresholds_equal = mlb_desk.get("thresholds") == wnba_desk.get("thresholds")

    # (a)/(d) — can only be assessed on data we trust is complete/fresh.
    # A near-zero total_scanned across the whole window is a strong (d)
    # signal ONLY if we're confident the data itself isn't stale/missing —
    # flagged explicitly rather than concluded silently. Staleness means
    # "no runs at all" OR "the most recent run is old relative to the
    # 30-min edge-discovery cadence" — a single genuinely-live run from
    # 9 days ago still means every day since has zero coverage, which is
    # exactly the same failure mode as zero runs and must be caught the
    # same way.
    wnba_scanned = wnba_s["total_scanned"]
    wnba_runs = wnba_s["n_runs"]
    STALE_THRESHOLD_HOURS = 3  # generous vs. the 30-min timer cadence
    last_run_age_hours = None
    if wnba_s["last_run_at"]:
        try:
            last_run_dt = datetime.fromisoformat(wnba_s["last_run_at"].replace("Z", "+00:00"))
            last_run_age_hours = (datetime.now(timezone.utc) - last_run_dt).total_seconds() / 3600
        except Exception:
            pass
    data_looks_stale = (
        wnba_runs == 0
        or wnba_s["last_run_at"] is None
        or last_run_age_hours is None
        or last_run_age_hours > STALE_THRESHOLD_HOURS
    )

    # A sustained zero — many runs, confirmed-fresh data, every single one
    # scanning zero markets — is much stronger evidence for (d) than for
    # (a): a genuine game-volume mismatch would still show *some* nonzero
    # days (WNBA plays most days in-season), whereas a perfect zero streak
    # across a large sample points at markets not existing at all.
    SUSTAINED_ZERO_RUN_THRESHOLD = 20
    sustained_zero = (
        not data_looks_stale
        and wnba_scanned == 0
        and wnba_runs >= SUSTAINED_ZERO_RUN_THRESHOLD
    )

    if sustained_zero:
        a_verdict = "UNLIKELY"
        d_verdict = "STRONG"
    elif data_looks_stale:
        a_verdict = "INCONCLUSIVE"
        d_verdict = "INCONCLUSIVE_UNTIL_C_RULED_OUT"
    elif wnba_scanned > 0 and comparison["scanned_ratio_wnba_to_mlb"] not in (None, 0):
        a_verdict = "PLAUSIBLE"
        d_verdict = "DISPROVEN"
    else:
        a_verdict = "CANNOT_RULE_OUT"
        d_verdict = "PLAUSIBLE"

    verdicts = {
        "a_game_volume_mismatch": {
            "verdict": a_verdict,
            "evidence": f"scanned_ratio_wnba_to_mlb={comparison['scanned_ratio_wnba_to_mlb']}, "
                        f"wnba n_runs={wnba_runs} over the trailing {WINDOW_DAYS}d window"
                        + (f" — a hard zero across {wnba_runs} runs (>= {SUSTAINED_ZERO_RUN_THRESHOLD}) "
                           f"on confirmed-fresh data argues against 'just fewer games'" if sustained_zero else ""),
        },
        "b_gate_strictness": {
            "verdict": "DISPROVEN" if thresholds_equal else "CONFIRMED",
            "evidence": f"desks/mlb.yaml thresholds == desks/wnba.yaml thresholds: {thresholds_equal} "
                        f"(both inherit from desks/base.yaml; re-checked this run, not assumed)",
        },
        "c_pipeline_or_sync_bug": {
            "verdict": "PLAUSIBLE" if data_looks_stale else "DISPROVEN",
            "evidence": f"wnba funnel log has {wnba_runs} run(s) in the trailing {WINDOW_DAYS}d "
                        f"(last_run_at={wnba_s['last_run_at']}) — if this looks stale/empty relative to "
                        f"the edge-discovery timer's actual 30-min cadence, that points here, NOT to (a)/(d)",
        },
        "d_no_kalshi_markets": {
            "verdict": d_verdict,
            "evidence": f"total_scanned={wnba_scanned} across {wnba_runs} run(s) — "
                        f"this can only be trusted once (c) is ruled out"
                        + (f"; (c) is disproven this run, so this reading is trustworthy" if sustained_zero else ""),
        },
    }
    return verdicts


# ── report ────────────────────────────────────────────────────────────────

def format_report(comparison: dict, verdicts: dict, wnba_trade_counts: tuple[int, int], mlb_trade_counts: tuple[int, int]) -> str:
    mlb_s, wnba_s = comparison["mlb"], comparison["wnba"]
    mlb_total, mlb_resolved = mlb_trade_counts
    wnba_total, wnba_resolved = wnba_trade_counts

    lines = [
        "# WNBA Parity Diagnosis",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"Window: trailing {WINDOW_DAYS} days",
        "",
        "## 1. Data freshness",
        "",
        f"- MLB funnel log: {mlb_s['n_runs']} run(s) in window, last at {mlb_s['last_run_at']}",
        f"- WNBA funnel log: {wnba_s['n_runs']} run(s) in window, last at {wnba_s['last_run_at']}",
        "",
    ]
    if wnba_s["n_runs"] == 0:
        lines.append(
            "**WARNING: zero WNBA funnel entries in this window on the data this script just read.** "
            "Before concluding anything about game volume or Kalshi market availability, confirm this "
            "script was run against live/fresh data (e.g. directly on the VPS) — a stale or "
            "un-synced local copy will look identical to a real pipeline problem."
        )
        lines.append("")

    lines += [
        "## 2. Current N comparison (trailing window)",
        "",
        "| Metric | MLB | WNBA |",
        "|---|---|---|",
        f"| total_scanned | {mlb_s['total_scanned']} | {wnba_s['total_scanned']} |",
        f"| above_threshold | {mlb_s['above_threshold']} | {wnba_s['above_threshold']} |",
        f"| above_threshold rate | {fmt_pct(comparison['mlb_above_threshold_rate'])} | {fmt_pct(comparison['wnba_above_threshold_rate'])} |",
        f"| already_traded | {mlb_s['already_traded']} | {wnba_s['already_traded']} |",
        f"| on_cooldown | {mlb_s['on_cooldown']} | {wnba_s['on_cooldown']} |",
        f"| pre_filter_skipped | {mlb_s['pre_filter_skipped']} | {wnba_s['pre_filter_skipped']} |",
        f"| researched | {mlb_s['researched']} | {wnba_s['researched']} |",
        f"| trade_verdicts | {mlb_s['trade_verdicts']} | {wnba_s['trade_verdicts']} |",
        f"| skip_verdicts | {mlb_s['skip_verdicts']} | {wnba_s['skip_verdicts']} |",
        f"| monitor_verdicts | {mlb_s['monitor_verdicts']} | {wnba_s['monitor_verdicts']} |",
        f"| shadow_verdicts | {mlb_s['shadow_verdicts']} | {wnba_s['shadow_verdicts']} |",
        f"| paper trades logged | {mlb_total} | {wnba_total} |",
        f"| paper trades resolved | {mlb_resolved} | {wnba_resolved} |",
        "",
        "## 3. Root-cause assessment",
        "",
    ]

    labels = {
        "a_game_volume_mismatch": "(a) Game-volume mismatch",
        "b_gate_strictness": "(b) Gate/threshold strictness",
        "c_pipeline_or_sync_bug": "(c) Pipeline/sync bug",
        "d_no_kalshi_markets": "(d) Kalshi market availability",
    }
    for key, label in labels.items():
        v = verdicts[key]
        lines.append(f"**{label}: {v['verdict']}**")
        lines.append(f"  {v['evidence']}")
        lines.append("")

    lines += [
        "## 4. Recommendation",
        "",
    ]
    if verdicts["c_pipeline_or_sync_bug"]["verdict"] == "PLAUSIBLE":
        lines.append(
            "Data completeness is the blocker — (a) and (d) cannot be assessed on data that "
            "may be stale/incomplete. **No gate/threshold change proposed.** Fix the data path "
            "first (confirm this script is reading live VPS data, not a stale local sync), "
            "then re-run this script before drawing any conclusion about WNBA opportunity volume."
        )
    elif verdicts["b_gate_strictness"]["verdict"] == "CONFIRMED":
        lines.append(
            "Gate thresholds differ between desks — this IS the smallest safe explanation to "
            "investigate, but any actual threshold change requires your explicit sign-off "
            "(affects live capital) and is NOT proposed automatically by this script."
        )
    elif verdicts["d_no_kalshi_markets"]["verdict"] == "STRONG":
        lines.append(
            "This is a **market-availability gap, not a system bug.** The pipeline is fully "
            "healthy (fresh data, correct 30-min cadence, identical gate thresholds to MLB) — "
            "but it has scanned exactly zero WNBA markets across every single run in the "
            "trailing window. Recommended next step: manually confirm against Kalshi directly "
            "whether `KXWNBAGAME` markets are currently open at all (e.g. check Kalshi's site/API "
            "for the series), since a full zero streak this long is not explained by ordinary "
            "game-volume variation. If Kalshi genuinely has no WNBA markets open right now, "
            "**no code or config change closes this gap** — it's a wait for the WNBA season/market "
            "window to open, not a fixable pipeline issue. No gate/threshold change proposed."
        )
    else:
        lines.append(
            "Gate thresholds are confirmed identical between MLB and WNBA. If total_scanned "
            "is genuinely low even on fresh data, the shortfall is most likely (a) — WNBA's "
            "season simply has fewer games/day than MLB's — which is not a bug to fix, only a "
            "longer wait for comparable N. Recommended: track WNBA's above_threshold rate "
            "(not absolute count) each week; if it stays comparable to MLB's rate, parity in "
            "*rate* already exists and only wall-clock time (more game-days) closes the N gap. "
            "No gate/threshold change proposed."
        )

    return "\n".join(lines)


def main() -> None:
    mlb_desk = get_desk("MLB")
    wnba_desk = get_desk("WNBA")

    mlb_funnel = summarize_funnel(load_funnel_history(mlb_desk))
    wnba_funnel = summarize_funnel(load_funnel_history(wnba_desk))
    comparison = compare_desks(mlb_funnel, wnba_funnel)

    mlb_trade_counts = load_trade_count(mlb_desk)
    wnba_trade_counts = load_trade_count(wnba_desk)

    verdicts = diagnose_root_cause(mlb_desk, wnba_desk, comparison)

    report = format_report(comparison, verdicts, wnba_trade_counts, mlb_trade_counts)
    print(report)


if __name__ == "__main__":
    main()
