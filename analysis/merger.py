import pandas as pd
from analysis.normalizer import normalize_team
from core.logger import logger


def merge_feeds(kalshi_df: pd.DataFrame, vegas_df: pd.DataFrame, sport: str) -> pd.DataFrame:
    """
    Join Kalshi and Vegas DataFrames on game_date + home_team + away_team.
    Both DataFrames must already have those columns normalized to canonical team names.
    """
    for df, label in [(kalshi_df, "kalshi"), (vegas_df, "vegas")]:
        for col in ("home_team", "away_team"):
            df[col] = df[col].map(lambda x: normalize_team(x, sport))

    merged = pd.merge(
        kalshi_df, vegas_df,
        on=["game_date", "home_team", "away_team"],
        how="inner",
        suffixes=("_kalshi", "_vegas"),
    )

    total_kalshi = len(kalshi_df)
    match_rate = len(merged) / total_kalshi if total_kalshi else 0
    logger.info(f"{sport.upper()} merge: {len(merged)}/{total_kalshi} matched ({match_rate:.0%})")
    return merged
