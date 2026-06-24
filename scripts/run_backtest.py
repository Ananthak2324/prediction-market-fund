"""
CLI: Run the full gap analysis backtest across all sports.

Usage:
    python scripts/run_backtest.py --threshold 0.03
"""
import argparse
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from analysis.merger import merge_feeds
from analysis.backtest import run_backtest
from dashboard.charts import plot_gap_distribution, plot_win_rate_by_threshold, plot_sport_breakdown
from core.logger import logger

SPORTS = ["nba", "nfl", "mlb"]


def load_sport(sport: str) -> pd.DataFrame | None:
    kalshi_path = f"data/raw/kalshi_{sport}.csv"
    vegas_path = f"data/raw/vegas_{sport}.csv"
    if not os.path.exists(kalshi_path) or not os.path.exists(vegas_path):
        logger.warning(f"Missing data for {sport} — skipping")
        return None
    kalshi = pd.read_csv(kalshi_path)
    kalshi["kalshi_home_prob"] = kalshi["yes_price"] / 100.0
    vegas = pd.read_csv(vegas_path)
    merged = merge_feeds(kalshi, vegas, sport)
    merged["sport"] = sport
    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=0.03)
    args = parser.parse_args()

    frames = [load_sport(s) for s in SPORTS]
    frames = [f for f in frames if f is not None]
    if not frames:
        logger.error("No data found. Run fetch_odds_history.py first.")
        sys.exit(1)

    combined = pd.concat(frames, ignore_index=True)
    results = run_backtest(combined, threshold=args.threshold)

    if not results:
        logger.error("Backtest returned no results.")
        sys.exit(1)

    os.makedirs("outputs", exist_ok=True)
    plot_gap_distribution(combined)
    plot_win_rate_by_threshold(combined)
    plot_sport_breakdown(results["sport_breakdown"])

    from rich.console import Console
    from rich.table import Table
    console = Console()
    table = Table(title="Phase 1 Backtest Results")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    for k, v in results.items():
        if k != "sport_breakdown":
            table.add_row(str(k), str(v))
    console.print(table)
    console.print(results["sport_breakdown"].to_string(index=False))


if __name__ == "__main__":
    main()
