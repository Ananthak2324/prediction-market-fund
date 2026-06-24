import pandas as pd
from scipy import stats
from analysis.gap_calculator import compute_gaps, flag_tradeable
from core.logger import logger


def run_backtest(merged: pd.DataFrame, threshold: float | None = None) -> dict:
    df = compute_gaps(merged)
    flagged = flag_tradeable(df, threshold)

    if flagged.empty:
        logger.warning("No flagged games — threshold may be too high")
        return {}

    # Vegas is correct when it disagrees with Kalshi's direction and the outcome proves it right
    flagged = flagged.copy()
    flagged["vegas_correct"] = (
        ((flagged["gap"] > 0) & (flagged["result"] == "away_win")) |
        ((flagged["gap"] < 0) & (flagged["result"] == "home_win"))
    )

    wins = flagged["vegas_correct"].sum()
    n = len(flagged)
    win_rate = wins / n

    p_value = stats.binomtest(int(wins), n=n, p=0.5, alternative="greater").pvalue

    sport_breakdown = (
        flagged.groupby("sport")["vegas_correct"]
        .agg(games="count", win_rate="mean")
        .reset_index()
    )

    gap_coverage = len(flagged) / len(df)

    results = {
        "total_games": len(df),
        "flagged_games": n,
        "gap_coverage_pct": round(gap_coverage * 100, 1),
        "win_rate": round(win_rate, 4),
        "wins": int(wins),
        "p_value": round(p_value, 4),
        "sport_breakdown": sport_breakdown,
    }

    logger.info(f"Backtest complete: {n} flagged games, {win_rate:.1%} win rate, p={p_value:.4f}")
    return results
