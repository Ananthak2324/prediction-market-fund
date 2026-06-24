"""
test_favorites_filter.py

Favorites filter: finds games where Vegas has a heavy favorite (>=65%)
and Kalshi underprices that favorite by 5%+, then measures how often
the Vegas-favored team actually wins.

Trade logic: BET YES on the Vegas favorite on Kalshi (they're underpriced).
  - vegas_correct = True  → profitable trade
  - vegas_correct = False → loss

Usage:
    python scripts/test_favorites_filter.py
    python scripts/test_favorites_filter.py --favorite-threshold 0.60
    python scripts/test_favorites_filter.py --gap-threshold 0.10
    python scripts/test_favorites_filter.py --verbose
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from scripts.backtest_gap import ALL_BOOKS, run

VEGAS_FAVORITE_THRESHOLD = 0.65
GAP_THRESHOLD            = 0.05


def build_dataframe(books: list[str] = ALL_BOOKS) -> pd.DataFrame:
    rows = run(books=books, min_gap=0.0)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # gap = k_prob - v_prob (negative means Vegas favors this team more than Kalshi)
    # For the favorites filter we want: v_prob high AND v_prob > k_prob
    df["v_minus_k"] = df["v_prob"] - df["k_prob"]   # positive = Vegas overweights vs Kalshi
    df["vegas_correct"] = df["won"]                  # did the team this row is about win?
    return df


def run_filter(df: pd.DataFrame,
               fav_threshold: float,
               gap_threshold: float,
               verbose: bool = False) -> pd.DataFrame:

    favorites = df[
        (df["v_prob"] >= fav_threshold) &
        (df["v_minus_k"] >= gap_threshold)
    ].copy()

    return favorites


def print_report(favorites: pd.DataFrame,
                 fav_threshold: float,
                 gap_threshold: float,
                 verbose: bool = False) -> None:

    print(f"\n{'═'*65}")
    print(f"  FAVORITES FILTER  (Vegas ≥{fav_threshold:.0%}, Kalshi gap ≥{gap_threshold:.0%})")
    print(f"  Trade: BET YES on the Vegas favorite where Kalshi underprices")
    print(f"{'═'*65}\n")

    if favorites.empty:
        print("  No games match the filter criteria.\n")
        return

    total     = len(favorites)
        # unique game-sides (same game/team can appear across multiple books)
    unique    = favorites.drop_duplicates(subset=["date","game","team"]).shape[0]
    win_rate  = favorites["vegas_correct"].mean()
    avg_gap   = favorites["v_minus_k"].mean()
    max_gap   = favorites["v_minus_k"].max()

    print(f"  Flagged rows (book×game-side) : {total}")
    print(f"  Unique game-sides             : {unique}  ({unique//2} games)")
    print(f"  Vegas correct (win rate)      : {win_rate:.1%}")
    print(f"  Average gap                   : {avg_gap:.1%}")
    print(f"  Max gap                       : {max_gap:.1%}")
    print()

    # ── Per-book breakdown ─────────────────────────────────────────────────────
    print(f"  {'Book':<14}  {'Sides':>5}  {'Win rate':>9}  {'Avg gap':>8}")
    print(f"  {'─'*44}")
    for book in ["pinnacle", "draftkings", "fanduel"]:
        sub = favorites[favorites["book"] == book]
        if sub.empty:
            continue
        rate    = sub["vegas_correct"].mean()
        avg_g   = sub["v_minus_k"].mean()
        bar     = "█" * round(rate * 10)
        print(f"  {book:<14}  {len(sub):>5}  {rate:>8.1%}  {avg_g:>7.1%}  {bar}")
    print()

    # ── Gap-size gradient ──────────────────────────────────────────────────────
    bins = [(0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.30), (0.30, 1.0)]
    print(f"  Gap gradient (does bigger gap = more Vegas accuracy?):")
    print(f"  {'Gap range':<12}  {'Sides':>5}  {'Win rate':>9}  Visual")
    print(f"  {'─'*50}")
    any_bucket = False
    for lo, hi in bins:
        sub = favorites[(favorites["v_minus_k"] >= lo) & (favorites["v_minus_k"] < hi)]
        if sub.empty:
            continue
        any_bucket = True
        rate  = sub["vegas_correct"].mean()
        bar   = "█" * round(rate * 10)
        label = f"{lo:.0%}–{hi:.0%}" if hi < 1.0 else f"{lo:.0%}+"
        print(f"  {label:<12}  {len(sub):>5}  {rate:>8.1%}  {bar}")
    if not any_bucket:
        print("  (no rows in any bucket)")
    print()

    # ── Vegas prob tiers (do higher prob favorites win more?) ──────────────────
    prob_bins = [(0.65, 0.75), (0.75, 0.85), (0.85, 1.0)]
    print(f"  Vegas prob tiers (how heavy is the favorite?):")
    print(f"  {'Vegas prob':<12}  {'Sides':>5}  {'Win rate':>9}  Visual")
    print(f"  {'─'*50}")
    for lo, hi in prob_bins:
        sub = favorites[(favorites["v_prob"] >= lo) & (favorites["v_prob"] < hi)]
        if sub.empty:
            continue
        rate  = sub["vegas_correct"].mean()
        bar   = "█" * round(rate * 10)
        label = f"{lo:.0%}–{hi:.0%}"
        print(f"  {label:<12}  {len(sub):>5}  {rate:>8.1%}  {bar}")
    print()

    # ── Full table ─────────────────────────────────────────────────────────────
    if verbose:
        print(f"  Full flagged game list:")
        hdr = f"  {'DATE':<12} {'GAME':<38} {'TEAM':<25} {'BOOK':<12} {'V%':>5} {'K%':>5} {'GAP':>7}  WIN?"
        print(hdr)
        print("  " + "─" * (len(hdr) - 2))
        for _, r in favorites.sort_values("v_minus_k", ascending=False).iterrows():
            won_str = "YES" if r["won"] else " NO"
            print(
                f"  {r['date']:<12} {r['game']:<38} {r['team']:<25} {r['book']:<12}"
                f"  {r['v_prob']:>4.1%}  {r['k_prob']:>4.1%}  {r['v_minus_k']:>+6.1%}  {won_str}"
            )
        print()

    # ── Verdict ────────────────────────────────────────────────────────────────
    print(f"  {'─'*65}")
    if total < 20:
        note = "Small sample — directionally interesting, not statistically conclusive."
    elif win_rate >= 0.70:
        note = "Strong signal. Filter is doing real work — Vegas favorites win at >70% when Kalshi lags."
    elif win_rate >= 0.60:
        note = "Moderate signal. Better than base rate; needs more data to confirm."
    else:
        note = "Weak signal. Filter not improving on base rate — revisit thresholds."

    print(f"  Sample: {total} rows ({unique//2} games)  |  Win rate: {win_rate:.1%}  |  {note}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--favorite-threshold", type=float, default=VEGAS_FAVORITE_THRESHOLD,
                        help=f"Vegas prob threshold for 'heavy favorite' (default: {VEGAS_FAVORITE_THRESHOLD})")
    parser.add_argument("--gap-threshold", type=float, default=GAP_THRESHOLD,
                        help=f"Min Vegas-Kalshi gap to flag (default: {GAP_THRESHOLD})")
    parser.add_argument("--verbose", action="store_true",
                        help="Print full table of flagged games")
    args = parser.parse_args()

    df        = build_dataframe()
    favorites = run_filter(df, args.favorite_threshold, args.gap_threshold)
    print_report(favorites, args.favorite_threshold, args.gap_threshold, verbose=args.verbose)


if __name__ == "__main__":
    main()
