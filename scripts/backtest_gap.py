"""
backtest_gap.py

Compare historical Kalshi prices against sportsbook closing lines and check
which side was more accurate.

Data sources:
  - data/raw/kalshi/mlb_markets.json
  - data/raw/vegas/mlb/*.json

Vegas price: EARLIEST captured_at row per outcome per game (avoids in-game
live-betting lines that contaminate later captures in the same file).

Usage:
    python scripts/backtest_gap.py                   # all books
    python scripts/backtest_gap.py --book pinnacle
    python scripts/backtest_gap.py --book draftkings
    python scripts/backtest_gap.py --book fanduel
    python scripts/backtest_gap.py --min-gap 0.03 --verbose
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils import remove_vig
from core.desk_loader import get_desk

# This script is permanently scoped to MLB (its data paths are MLB-specific
# static files) — there's no --sport/--desk selector to add. It reads its
# alias map from desks/mlb.yaml (teams.alias_map), the same canonical source
# used by every other consumer of the once-diverged KALSHI_ALIAS dicts.
KALSHI_FILE = "data/raw/kalshi/mlb_markets.json"
VEGAS_GLOB  = "data/raw/vegas/mlb/*.json"
ALL_BOOKS   = ["pinnacle", "draftkings", "fanduel"]
MIN_DATE    = "2026-05-13"

_MLB_DESK = get_desk("MLB")


# ── helpers ───────────────────────────────────────────────────────────────────

def normalise(sub: str) -> str:
    return _MLB_DESK.alias_map.get(sub, sub)


def match_team(kalshi_sub: str, vegas_teams: list[str]) -> str | None:
    kw = normalise(kalshi_sub).lower()
    for t in vegas_teams:
        if kw in t.lower():
            return t
    return None


# ── load Kalshi events ────────────────────────────────────────────────────────

def load_kalshi_events() -> list[dict]:
    with open(KALSHI_FILE) as f:
        markets = json.load(f)

    by_event: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        by_event[m["event_ticker"]].append(m)

    events = []
    for sides in by_event.values():
        if len(sides) < 2:
            continue
        if all(
            float(s.get("previous_yes_bid_dollars") or 0) > 0.01 and
            float(s.get("previous_yes_ask_dollars") or 0) > 0.01
            for s in sides
        ):
            date = sides[0]["close_time"][:10]
            if date >= MIN_DATE:
                events.append({"date": date, "sides": sides})

    return events


# ── load Vegas (earliest capture per outcome, per book) ───────────────────────

def load_vegas_by_date(books: list[str]) -> dict[str, dict[tuple, dict[str, dict]]]:
    """
    Returns: {date: {(home, away): {outcome_name: {book: price, ...}}}}
    For each outcome we keep the EARLIEST captured_at row per book.
    """
    # raw[date][(home,away)][outcome][book] = [(captured_at, price), ...]
    raw: dict = defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(lambda: defaultdict(list))
        )
    )

    for f in glob.glob(VEGAS_GLOB):
        date = os.path.basename(f)[:10]
        with open(f) as fh:
            rows = json.load(fh)
        for r in rows:
            book = r.get("book", "")
            if book not in books:
                continue
            key = (r["home_team"], r["away_team"])
            raw[date][key][r["outcome_name"]][book].append(
                (r["captured_at"], r["price"])
            )

    # Collapse to earliest price per outcome per book
    result: dict = {}
    for date, games in raw.items():
        result[date] = {}
        for game_key, outcomes in games.items():
            outcome_prices: dict[str, dict[str, int]] = {}
            for outcome, book_caps in outcomes.items():
                outcome_prices[outcome] = {
                    book: min(caps, key=lambda x: x[0])[1]
                    for book, caps in book_caps.items()
                }
            if len(outcome_prices) >= 2:
                result[date][game_key] = outcome_prices

    return result


# ── backtest ──────────────────────────────────────────────────────────────────

def run(books: list[str], min_gap: float = 0.0) -> list[dict]:
    events = load_kalshi_events()
    vegas  = load_vegas_by_date(books)

    rows = []

    for ev in events:
        date  = ev["date"]
        sides = ev["sides"]

        if date not in vegas:
            continue

        k_names = [s["yes_sub_title"] for s in sides]

        for (home_v, away_v), outcome_prices in vegas[date].items():
            both_v  = [home_v, away_v]
            mapping = {}
            for ks in k_names:
                m = match_team(ks, both_v)
                if m:
                    mapping[ks] = m
            if len(mapping) != 2 or len(set(mapping.values())) != 2:
                continue

            # For each available book, compute gap and record result
            for book in books:
                home_price = outcome_prices.get(home_v, {}).get(book)
                away_price = outcome_prices.get(away_v, {}).get(book)
                if home_price is None or away_price is None:
                    continue

                vegas_home_prob, vegas_away_prob = remove_vig(home_price, away_price)

                for s in sides:
                    vs_name = mapping.get(s["yes_sub_title"])
                    if not vs_name:
                        continue
                    is_home  = (vs_name == home_v)
                    v_prob   = vegas_home_prob if is_home else vegas_away_prob

                    bid    = float(s["previous_yes_bid_dollars"])
                    ask    = float(s["previous_yes_ask_dollars"])
                    k_prob = (bid + ask) / 2.0
                    gap    = k_prob - v_prob

                    if abs(gap) < min_gap:
                        continue

                    won           = (s["result"] == "yes")
                    kalshi_right  = ((k_prob > v_prob) == won)

                    rows.append({
                        "date":         date,
                        "game":         f"{away_v} @ {home_v}",
                        "team":         vs_name,
                        "book":         book,
                        "k_prob":       k_prob,
                        "v_prob":       v_prob,
                        "gap":          gap,
                        "abs_gap":      abs(gap),
                        "won":          won,
                        "kalshi_right": kalshi_right,
                    })
            break  # matched this event, move on

    return rows


# ── output ────────────────────────────────────────────────────────────────────

def print_results(rows: list[dict], verbose: bool = False) -> None:
    if not rows:
        print("No matched games found.")
        return

    rows_sorted = sorted(rows, key=lambda x: x["abs_gap"], reverse=True)

    if verbose:
        hdr = f"{'DATE':<12} {'GAME':<38} {'TEAM':<25} {'BOOK':<12} {'K%':>5} {'V%':>5} {'GAP':>7}  WON  VERDICT"
        print(hdr)
        print("─" * len(hdr))
        for r in rows_sorted:
            verdict = "KALSHI ✓" if r["kalshi_right"] else "VEGAS  ✓"
            won_str = "YES" if r["won"] else " NO"
            print(
                f"{r['date']:<12} {r['game']:<38} {r['team']:<25} {r['book']:<12}"
                f"  {r['k_prob']:>4.1%}  {r['v_prob']:>4.1%}  {r['gap']:>+6.1%}  {won_str}  {verdict}"
            )
        print()

    # Unique game-sides (deduplicate across books for top-level count)
    unique_sides = {(r["date"], r["game"], r["team"]): r for r in rows_sorted}
    n_sides = len(unique_sides)
    n_games = n_sides // 2

    print(f"{'═'*65}")
    print(f"  BACKTEST RESULTS  (Kalshi vs Sportsbooks, MLB 2026)")
    print(f"{'═'*65}")
    print(f"  Matched game-sides (unique) : {n_sides}  ({n_games} games)")
    print(f"  Total rows (sides × books)  : {len(rows_sorted)}")
    print()

    # Per-book breakdown
    books_present = sorted(set(r["book"] for r in rows_sorted))
    print(f"  {'Book':<14}  {'Sides':>5}  {'Vegas%':>7}  {'Kalshi%':>8}  {'Avg|gap|':>9}")
    print(f"  {'─'*50}")
    for book in books_present:
        br = [r for r in rows_sorted if r["book"] == book]
        k_right = sum(1 for r in br if r["kalshi_right"])
        v_right = len(br) - k_right
        avg_gap = sum(r["abs_gap"] for r in br) / len(br)
        print(
            f"  {book:<14}  {len(br):>5}  {v_right/len(br)*100:>6.0f}%  "
            f"{k_right/len(br)*100:>7.0f}%  {avg_gap:>8.1%}"
        )

    # Combined
    k_right_all = sum(1 for r in rows_sorted if r["kalshi_right"])
    v_right_all = len(rows_sorted) - k_right_all
    avg_all     = sum(r["abs_gap"] for r in rows_sorted) / len(rows_sorted)
    print(f"  {'─'*50}")
    print(
        f"  {'ALL':<14}  {len(rows_sorted):>5}  {v_right_all/len(rows_sorted)*100:>6.0f}%  "
        f"{k_right_all/len(rows_sorted)*100:>7.0f}%  {avg_all:>8.1%}"
    )
    print()

    # Gap-bucket breakdown (all books combined)
    buckets = [("0–3%", 0.0, 0.03), ("3–5%", 0.03, 0.05),
               ("5–10%", 0.05, 0.10), ("10%+", 0.10, 1.0)]
    print(f"  Gap-size breakdown (all books):")
    print(f"  {'Gap range':<10}  {'Sides':>5}  {'Vegas%':>7}  {'Kalshi%':>8}")
    print(f"  {'─'*38}")
    for label, lo, hi in buckets:
        bucket = [r for r in rows_sorted if lo <= r["abs_gap"] < hi]
        if not bucket:
            continue
        k_r = sum(1 for r in bucket if r["kalshi_right"])
        v_r = len(bucket) - k_r
        print(f"  {label:<10}  {len(bucket):>5}  {v_r/len(bucket)*100:>6.0f}%  {k_r/len(bucket)*100:>7.0f}%")
    print()

    max_row = rows_sorted[0]
    print(
        f"  Largest gap : {max_row['gap']:+.1%}  ({max_row['team']}  "
        f"{max_row['game']}  {max_row['date']}  [{max_row['book']}])"
    )
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--book",
        choices=ALL_BOOKS + ["all"],
        default="all",
        help="Which sportsbook to compare against (default: all)",
    )
    parser.add_argument(
        "--min-gap", type=float, default=0.0,
        help="Only include rows where |gap| >= this value",
    )
    parser.add_argument("--verbose", action="store_true",
                        help="Print full game-by-game table")
    args = parser.parse_args()

    books = ALL_BOOKS if args.book == "all" else [args.book]
    rows  = run(books=books, min_gap=args.min_gap)
    print_results(rows, verbose=args.verbose)


if __name__ == "__main__":
    main()
