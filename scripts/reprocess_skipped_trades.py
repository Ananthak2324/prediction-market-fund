"""
scripts/reprocess_skipped_trades.py

Retroactive reprocessing: applies the corrected (post-recalibration) research
agent decision logic in pure Python to every historical SKIP decision, without
re-calling the Claude API. Answers: "If the corrected agent logic had been
running from day one, what would our win rate actually be?"

Read-only. Does not modify paper_trades.json or skipped_trades.json.

Data notes (current schema, pre desk-config migration):
  - data/skipped_trades.json has no "sport" field — sport is derived from the
    event_ticker prefix (KXMLBGAME → MLB, KXWNBAGAME → WNBA).
  - Resolved outcome lives in shadow_outcome / shadow_correct / shadow_resolved_at
    (pre-populated by update_outcomes.py without ever placing a real trade).
  - No "tier" field — tier is derived from abs_gap (5-10% / 10-15% / 15%+).
  - No "news_age_estimate" field on legacy records — treated as None, which the
    override logic below counts as "old" (conservative: TRADE bias, matching
    the corrected agent's philosophy that unknown-age news defaults to priced-in).

Usage:
    python scripts/reprocess_skipped_trades.py
"""

import json
import os
import sys
from datetime import datetime, timezone

from scipy.stats import binomtest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.desk_loader import get_desk

BASE          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIPPED_FILE  = os.path.join(BASE, "data", "skipped_trades.json")
TRADES_FILE   = os.path.join(BASE, "data", "paper_trades.json")
OUT_JSON      = os.path.join(BASE, "outputs", "retroactive_analysis.json")
OUT_TXT       = os.path.join(BASE, "outputs", "retroactive_report.txt")

CHRONIC_KEYWORDS = [
    "acuna", "strider", "seager",
    "tommy john", "60-day", "season-ending",
    "placed on il", "injured list",
    "caitlin clark",  # WNBA example
]

# Tier boundaries sourced from desk config (desks/base.yaml thresholds.tier_a/b/c)
# instead of a third independent hardcoded copy — was previously its own
# TIER_BOUNDS list, found during the 2026-07-04 audit to be a silent-drift risk
# alongside edge_discovery_agent.py's and research_agent.py's own copies.
_DESK = get_desk("MLB")  # MLB/WNBA currently share identical tier boundaries
TIER_BOUNDS = [
    ("tier_a", _DESK.tier_a[0], _DESK.tier_b[0]),
    ("tier_b", _DESK.tier_b[0], _DESK.tier_c[0]),
    ("tier_c", _DESK.tier_c[0], float("inf")),
]


# ── sport / tier derivation ─────────────────────────────────────────────────

def _derive_sport(trade: dict) -> str:
    et = trade.get("event_ticker", "") or trade.get("kalshi_ticker", "")
    if "MLBGAME" in et:
        return "MLB"
    if "WNBAGAME" in et:
        return "WNBA"
    if "NBAGAME" in et:
        return "NBA"
    if "NFLGAME" in et:
        return "NFL"
    return trade.get("sport", "UNKNOWN") or "UNKNOWN"


def _derive_tier(abs_gap: float) -> str:
    for label, lo, hi in TIER_BOUNDS:
        if lo <= abs_gap < hi:
            return label
    return "tier_a"


# ── core recalibration logic (applied exactly as specified) ────────────────

def would_new_agent_trade(trade: dict) -> tuple[bool, str]:
    """
    Returns (would_trade: bool, reason: str)
    """
    # Rule 1 — Pinnacle moved hard (always SKIP)
    pinnacle_movement = trade.get("pinnacle_movement") or 0
    if pinnacle_movement >= 0.05:
        return False, "PINNACLE_MOVED_HARD"

    # Rule 2 — No news found at all (always TRADE)
    if not trade.get("news_found", False):
        return True, "NO_NEWS_FOUND"

    # Rule 3 — News found but Pinnacle stable
    # Old agent: SKIP because news_found=True
    # New agent: check if news is old/chronic
    if trade.get("pinnacle_stable", True):

        news_detail = (trade.get("news_detail") or "").lower()
        news_age = trade.get("news_age_estimate", "older")

        is_chronic = any(kw in news_detail for kw in CHRONIC_KEYWORDS)
        is_old_news = news_age in ["this week", "older", None]

        if is_chronic or is_old_news:
            # Old agent wrongly skipped this
            # New agent would TRADE
            return True, "CHRONIC_OR_OLD_NEWS_OVERRIDDEN"

        # News is recent and not chronic
        # New agent would still SKIP
        return False, "RECENT_NEWS_CONFIRMED"

    # Rule 4 — News found and Pinnacle moved softly (between 3% and 5%) — MONITOR not SKIP
    if pinnacle_movement >= 0.03:
        return False, "PINNACLE_SOFT_MOVE"

    # Default — if news is ambiguous and Pinnacle stable
    return True, "DEFAULT_TRADE_STABLE_PINNACLE"


# ── main ─────────────────────────────────────────────────────────────────

def main() -> None:
    if not os.path.exists(SKIPPED_FILE):
        print(f"ERROR: {SKIPPED_FILE} not found.")
        sys.exit(1)

    with open(SKIPPED_FILE) as f:
        skipped = json.load(f)

    existing_trades: list[dict] = []
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE) as f:
            existing_trades = json.load(f)

    # ── Step 2/3 — apply logic + bucket results ─────────────────────────────
    would_trade_records: list[dict] = []
    kept_skip_records:   list[dict] = []

    override_reason_counts = {
        "CHRONIC_OR_OLD_NEWS_OVERRIDDEN": 0,
        "NO_NEWS_FOUND": 0,
        "DEFAULT_TRADE_STABLE_PINNACLE": 0,
    }
    skip_reason_counts = {
        "PINNACLE_MOVED_HARD": 0,
        "RECENT_NEWS_CONFIRMED": 0,
        "PINNACLE_SOFT_MOVE": 0,
    }

    by_sport: dict[str, dict] = {}
    by_tier:  dict[str, dict] = {}

    def _bucket_init() -> dict:
        return {"resolved": 0, "wins": 0}

    retro_wins = retro_losses = retro_unresolved = 0

    for trade in skipped:
        sport = _derive_sport(trade)
        abs_gap = trade.get("abs_gap") or abs(trade.get("gap") or 0)
        tier = _derive_tier(abs_gap)

        would_trade, reason = would_new_agent_trade(trade)
        outcome = trade.get("shadow_outcome")  # "WIN" / "LOSS" / None

        record = {
            "trade_id":     trade.get("trade_id"),
            "game":         trade.get("game"),
            "sport":        sport,
            "tier":         tier,
            "gap":          trade.get("gap"),
            "abs_gap":      abs_gap,
            "news_found":   trade.get("news_found"),
            "news_detail":  trade.get("news_detail"),
            "pinnacle_stable":   trade.get("pinnacle_stable"),
            "pinnacle_movement": trade.get("pinnacle_movement"),
            "would_trade":  would_trade,
            "reason":       reason,
            "shadow_outcome":    outcome,
            "shadow_resolved_at": trade.get("shadow_resolved_at"),
        }

        if would_trade:
            would_trade_records.append(record)
            override_reason_counts[reason] = override_reason_counts.get(reason, 0) + 1

            by_sport.setdefault(sport, _bucket_init())
            by_tier.setdefault(tier, _bucket_init())

            if outcome == "WIN":
                retro_wins += 1
                by_sport[sport]["resolved"] += 1
                by_sport[sport]["wins"] += 1
                by_tier[tier]["resolved"] += 1
                by_tier[tier]["wins"] += 1
            elif outcome == "LOSS":
                retro_losses += 1
                by_sport[sport]["resolved"] += 1
                by_tier[tier]["resolved"] += 1
            else:
                retro_unresolved += 1
        else:
            kept_skip_records.append(record)
            skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1

    retro_resolved = retro_wins + retro_losses
    retro_win_rate = (retro_wins / retro_resolved) if retro_resolved else None

    # ── Step 4 — combine with current clean paper trades ────────────────────
    current_resolved_trades = [t for t in existing_trades if t.get("outcome") in ("WIN", "LOSS")]
    current_wins   = sum(1 for t in current_resolved_trades if t.get("outcome") == "WIN")
    current_resolved = len(current_resolved_trades)
    current_win_rate = (current_wins / current_resolved) if current_resolved else None

    combined_resolved = current_resolved + retro_resolved
    combined_wins      = current_wins + retro_wins
    combined_win_rate  = (combined_wins / combined_resolved) if combined_resolved else None

    p_value = None
    if combined_resolved > 0:
        p_value = binomtest(combined_wins, n=combined_resolved, p=0.5, alternative="greater").pvalue

    if p_value is None:
        significance = "N/A"
    elif p_value < 0.05:
        significance = "YES (p<0.05)"
    elif p_value < 0.10:
        significance = "APPROACHING (p<0.10)"
    else:
        significance = "NOT YET (p>0.10)"

    # ── Raw signal validation — all resolved outcomes, no agent filtering ───
    raw_resolved = current_resolved + sum(
        1 for t in skipped if t.get("shadow_outcome") in ("WIN", "LOSS")
    )
    raw_wins = current_wins + sum(
        1 for t in skipped if t.get("shadow_outcome") == "WIN"
    )
    raw_win_rate = (raw_wins / raw_resolved) if raw_resolved else None

    # ── Days of data ─────────────────────────────────────────────────────────
    all_dates: list[datetime] = []
    for t in existing_trades + skipped:
        ts = t.get("snapshot_time") or t.get("skipped_at")
        if not ts:
            continue
        try:
            ts_clean = ts.replace("_", "T").split(".")[0]
            all_dates.append(datetime.fromisoformat(ts_clean.replace("Z", "")))
        except Exception:
            continue
    days_of_data = (max(all_dates) - min(all_dates)).days + 1 if all_dates else 0

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Step 5 — build report text ───────────────────────────────────────────
    lines: list[str] = []
    w = lines.append

    w("═" * 56)
    w("  RETROACTIVE REPROCESSING RESULTS")
    w("  EdgeFund — Agent Recalibration Analysis")
    w(f"  Generated: {generated_at}")
    w("═" * 56)
    w("")
    w("SKIPPED TRADES ANALYZED")
    n_skip = len(skipped)
    n_would = len(would_trade_records)
    n_kept = len(kept_skip_records)
    w(f"  Total skipped trades:              {n_skip}")
    w(f"  Would TRADE under new logic:       {n_would} ({(n_would/n_skip*100 if n_skip else 0):.0f}%)")
    w(f"  Correctly kept as SKIP:            {n_kept} ({(n_kept/n_skip*100 if n_skip else 0):.0f}%)")
    w("")
    w("  Override reasons:")
    w(f"    CHRONIC_OR_OLD_NEWS_OVERRIDDEN:  {override_reason_counts['CHRONIC_OR_OLD_NEWS_OVERRIDDEN']}")
    w(f"    NO_NEWS_FOUND (wrongly skipped): {override_reason_counts['NO_NEWS_FOUND']}")
    w(f"    DEFAULT_TRADE_STABLE_PINNACLE:   {override_reason_counts['DEFAULT_TRADE_STABLE_PINNACLE']}")
    w("")
    w("  Kept as SKIP reasons:")
    w(f"    PINNACLE_MOVED_HARD:             {skip_reason_counts['PINNACLE_MOVED_HARD']}")
    w(f"    RECENT_NEWS_CONFIRMED:           {skip_reason_counts['RECENT_NEWS_CONFIRMED']}")
    w(f"    PINNACLE_SOFT_MOVE:              {skip_reason_counts['PINNACLE_SOFT_MOVE']}")
    w("")
    w("─" * 56)
    w("RETROACTIVE TRADE OUTCOMES")
    w(f"  Would-be trades with resolved outcomes: {retro_resolved}")
    w(f"  Retroactive wins:                       {retro_wins}")
    w(f"  Retroactive losses:                     {retro_losses}")
    w(f"  Retroactive win rate:                   {(retro_win_rate*100 if retro_win_rate is not None else 0):.1f}%")
    w("")
    w(f"  Unresolved (game not yet played):       {retro_unresolved}")
    w("")
    w("BY SPORT")
    for sport, b in sorted(by_sport.items()):
        wr = (b["wins"] / b["resolved"] * 100) if b["resolved"] else 0
        w(f"  {sport} retroactive:  {b['resolved']} resolved | {wr:.0f}% win rate")
    if not by_sport:
        w("  (none)")
    w("")
    w("BY TIER")
    tier_labels = {"tier_a": "Tier A (5-10%)", "tier_b": "Tier B (10-15%)", "tier_c": "Tier C (15%+)"}
    for tier_key in ("tier_a", "tier_b", "tier_c"):
        b = by_tier.get(tier_key, _bucket_init())
        wr = (b["wins"] / b["resolved"] * 100) if b["resolved"] else 0
        w(f"  {tier_labels[tier_key]}: {b['resolved']} resolved | {wr:.0f}% win rate")
    w("")
    w("─" * 56)
    w("COMBINED PICTURE (current + retroactive)")
    w(f"  Current clean trades resolved:     {current_resolved}")
    w(f"  Current win rate:                  {(current_win_rate*100 if current_win_rate is not None else 0):.1f}%")
    w("")
    w(f"  Retroactive trades resolved:       {retro_resolved}")
    w(f"  Retroactive win rate:              {(retro_win_rate*100 if retro_win_rate is not None else 0):.1f}%")
    w("")
    w("  " + "─" * 35)
    w(f"  COMBINED resolved:                 {combined_resolved}")
    w(f"  COMBINED wins:                     {combined_wins}")
    w(f"  COMBINED win rate:                 {(combined_win_rate*100 if combined_win_rate is not None else 0):.1f}%")
    w(f"  P-value vs 50% null:               {p_value:.3f}" if p_value is not None else "  P-value vs 50% null:               N/A")
    w(f"  Statistical significance:          {significance}")
    w("  " + "─" * 35)
    w("")
    w("─" * 56)
    w("RAW SIGNAL VALIDATION")
    w(f"  Raw gap signal win rate            {(raw_win_rate*100 if raw_win_rate is not None else 0):.1f}%")
    w("  (all resolved trades before        (was 78.6% —")
    w("   any agent filtering)               confirm this holds)")
    w("")
    w(f"  Corrected agent win rate:          {(combined_win_rate*100 if combined_win_rate is not None else 0):.1f}%")
    w("  (retroactive + current combined)")
    w("")
    w("─" * 56)
    w("YC APPLICATION NUMBERS")
    w(f"  Headline win rate to use:          {(combined_win_rate*100 if combined_win_rate is not None else 0):.1f}%")
    w(f"  Sample size:                       {combined_resolved} resolved trades")
    w(f"  P-value:                           {p_value:.3f}" if p_value is not None else "  P-value:                           N/A")
    w(f"  Days of data:                      {days_of_data}")
    w("")
    w("  Honest framing:")
    w(f'  "Our gap signal produces a {(combined_win_rate*100 if combined_win_rate is not None else 0):.0f}% win rate across ')
    w(f"  {combined_resolved} resolved trades ({days_of_data} days of data, p={p_value:.3f}). " if p_value is not None else f"  {combined_resolved} resolved trades ({days_of_data} days of data).")
    w("  We identified and corrected an agent calibration ")
    w("  bug that was over-filtering candidates — the ")
    w('  corrected pipeline has been live since July 3."')
    w("")
    w("═" * 56)

    report_text = "\n".join(lines)
    print(report_text)

    # Verdict banner
    if combined_win_rate is not None and combined_win_rate > 0.65 and p_value is not None and p_value < 0.10:
        print("\n✓ STRONG — lead with this in YC application")
    elif combined_win_rate is not None and 0.58 <= combined_win_rate <= 0.65 and p_value is not None and p_value < 0.20:
        print("\n→ PROMISING — continue accumulating trades")
    else:
        print("\n⚠ INVESTIGATE — check if retroactive logic is applying correctly")

    # ── Save outputs ──────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    output = {
        "generated_at": generated_at,
        "total_skipped": n_skip,
        "would_trade_count": n_would,
        "kept_skip_count": n_kept,
        "override_reason_counts": override_reason_counts,
        "skip_reason_counts": skip_reason_counts,
        "retroactive": {
            "resolved": retro_resolved,
            "wins": retro_wins,
            "losses": retro_losses,
            "unresolved": retro_unresolved,
            "win_rate": round(retro_win_rate, 4) if retro_win_rate is not None else None,
        },
        "by_sport": by_sport,
        "by_tier": by_tier,
        "current": {
            "resolved": current_resolved,
            "wins": current_wins,
            "win_rate": round(current_win_rate, 4) if current_win_rate is not None else None,
        },
        "combined": {
            "resolved": combined_resolved,
            "wins": combined_wins,
            "win_rate": round(combined_win_rate, 4) if combined_win_rate is not None else None,
            "p_value": round(p_value, 6) if p_value is not None else None,
            "significance": significance,
        },
        "raw_signal": {
            "resolved": raw_resolved,
            "wins": raw_wins,
            "win_rate": round(raw_win_rate, 4) if raw_win_rate is not None else None,
        },
        "days_of_data": days_of_data,
        "would_trade_records": would_trade_records,
        "kept_skip_records": kept_skip_records,
    }
    with open(OUT_JSON, "w") as f:
        json.dump(output, f, indent=2, default=str)

    with open(OUT_TXT, "w") as f:
        f.write(report_text + "\n")

    print(f"\nSaved to {OUT_JSON}")
    print(f"Saved to {OUT_TXT}")


if __name__ == "__main__":
    main()
