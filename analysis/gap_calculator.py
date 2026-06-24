import pandas as pd

GAP_THRESHOLD = 0.03


def compute_gaps(merged: pd.DataFrame) -> pd.DataFrame:
    df = merged.copy()
    df["gap"] = df["kalshi_home_prob"] - df["vegas_home_prob"]
    df["abs_gap"] = df["gap"].abs()
    df["gap_direction"] = df["gap"].apply(
        lambda g: "kalshi_higher" if g > 0 else "vegas_higher"
    )
    df["flagged"] = df["abs_gap"] >= GAP_THRESHOLD
    return df


def flag_tradeable(df: pd.DataFrame, threshold: float | None = None) -> pd.DataFrame:
    t = threshold if threshold is not None else GAP_THRESHOLD
    return df[df["abs_gap"] >= t].copy()
