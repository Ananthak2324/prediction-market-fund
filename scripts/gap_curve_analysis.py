"""
scripts/gap_curve_analysis.py

Reads data/gap_curves.db and produces:
  1. Normalized gap-vs-time curve plots, anchored two ways:
       - hours_since_open   (anchored to when the Kalshi market opened)
       - hours_to_game      (anchored to game start time, 0 = first pitch/tip)
  2. Non-monotonic pattern detection — flags gaps that reopen or spike

Run locally after syncing data/gap_curves.db from the VPS:
    bash scripts/sync_from_vps.sh
    python scripts/gap_curve_analysis.py
    python scripts/gap_curve_analysis.py --sport wnba --min-snapshots 5
    python scripts/gap_curve_analysis.py --output-dir ~/Desktop/charts
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")  # headless-safe; swap to "TkAgg" for interactive display
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd

BASE    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "gap_curves.db")


# ── Data Loading ──────────────────────────────────────────────────────────────

def load_gap_curves(sport: str | None = None, min_snapshots: int = 3) -> pd.DataFrame:
    """Load gap_curves.db, filter by sport, drop thinly-sampled markets."""
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}")
        print("Run: bash scripts/sync_from_vps.sh")
        return pd.DataFrame()

    conn = sqlite3.connect(DB_PATH)
    q    = "SELECT * FROM gap_curves WHERE 1=1"
    params: list = []
    if sport and sport.upper() != "ALL":
        q += " AND sport = ?"
        params.append(sport.upper())
    q += " ORDER BY market_ticker, snapshot_utc"
    df = pd.read_sql_query(q, conn, params=params)
    conn.close()

    if df.empty:
        return df

    # Drop markets with fewer than min_snapshots readings
    counts = df.groupby("market_ticker")["id"].count()
    keep   = counts[counts >= min_snapshots].index
    df     = df[df["market_ticker"].isin(keep)].copy()

    # Convert seconds → hours for readability
    df["hours_since_open"] = df["seconds_since_open"] / 3600
    df["hours_to_game"]    = df["seconds_to_close"]   / 3600

    return df


# ── Plotting ──────────────────────────────────────────────────────────────────

_DARK_BG   = "#0D1117"
_GRID_COL  = "#1F2937"
_TEXT_COL  = "#9CA3AF"
_MEAN_COL  = "#00C896"
_GAME_LINE = "#7F1D1D"
_THRESH    = "#065F46"


def _style_ax(ax: plt.Axes) -> None:
    ax.set_facecolor(_DARK_BG)
    ax.tick_params(colors=_TEXT_COL, labelsize=9)
    ax.yaxis.label.set_color(_TEXT_COL)
    ax.xaxis.label.set_color(_TEXT_COL)
    ax.title.set_color("#E5E7EB")
    for spine in ax.spines.values():
        spine.set_edgecolor(_GRID_COL)
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(xmax=1.0, decimals=1))
    ax.grid(color=_GRID_COL, linewidth=0.5, alpha=0.6)
    ax.axhline(0.05, color=_THRESH, linewidth=0.9, linestyle="--", label="5% threshold")


def _mean_band(ax: plt.Axes, x_series: pd.Series, y_series: pd.Series, n_bins: int = 30, min_count: int = 3) -> None:
    bins     = pd.cut(x_series, bins=n_bins)
    grouped  = y_series.groupby(bins, observed=False).agg(["mean", "std", "count"])
    grouped  = grouped[grouped["count"] >= min_count]
    x_pts    = [iv.mid for iv in grouped.index]
    ax.plot(x_pts, grouped["mean"], color=_MEAN_COL, linewidth=2.5, label="Mean", zorder=3)
    ax.fill_between(
        x_pts,
        grouped["mean"] - grouped["std"],
        grouped["mean"] + grouped["std"],
        color=_MEAN_COL, alpha=0.12, label="±1 SD",
    )


def plot_normalized_curves(df: pd.DataFrame, sport: str, output_dir: str = ".") -> str:
    """
    Two-panel figure:
      Left:  abs_gap vs hours_since_open (market-open anchor)
      Right: abs_gap vs hours_to_game    (game-start anchor, x=0 = tip-off)
    """
    if df.empty:
        print(f"  No data to plot for {sport.upper()}")
        return ""

    n_games = df["event_ticker"].nunique()
    n_sides = df["market_ticker"].nunique()

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.patch.set_facecolor(_DARK_BG)
    fig.suptitle(
        f"{sport.upper()} — Kalshi vs Pinnacle Full Gap Curve  "
        f"({n_games} games · {n_sides} market sides)",
        fontsize=13, fontweight="bold", color="#E5E7EB",
    )

    # ── Left: anchored to market open ─────────────────────────────────────────
    ax = axes[0]
    _style_ax(ax)
    ax.set_title("Anchored to Market Open", fontsize=11)
    ax.set_xlabel("Hours Since Market Open")
    ax.set_ylabel("|Gap|  (Kalshi − Pinnacle)")
    ax.axhline(0, color=_GRID_COL, linewidth=0.8)

    for _, grp in df.groupby("market_ticker"):
        grp = grp.sort_values("hours_since_open")
        ax.plot(grp["hours_since_open"], grp["abs_gap"],
                color="#10B981", alpha=0.18, linewidth=1)

    _mean_band(ax, df["hours_since_open"], df["abs_gap"])
    ax.legend(fontsize=9, facecolor="#111827", edgecolor=_GRID_COL, labelcolor=_TEXT_COL)

    # ── Right: anchored to game start ─────────────────────────────────────────
    ax = axes[1]
    _style_ax(ax)
    ax.set_title("Anchored to Game Start  (x = 0 → tip-off)", fontsize=11)
    ax.set_xlabel("Hours to Game  (right → game time)")
    ax.set_ylabel("|Gap|  (Kalshi − Pinnacle)")
    ax.axhline(0, color=_GRID_COL, linewidth=0.8)
    ax.axvline(0, color=_GAME_LINE, linewidth=0.9, linestyle=":", label="Game start", zorder=4)

    # Include up to 30 min after tip-off (settlement lag)
    pre = df[df["hours_to_game"] >= -0.5]
    for _, grp in pre.groupby("market_ticker"):
        grp = grp.sort_values("hours_to_game")
        ax.plot(grp["hours_to_game"], grp["abs_gap"],
                color="#10B981", alpha=0.18, linewidth=1)

    _mean_band(ax, pre["hours_to_game"], pre["abs_gap"])
    ax.invert_xaxis()  # time flows left → right toward game
    ax.legend(fontsize=9, facecolor="#111827", edgecolor=_GRID_COL, labelcolor=_TEXT_COL)

    plt.tight_layout()
    os.makedirs(output_dir, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fname    = os.path.join(output_dir, f"gap_curves_{sport.lower()}_{date_str}.png")
    plt.savefig(fname, dpi=140, facecolor=fig.get_facecolor())
    plt.close()
    print(f"  Chart saved → {fname}")
    return fname


# ── Non-Monotonic Detection ───────────────────────────────────────────────────

def detect_non_monotonic(df: pd.DataFrame) -> pd.DataFrame:
    """
    Flag market sides where the gap:
      NON-MONO : sign of delta changes ≥ 3 times (gap reverses direction repeatedly)
      SPIKE    : abs_gap at any point is > 1.5× the value 6 snapshots later
      BOTH     : both patterns present
    """
    flagged = []
    for ticker, grp in df.groupby("market_ticker"):
        grp  = grp.sort_values("snapshot_utc")
        gaps = grp["abs_gap"].values

        if len(gaps) < 5:
            continue

        deltas       = np.diff(gaps)
        sign_changes = int(np.sum(np.diff(np.sign(deltas)) != 0))
        is_non_mono  = sign_changes >= 3

        is_spike = False
        for i in range(len(gaps) - 6):
            if gaps[i] > 0.02 and gaps[i] > 1.5 * gaps[i + 6]:
                is_spike = True
                break

        if is_non_mono or is_spike:
            r = grp.iloc[0]
            flagged.append({
                "sport":         r["sport"],
                "game":          r["game"],
                "team":          r["team"],
                "market_ticker": ticker,
                "snapshots":     len(grp),
                "max_gap":       f"{gaps.max():.1%}",
                "final_gap":     f"{gaps[-1]:.1%}",
                "sign_changes":  sign_changes,
                "pattern": (
                    "BOTH"     if is_non_mono and is_spike
                    else ("NON-MONO" if is_non_mono else "SPIKE")
                ),
            })

    return pd.DataFrame(flagged) if flagged else pd.DataFrame()


# ── Summary Stats ─────────────────────────────────────────────────────────────

def print_summary(df: pd.DataFrame, sport: str) -> None:
    n_games = df["event_ticker"].nunique()
    n_sides = df["market_ticker"].nunique()
    n_rows  = len(df)

    print(f"  {n_games} games · {n_sides} market sides · {n_rows} snapshots")
    print(f"  Avg snapshots/side : {n_rows / n_sides:.1f}")
    print(f"  Gap range          : {df['abs_gap'].min():.1%} – {df['abs_gap'].max():.1%}  "
          f"(mean {df['abs_gap'].mean():.1%})")

    if "hours_since_open" in df.columns:
        max_hrs = df["hours_since_open"].max()
        print(f"  Max hours tracked  : {max_hrs:.1f}h since market open")

    # Rough count of sides that ever crossed the 5% trade threshold
    ever_flagged = df.groupby("market_ticker")["abs_gap"].max()
    n_ever_5pct  = (ever_flagged >= 0.05).sum()
    print(f"  Sides ever ≥ 5%    : {n_ever_5pct} / {n_sides}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Gap curve analysis")
    parser.add_argument("--sport",         default="all", choices=["mlb", "wnba", "all"],
                        help="Sport to analyse (default: all)")
    parser.add_argument("--min-snapshots", default=3, type=int,
                        help="Minimum snapshots per market to include (default: 3)")
    parser.add_argument("--output-dir",    default=".",
                        help="Directory to save chart PNGs (default: current dir)")
    args = parser.parse_args()

    sports = ["mlb", "wnba"] if args.sport == "all" else [args.sport]

    for sport in sports:
        print(f"\n{'═'*60}")
        print(f"  {sport.upper()} — GAP CURVE ANALYSIS")
        print(f"{'═'*60}")

        df = load_gap_curves(sport=sport, min_snapshots=args.min_snapshots)
        if df.empty:
            print(f"  No data yet for {sport.upper()} "
                  f"(need ≥ {args.min_snapshots} snapshots per market side)")
            continue

        print_summary(df, sport)
        plot_normalized_curves(df, sport, args.output_dir)

        flagged = detect_non_monotonic(df)
        if flagged.empty:
            print("  Non-monotonic patterns: none detected")
        else:
            print(f"\n  ⚠  Non-monotonic patterns ({len(flagged)} sides):")
            pd.set_option("display.max_columns", None)
            pd.set_option("display.width", 120)
            print(flagged.to_string(index=False))

    print()


if __name__ == "__main__":
    main()
