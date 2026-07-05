"""
agent/pre_filter.py

Pure Python pre-filter for edge discovery candidates.
Runs BEFORE the research agent — no API calls, no cost.

Catches obvious disqualifiers so the research agent only sees
candidates worth evaluating. Returns PROCEED or SKIP with a
specific reason code.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.desk_loader import DeskConfig

# Fallback defaults if a desk config is missing a threshold — kept in sync with
# desks/base.yaml's thresholds block.
_V_PROB_MIN_DEFAULT              = 0.20
_V_PROB_MAX_DEFAULT              = 0.80
_PINNACLE_MOVE_THRESHOLD_DEFAULT = 0.05


def pre_filter(
    desk: DeskConfig,
    candidate: dict,
    existing_trades: list,
    snapshot_time: datetime | None = None,
) -> dict:
    """
    Run cheap Python checks before burning a research agent API call.

    Args:
        desk:            DeskConfig — thresholds sourced from desks/<id>.yaml.
        candidate:       Edge discovery candidate dict (from compute_gap_matrix).
        existing_trades: Current contents of paper_trades.json.
        snapshot_time:   UTC datetime of this discovery run (defaults to now).

    Returns:
        {"action": "SKIP",    "reason": "PRE_FILTER_*: <detail>"}
        {"action": "PROCEED", "reason": "passes all pre-filter checks"}
    """
    v_prob_min = desk.get("thresholds.v_prob_min", _V_PROB_MIN_DEFAULT)
    v_prob_max = desk.get("thresholds.v_prob_max", _V_PROB_MAX_DEFAULT)
    pinnacle_move_threshold = desk.get("thresholds.pinnacle_move_hard_gate", _PINNACLE_MOVE_THRESHOLD_DEFAULT)

    ticker  = candidate.get("kalshi_ticker", "")
    v_prob  = float(candidate.get("v_prob") or candidate.get("pinnacle_prob") or 0.0)
    start   = candidate.get("start_utc", "")
    now_utc = snapshot_time or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    # CHECK 1 — Data validity
    # Pinnacle never prices an MLB/WNBA team below 20% or above 80% in a regular-season
    # game. Values outside this range signal a data-matching error, not a real gap.
    if v_prob < v_prob_min or v_prob > v_prob_max:
        return {
            "action": "SKIP",
            "reason": (
                f"PRE_FILTER_DATA_ERROR: v_prob={v_prob:.3f} outside valid range "
                f"{v_prob_min}-{v_prob_max} — likely data matching error"
            ),
        }

    # CHECK 2 — Already traded
    # Never trade the same Kalshi ticker twice regardless of cooldown state.
    if ticker and any(t.get("kalshi_ticker") == ticker for t in existing_trades):
        return {
            "action": "SKIP",
            "reason": f"PRE_FILTER_DUPLICATE: {ticker} already in paper_trades",
        }

    # CHECK 3 — Game already started
    # Edge discovery scans in-game markets (for behavioral gaps), but the paper trade
    # system only executes pre-game contracts. Skip games that have already started.
    if start:
        try:
            game_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if now_utc >= game_start:
                return {
                    "action": "SKIP",
                    "reason": f"PRE_FILTER_GAME_STARTED: game started at {start}",
                }
        except Exception:
            pass  # unparseable start time → don't block

    # CHECK 4 — Pinnacle line has moved sharply (if current price is available)
    # The research agent re-checks Pinnacle at its own 3% threshold. This pre-filter
    # hard-gates at 5% if the candidate already carries a freshly-fetched current price.
    v_prob_current = candidate.get("v_prob_current")
    if v_prob_current is not None:
        movement = abs(float(v_prob_current) - v_prob)
        if movement >= pinnacle_move_threshold:
            return {
                "action": "SKIP",
                "reason": (
                    f"PRE_FILTER_PINNACLE_MOVED: {movement:.1%} movement "
                    f"({v_prob:.1%} → {v_prob_current:.1%}) — sharp money reacting"
                ),
            }

    return {"action": "PROCEED", "reason": "passes all pre-filter checks"}
