"""
scripts/test_agent.py

Tests research_agent.run() on the 3 most recently logged paper trades.
Prints: raw agent response, parsed verdict, final recommendation,
and what would be logged to the database.

Usage:
    python scripts/test_agent.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.research_agent import run as agent_run

TRADES_FILE = "data/paper_trades.json"


def build_game_dict(trade: dict) -> dict:
    """Map a paper_trades.json entry to the game_dict format research_agent expects."""
    game  = trade.get("game", "")
    parts = game.split(" @ ")
    away  = parts[0].strip() if len(parts) == 2 else ""
    home  = parts[1].strip() if len(parts) == 2 else ""

    abs_gap   = trade.get("abs_gap", abs(trade.get("gap", 0)))
    tier      = 1 if abs_gap >= 0.10 else 2
    start_utc = trade.get("start_utc", "")
    date      = start_utc[:10] if start_utc else ""

    return {
        "home_team":         home,
        "away_team":         away,
        "team":              trade.get("team", ""),
        "side":              trade.get("side", "HOME"),
        "game_time":         start_utc,
        "sport":             trade.get("sport", "MLB"),
        "kalshi_prob":       trade.get("k_prob", 0),
        "pinnacle_prob":     trade.get("v_prob", 0),
        "gap":               trade.get("gap", 0),
        "abs_gap":           abs_gap,
        "gap_direction":     "kalshi_lower" if trade.get("gap", 0) < 0 else "kalshi_higher",
        "tier":              tier,
        "signal":            trade.get("signal", ""),
        "snapshot_time":     trade.get("snapshot_time", ""),
        "date":              date,
        "hours_before_game": trade.get("hours_before_game"),
        "timing_suspect":    trade.get("timing_suspect", False),
    }


def sep(char: str = "═", width: int = 80) -> None:
    print(char * width)


def run_test() -> None:
    with open(TRADES_FILE) as f:
        all_trades = json.load(f)

    # 3 most recently snapped trades
    sorted_trades = sorted(
        all_trades, key=lambda t: t.get("snapshot_time", ""), reverse=True
    )
    test_trades = sorted_trades[:3]

    sep()
    print(f"  RESEARCH AGENT TEST  —  {len(test_trades)} trades")
    sep()

    for i, trade in enumerate(test_trades, 1):
        game_dict = build_game_dict(trade)

        print(f"\n[{i}/{len(test_trades)}]  {trade['game']}")
        print(f"  Signal:   {trade['signal']}  |  Gap: {trade['gap']:+.1%}  |  Tier: {game_dict['tier']}")
        print(f"  Kalshi:   {trade['k_prob']:.1%}  |  Pinnacle: {trade['v_prob']:.1%}")
        print(f"  Game:     {trade['start_utc']}")
        hours = trade.get("hours_before_game", "?")
        print(f"  Snapshot: {trade['snapshot_time']}  ({hours}h before)  suspect={trade.get('timing_suspect')}")

        print(f"\n  Running agent...")
        sep("─")

        verdict = agent_run(game_dict)
        raw     = verdict.pop("_raw_response", "(no raw response captured)")

        print("\n  RAW AGENT RESPONSE:")
        for line in raw.splitlines():
            print(f"    {line}")

        print("\n  PARSED VERDICT:")
        for k, v in verdict.items():
            if not k.startswith("_"):
                print(f"    {k:<24}  {json.dumps(v)}")

        rec = verdict.get("recommendation", "MONITOR")
        print(f"\n  FINAL RECOMMENDATION:  {rec}")
        print(f"  CONFIDENCE:            {verdict.get('confidence', 'UNKNOWN')}")
        print(f"  GAP TYPE:              {verdict.get('gap_type', 'UNKNOWN')}")

        print("\n  WHAT WOULD BE LOGGED:")
        if rec == "SKIP":
            print(f"    status            = SKIPPED")
            print(f"    outcome           = SKIPPED  (excluded from win rate)")
            print(f"    agent_verdict     = SKIP")
            print(f"    agent_confidence  = N/A")
            print(f"    news_found        = {verdict.get('news_found')}")
            print(f"    news_detail       = {verdict.get('news_detail')}")
            print(f"    agent_reasoning   = {verdict.get('reasoning')}")
        else:
            print(f"    status            = OPEN")
            print(f"    outcome           = null  (pending resolution)")
            print(f"    agent_verdict     = {rec}")
            print(f"    agent_confidence  = {verdict.get('confidence')}")
            print(f"    agent_reasoning   = {verdict.get('reasoning')}")
            print(f"    gap_explanation   = {verdict.get('gap_explanation')}")
            print(f"    news_found        = {verdict.get('news_found')}")
            print(f"    pinnacle_stable   = {verdict.get('pinnacle_stable')}")
            print(f"    pinnacle_movement = {verdict.get('pinnacle_movement')}")

        sep()

    print("\nTest complete.\n")


if __name__ == "__main__":
    run_test()
