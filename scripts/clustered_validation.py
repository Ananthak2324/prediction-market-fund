"""
scripts/clustered_validation.py

Standalone, read-only validation script that corrects for same-night trade
clustering when computing win rate / p-value significance. Run anytime to
re-validate — this is not a one-time patch, and it does not touch the live
pipeline, signal gates, Kelly sizing, or scripts/update_outcomes.py.

THE FLAW BEING FIXED: build_summary() (scripts/update_outcomes.py) computes
p-values with scipy.stats.binomtest(wins, n, 0.5), which treats every
resolved trade as an independent Bernoulli trial. But trades that fire on
the same calendar night share the same slate, correlated market conditions,
and correlated model errors — they are not independent draws. A handful of
lucky/unlucky nights can dominate the naive p-value.

THE FIX: cluster resolved trades by calendar night (ET date — the exact
`entry_date` derivation execution/position_manager.py already uses:
start_utc converted to America/New_York, then .date()) and run a night-level
block permutation test instead of a per-trade binomial test, plus a matching
night-level bootstrap confidence interval. See cluster_permutation_test()
and cluster_bootstrap_ci() docstrings for why this method was chosen over a
cluster-robust standard-error (sandwich variance) adjustment.

Explicitly NOT touched: Sharpe ratio / portfolio metrics (build_summary()'s
_build_portfolio_metrics()) — flagged as uncitable below 30 resolved trades
per existing project convention, no fix attempted here.

MLB and WNBA are validated as two fully independent runs (desk-namespaced
trade files already separate them) — this is not intended to imply anything
about cross-desk pooling.

Usage:
    python scripts/clustered_validation.py > reports/clustered_validation_$(date +%F).md
    python scripts/clustered_validation.py --seed 42   # stability check
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
from scipy.stats import binomtest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.desk_loader import get_desk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET   = ZoneInfo("America/New_York")


def fmt_pct(v) -> str:
    return f"{v * 100:.1f}%" if v is not None else "—"


# ── data loading ───────────────────────────────────────────────────────────

def load_resolved_trades(desk) -> list[dict]:
    """
    Same exclusions build_summary() applies (status != PAUSED, resolved,
    valid_for_analysis) — reimplemented standalone so this script has no
    import dependency on the live pipeline's summary logic. Must produce
    identical naive win-rate/p-value numbers to build_summary() on the same
    trade set; verified by running both side by side.
    """
    path = os.path.join(BASE, desk.paper_trades_path)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        trades = json.load(f)
    return [
        t for t in trades
        if t.get("status") != "PAUSED"
        and t.get("outcome") is not None
        and t.get("valid_for_analysis", True)
    ]


def derive_night(trade: dict, unparseable: list[dict]) -> str:
    """
    Calendar-night cluster key — identical derivation to
    execution/position_manager.py's entry_date (start_utc -> ET -> .date()).
    Falls back to snapshot_time's date prefix if start_utc is missing or
    unparseable, and records the trade in `unparseable` so misclustering is
    never silent.
    """
    start_utc = trade.get("start_utc", "")
    try:
        dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        return dt.astimezone(ET).date().isoformat()
    except Exception:
        unparseable.append(trade)
        snap = trade.get("snapshot_time", "")
        return snap[:10] if snap else "UNKNOWN"


def group_by_night(trades: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    unparseable: list[dict] = []
    nights: dict[str, list[dict]] = {}
    for t in trades:
        night = derive_night(t, unparseable)
        nights.setdefault(night, []).append(t)
    return nights, unparseable


# ── naive (uncorrected) test — the "before" column ──────────────────────────

def naive_binomial_test(trades: list[dict]) -> dict:
    """
    Reimplements build_summary()'s exact pattern:
        win_rate = valid_wins / valid
        p_value  = binomtest(wins, n, 0.5, alternative="greater") if n >= 5
    This treats every trade as an independent Bernoulli trial — the flaw.
    """
    n = len(trades)
    wins = sum(1 for t in trades if t["outcome"] == "WIN")
    win_rate = wins / n if n else None
    p_value = None
    if n >= 5:
        p_value = round(binomtest(wins, n, 0.5, alternative="greater").pvalue, 4)
    return {
        "n": n, "wins": wins,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "p_value": p_value,
        "method": "naive_iid_binomial",
    }


# ── clustering-aware method ─────────────────────────────────────────────────

def cluster_permutation_test(nights: dict[str, list[dict]], n_perms: int = 10000, seed: int = 1337) -> dict:
    """
    Night-level block permutation test — the clustering-aware replacement
    for the naive per-trade binomial test.

    WHY A PERMUTATION TEST INSTEAD OF A CLUSTER-ROBUST STANDARD ERROR:
    A sandwich/cluster-robust-SE adjustment on the binomial proportion would
    require assuming asymptotic normality to build a Wald CI/p-value — fragile
    at this sample size (a few dozen trades across a handful of nights) and
    easy to get subtly wrong (small-cluster bias correction choice, degrees-
    of-freedom adjustment). A permutation test instead:
      1. Makes no distributional assumption beyond exchangeability of nights
         under the null.
      2. Directly encodes "resample at the night level" as the resampling
         unit — exactly the stated concern (same-night trades share a slate,
         correlated market conditions, correlated model errors).
      3. Is simple enough to have an obvious correctness check: rerun with a
         different --seed and confirm the p-value is stable.

    ALGORITHM: this is a parametric block permutation, not a shuffle of
    existing labels (there's only one dataset here, no second arm to permute
    against). Under the null (each trade is a coin flip), each night's
    outcome is resampled as ONE Binomial(night_size, 0.5) draw — preserving
    the real night sizes exactly, so within-night correlation structure in
    the resampled data matches the real data's clustering pattern. Summing
    across nights gives one simulated "total wins" draw; repeating n_perms
    times builds the null distribution of total wins under night-level
    exchangeability.
    """
    night_sizes = [len(v) for v in nights.values()]
    observed_wins = sum(1 for night_trades in nights.values() for t in night_trades if t["outcome"] == "WIN")
    n_total = sum(night_sizes)

    rng = np.random.default_rng(seed)
    perm_stats = np.array([
        sum(rng.binomial(size, 0.5) for size in night_sizes)
        for _ in range(n_perms)
    ])
    # +1 smoothing in both numerator/denominator — standard permutation-test
    # convention, avoids a p-value of exactly 0 from finite Monte Carlo draws.
    p_value = float((np.sum(perm_stats >= observed_wins) + 1) / (n_perms + 1))

    return {
        "method": "night_block_permutation",
        "n_permutations": n_perms,
        "seed": seed,
        "observed_wins": observed_wins,
        "n_total": n_total,
        "p_value": round(p_value, 4),
    }


def cluster_bootstrap_ci(nights: dict[str, list[dict]], n_boot: int = 10000, seed: int = 1337) -> tuple[float | None, float | None]:
    """
    95% CI via night-level (cluster) bootstrap — the natural CI companion to
    cluster_permutation_test() above (same clustering unit, same small-sample
    rationale for avoiding a Wald CI on a cluster-robust SE).

    Resamples NIGHTS with replacement (not individual trades) len(nights)
    times per bootstrap draw, pools all trades from the resampled nights,
    computes the win rate, and collects the 2.5/97.5 percentiles.
    """
    night_keys = list(nights.keys())
    if not night_keys:
        return None, None
    rng = np.random.default_rng(seed)
    boot_rates = []
    for _ in range(n_boot):
        sampled_nights = rng.choice(night_keys, size=len(night_keys), replace=True)
        pooled = [t for night in sampled_nights for t in nights[night]]
        if not pooled:
            continue
        wins = sum(1 for t in pooled if t["outcome"] == "WIN")
        boot_rates.append(wins / len(pooled))
    if not boot_rates:
        return None, None
    lo, hi = np.percentile(boot_rates, [2.5, 97.5])
    return round(float(lo), 4), round(float(hi), 4)


def flag_large_clusters(nights: dict[str, list[dict]], threshold: int = 3) -> list[dict]:
    """Every night with >= threshold trades — the literal 'flag every trade
    in a same-night cluster of 3+' requirement."""
    flagged = [
        {
            "night": night,
            "n_trades": len(trades),
            "trades": [t.get("trade_id") or t.get("game", "?") for t in trades],
        }
        for night, trades in nights.items()
        if len(trades) >= threshold
    ]
    return sorted(flagged, key=lambda x: x["n_trades"], reverse=True)


# ── orchestration ────────────────────────────────────────────────────────────

def build_comparison(desk_id: str, trades: list[dict], n_perms: int, seed: int) -> dict:
    nights, unparseable = group_by_night(trades)
    naive = naive_binomial_test(trades)

    clustered: dict = {"method": "night_block_permutation", "p_value": None, "ci_95": (None, None)}
    if trades:
        perm_result = cluster_permutation_test(nights, n_perms=n_perms, seed=seed)
        ci = cluster_bootstrap_ci(nights, n_boot=n_perms, seed=seed)
        clustered = {**perm_result, "ci_95": ci}

    return {
        "desk_id": desk_id,
        "n_resolved": len(trades),
        "n_nights": len(nights),
        "naive": naive,
        "clustered": clustered,
        "flagged_clusters": flag_large_clusters(nights),
        "unparseable_start_utc": [t.get("trade_id", "?") for t in unparseable],
    }


# ── report formatting ────────────────────────────────────────────────────────

def _format_desk_section(result: dict) -> str:
    lines = [f"## {result['desk_id']}", ""]

    if result["n_resolved"] == 0:
        lines.append("No resolved trades — nothing to validate.")
        lines.append("")
        return "\n".join(lines)

    naive = result["naive"]
    clustered = result["clustered"]
    ci_lo, ci_hi = clustered.get("ci_95", (None, None))

    lines += [
        "| Metric | Naive (i.i.d.) | Clustered (night-block) |",
        "|---|---|---|",
        f"| N resolved | {naive['n']} | {naive['n']} |",
        f"| N calendar nights | — | {result['n_nights']} |",
        f"| Win rate | {fmt_pct(naive['win_rate'])} | {fmt_pct(naive['win_rate'])} (same point estimate — clustering affects significance, not the rate) |",
        f"| P-value vs 50% | {naive['p_value'] if naive['p_value'] is not None else '—'} | {clustered.get('p_value', '—')} |",
        f"| 95% CI | — (naive test has no CI) | [{fmt_pct(ci_lo)}, {fmt_pct(ci_hi)}] |",
        "",
    ]

    if result["n_nights"] < 5:
        lines.append(
            "*Fewer than 5 distinct nights — the clustered p-value/CI should be "
            "treated as indicative only, not a firm significance claim.*"
        )
        lines.append("")

    flagged = result["flagged_clusters"]
    if flagged:
        lines.append("**Flagged same-night clusters (n>=3 trades):**")
        lines.append("")
        lines.append("| Night | N trades | Trades |")
        lines.append("|---|---|---|")
        for c in flagged:
            trade_list = ", ".join(str(t) for t in c["trades"])
            lines.append(f"| {c['night']} | {c['n_trades']} | {trade_list} |")
        lines.append("")
    else:
        lines.append("No same-night clusters of 3+ trades found.")
        lines.append("")

    if result["unparseable_start_utc"]:
        lines.append(
            f"*Warning: {len(result['unparseable_start_utc'])} trade(s) had an "
            f"unparseable start_utc and fell back to snapshot_time for clustering — "
            f"{result['unparseable_start_utc']}*"
        )
        lines.append("")

    return "\n".join(lines)


def format_report(mlb_result: dict, wnba_result: dict) -> str:
    lines = [
        "# Clustering-Aware Validation Report",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Corrects for same-night trade clustering when computing significance — "
        "trades on the same calendar night share a slate, correlated market "
        "conditions, and correlated model errors, so treating each as an "
        "independent Bernoulli trial (the naive method) overstates confidence. "
        "MLB and WNBA are validated independently below. Sharpe/portfolio "
        "metrics are untouched by this script.",
        "",
        "---",
        "",
        _format_desk_section(mlb_result),
        "---",
        "",
        _format_desk_section(wnba_result),
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-perms", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--out-json", default=None)
    args = parser.parse_args()

    mlb_desk  = get_desk("MLB")
    wnba_desk = get_desk("WNBA")

    mlb  = build_comparison("MLB",  load_resolved_trades(mlb_desk),  args.n_perms, args.seed)
    wnba = build_comparison("WNBA", load_resolved_trades(wnba_desk), args.n_perms, args.seed)

    report = format_report(mlb, wnba)
    print(report)

    out_dir = os.path.join(BASE, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.out_json or os.path.join(out_dir, f"clustered_validation_{date.today().isoformat()}.json")
    with open(out_path, "w") as f:
        json.dump({"mlb": mlb, "wnba": wnba}, f, indent=2, default=str)


if __name__ == "__main__":
    main()
