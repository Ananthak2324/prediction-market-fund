"""
tests/test_agent_cost.py

Validates the three cost fixes on a hardcoded mock game — no paper trade
is logged, no snapshot data is touched.

Runs the research agent TWICE on the same game to confirm:
  Run 1: cache_read_tokens = 0  (cold cache — system prompt written to cache)
  Run 2: cache_read_tokens > 0  (warm cache — system prompt read from cache)

Expected total cost for both runs combined: ~$0.05

Usage:
    python tests/test_agent_cost.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.research_agent import run as agent_run

MOCK_GAME = {
    "home_team":     "Miami Marlins",
    "away_team":     "Texas Rangers",
    "game_time":     "2026-06-25T19:10:00Z",
    "sport":         "MLB",
    "kalshi_prob":   0.295,
    "pinnacle_prob": 0.55,
    "gap":           -0.255,
    "gap_direction": "kalshi_lower",
    "tier":          1,
    "snapshot_time": "2026-06-25T17:08:00Z",
    "date":          "2026-06-25",
    "side":          "HOME",
    "hours_before_game": 2.03,
    "timing_suspect": False,
}


def sep(char: str = "═", width: int = 60) -> None:
    print(char * width)


def run_test() -> None:
    sep()
    print("  AGENT COST TEST  —  2 runs on mock game")
    print("  Texas Rangers @ Miami Marlins  |  gap=-25.5%  tier=1")
    sep()

    results = []

    for run_num in (1, 2):
        print(f"\n{'─'*60}")
        print(f"  RUN {run_num}/2  ({'cold cache' if run_num == 1 else 'warm cache — expect cache_read_tokens > 0'})")
        print(f"{'─'*60}")

        verdict = agent_run(MOCK_GAME)

        # Cost fields come from _print_and_log_cost (already printed inline)
        # Just record the recommendation for display
        results.append({
            "run":            run_num,
            "recommendation": verdict.get("recommendation"),
            "confidence":     verdict.get("confidence"),
            "news_found":     verdict.get("news_found"),
            "agent_error":    verdict.get("_agent_error", False),
        })

    sep()
    print("\n  SUMMARY")
    print(f"  {'Run':<6} {'Recommendation':<14} {'Confidence':<12} {'News':<8} {'Error'}")
    print(f"  {'─'*55}")
    for r in results:
        print(
            f"  {r['run']:<6} {str(r['recommendation']):<14} "
            f"{str(r['confidence']):<12} {str(r['news_found']):<8} {r['agent_error']}"
        )

    print()
    print("  Cost log appended to: data/agent_cost_log.csv")
    print("  Check run 2 cache_read_tokens > 0 to confirm caching works.\n")


if __name__ == "__main__":
    run_test()
