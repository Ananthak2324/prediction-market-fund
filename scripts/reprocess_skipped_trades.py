"""
scripts/reprocess_skipped_trades.py

Retroactively apply the new calibration logic to all historical SKIP decisions
and compute how many would have been TRADEs (and whether those were winners).

Logic applied per skipped trade:
  1. Pre-filter heuristic: if the old record has pinnacle_stable=False → still SKIP
  2. News-age heuristic:
       - news_found=False           → would TRADE
       - news_found=True, pinnacle_stable=True, keywords suggest chronic condition → would TRADE
       - news_found=True, pinnacle_stable=True, keywords suggest new scratch → borderline SKIP
       - news_found=True, pinnacle_stable=False → still SKIP
  3. For each retroactive TRADE, pull shadow_outcome (pre-populated by update_outcomes.py)

Output:
  data/retroactive_analysis.json   — machine-readable results
  Printed summary table            — human-readable overview

P-value: exact binomial test (no scipy dependency).
"""

import json
import math
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIPPED_FILE = os.path.join(BASE, "data", "skipped_trades.json")
OUTPUT_FILE  = os.path.join(BASE, "data", "retroactive_analysis.json")

# Keywords in news_detail that suggest a CHRONIC (already-priced) condition
CHRONIC_KEYWORDS = [
    "il", "injured list", "disabled list", "season-ending", "season ending",
    "out for the season", "placed on", "tommy john", "acl", "acuna", "strider",
    "seager", "out since", "has been out", "has been on", "missed the last",
    "day-to-day", "not expected", "expected to return", "rehab",
]

# Keywords in news_detail that suggest a genuine NEW scratch (this-game disqualifier)
NEW_SCRATCH_KEYWORDS = [
    "scratched", "scratch tonight", "scratch today", "ruled out tonight",
    "ruled out today", "late scratch", "did not start", "not in lineup",
    "will not start", "won't start",
]


def _is_chronic(news_detail: str) -> bool:
    nd = (news_detail or "").lower()
    has_chronic = any(kw in nd for kw in CHRONIC_KEYWORDS)
    has_new     = any(kw in nd for kw in NEW_SCRATCH_KEYWORDS)
    return has_chronic and not has_new


def _retroactive_verdict(trade: dict) -> str:
    """
    Apply new calibration logic to a historical skip record.
    Returns "TRADE" or "SKIP".
    """
    # Pre-filter skips: trust the pre_filter decision (keep as SKIP)
    if trade.get("pre_filter_skip"):
        return "SKIP"

    pinnacle_stable   = trade.get("pinnacle_stable", True)
    news_found        = trade.get("news_found",  False)
    news_detail       = trade.get("news_detail") or ""
    pinnacle_movement = float(trade.get("pinnacle_movement") or 0.0)

    # Pinnacle moved hard → still SKIP (sharp money reacting)
    if not pinnacle_stable and pinnacle_movement >= 0.05:
        return "SKIP"

    # No news found → behavioral gap, TRADE
    if not news_found:
        return "TRADE"

    # News found + Pinnacle stable → check if news is chronic or new
    if pinnacle_stable:
        if _is_chronic(news_detail):
            return "TRADE"
        # Ambiguous or new scratch keywords → keep as SKIP (conservative)
        return "SKIP"

    # News found + Pinnacle unstable → SKIP
    return "SKIP"


def _binomial_p_value(wins: int, n: int, p: float = 0.5) -> float:
    """
    One-sided p-value: P(X >= wins) under H0 (p=0.5, no edge).
    Uses exact binomial PMF. No scipy.
    """
    if n == 0:
        return 1.0
    p_val = 0.0
    for k in range(wins, n + 1):
        # log-space to avoid overflow
        log_binom = math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
        log_prob  = log_binom + k * math.log(p) + (n - k) * math.log(1 - p)
        p_val    += math.exp(log_prob)
    return round(p_val, 6)


def main() -> None:
    if not os.path.exists(SKIPPED_FILE):
        print(f"ERROR: {SKIPPED_FILE} not found.")
        sys.exit(1)

    with open(SKIPPED_FILE) as f:
        skipped = json.load(f)

    print(f"Loaded {len(skipped)} skipped trades from {SKIPPED_FILE}")

    results: list[dict] = []
    would_trade: list[dict] = []
    kept_skip:   list[dict] = []

    for trade in skipped:
        retro = _retroactive_verdict(trade)
        record = {
            "trade_id":         trade.get("trade_id"),
            "game":             trade.get("game"),
            "team":             trade.get("team"),
            "signal":           trade.get("signal"),
            "gap":              trade.get("gap"),
            "abs_gap":          trade.get("abs_gap"),
            "skipped_at":       trade.get("skipped_at"),
            "pinnacle_stable":  trade.get("pinnacle_stable"),
            "pinnacle_movement":trade.get("pinnacle_movement"),
            "news_found":       trade.get("news_found"),
            "news_detail":      trade.get("news_detail"),
            "pre_filter_skip":  trade.get("pre_filter_skip", False),
            "old_verdict":      "SKIP",
            "new_verdict":      retro,
            "shadow_outcome":   trade.get("shadow_outcome"),
            "shadow_correct":   trade.get("shadow_correct"),
            "shadow_resolved_at": trade.get("shadow_resolved_at"),
        }
        results.append(record)
        if retro == "TRADE":
            would_trade.append(record)
        else:
            kept_skip.append(record)

    # ── Stats on retroactive TRADEs ───────────────────────────────────────────
    resolved  = [r for r in would_trade if r["shadow_outcome"] is not None]
    unresolved = [r for r in would_trade if r["shadow_outcome"] is None]
    wins       = [r for r in resolved if r["shadow_correct"] is True]
    losses     = [r for r in resolved if r["shadow_correct"] is False]

    n     = len(resolved)
    w     = len(wins)
    win_rate = (w / n) if n > 0 else None
    p_val = _binomial_p_value(w, n, p=0.5) if n > 0 else None

    # ── Print summary ─────────────────────────────────────────────────────────
    print(f"\n{'═'*70}")
    print(f"  RETROACTIVE ANALYSIS — NEW CALIBRATION RULES")
    print(f"{'═'*70}")
    print(f"  Total skipped trades:         {len(skipped):>5}")
    print(f"  Would still SKIP:             {len(kept_skip):>5}")
    print(f"  Would now TRADE:              {len(would_trade):>5}  ({len(would_trade)/len(skipped):.1%} of skips)")
    print(f"")
    print(f"  Of retroactive TRADEs:")
    print(f"    Resolved (shadow outcome):  {n:>5}")
    print(f"    Unresolved:                 {len(unresolved):>5}")
    if n > 0:
        print(f"    Wins:                       {w:>5}  ({win_rate:.1%})")
        print(f"    Losses:                     {len(losses):>5}")
        print(f"    P-value (H0: p=0.5):        {p_val:.4f}{'  ★ significant' if p_val is not None and p_val < 0.05 else ''}")

    print(f"\n  Breakdown — why SKIPs would flip to TRADE:")
    no_news = sum(1 for r in would_trade if not r["news_found"])
    chronic = sum(1 for r in would_trade if r["news_found"])
    print(f"    No news found:              {no_news:>5}")
    print(f"    News found but chronic:     {chronic:>5}")

    print(f"\n  Sample retroactive TRADEs (first 10):")
    print(f"  {'GAME':<40} {'GAP':>7}  {'OUTCOME':<10}  DETAIL")
    print(f"  {'─'*80}")
    for r in would_trade[:10]:
        outcome = r["shadow_outcome"] or "pending"
        detail  = (r["news_detail"] or "")[:40]
        print(f"  {str(r['game']):<40} {(r['abs_gap'] or 0):>6.1%}  {outcome:<10}  {detail}")

    # ── Save output ────────────────────────────────────────────────────────────
    output = {
        "generated_at":        datetime.now().isoformat(),
        "total_skipped":       len(skipped),
        "would_still_skip":    len(kept_skip),
        "would_now_trade":     len(would_trade),
        "resolved_count":      n,
        "wins":                w,
        "losses":              len(losses),
        "win_rate":            round(win_rate, 4) if win_rate is not None else None,
        "p_value":             p_val,
        "no_news_flips":       no_news,
        "chronic_news_flips":  chronic,
        "records":             results,
    }
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
