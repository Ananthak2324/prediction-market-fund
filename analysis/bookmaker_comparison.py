"""
analysis/bookmaker_comparison.py

Historical multi-book edge analysis: Pinnacle vs DraftKings vs FanDuel vs Kalshi.

Sections:
  A. Book vig comparison       — how much juice each book charges (pre-game only)
  B. Kalshi vs each book gaps  — gap size and win rate (~23 games with valid Kalshi price)
  C. Cross-book divergence     — cases where Pinnacle and DK disagree
  D. OddsPortal calibration    — BetMGM implied prob vs actual win rate (2024-2025, 2.4k games)

Data used:
  data/raw/vegas/mlb/*.json    — TheOddsAPI historical captures (Pinnacle, DK, FanDuel)
  data/raw/kalshi/mlb_markets.json  — settled Kalshi markets with previous_price
  data/raw/vegas_mlb.csv       — OddsPortal historical games (BetMGM, 2024-2025)

Usage:
    python analysis/bookmaker_comparison.py
    python analysis/bookmaker_comparison.py --save
"""
import argparse
import ast
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.utils import american_to_prob, remove_vig, ticker_to_utc
from scripts.snapshot_gaps import match_team

BASE          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VEGAS_MLB_DIR = os.path.join(BASE, "data", "raw", "vegas", "mlb")
KALSHI_FILE   = os.path.join(BASE, "data", "raw", "kalshi", "mlb_markets.json")
ODDSPORTAL    = os.path.join(BASE, "data", "raw", "vegas_mlb.csv")
OUTPUT_PATH   = os.path.join(BASE, "outputs", "bookmaker_analysis.json")

KALSHI_VALID_DIFF  = 0.05          # min |previous_price - last_price| to count as pre-game
KALSHI_MID_RANGE   = (0.10, 0.90)  # valid pre-game price range; near-0/1 = near-settled noise
GAP_MIN            = 0.05          # minimum gap to count as a trade candidate

MLB_TEAMS = [
    "Arizona Diamondbacks", "Athletics", "Atlanta Braves", "Baltimore Orioles",
    "Boston Red Sox", "Chicago Cubs", "Chicago White Sox", "Cincinnati Reds",
    "Cleveland Guardians", "Colorado Rockies", "Detroit Tigers", "Houston Astros",
    "Kansas City Royals", "Los Angeles Angels", "Los Angeles Dodgers",
    "Miami Marlins", "Milwaukee Brewers", "Minnesota Twins", "New York Mets",
    "New York Yankees", "Philadelphia Phillies", "Pittsburgh Pirates",
    "San Diego Padres", "San Francisco Giants", "Seattle Mariners",
    "St. Louis Cardinals", "Tampa Bay Rays", "Texas Rangers", "Toronto Blue Jays",
    "Washington Nationals",
]


# ── data loading ──────────────────────────────────────────────────────────────

def load_vegas_rows() -> list[dict]:
    rows = []
    for path in sorted(glob.glob(os.path.join(VEGAS_MLB_DIR, "*.json"))):
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, list):
                rows.extend(data)
        except Exception:
            pass
    return rows


def load_kalshi_markets() -> list[dict]:
    with open(KALSHI_FILE) as f:
        return json.load(f)


def _parse_dt(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc)


# ── section A: book vig comparison ────────────────────────────────────────────

def compute_vig_analysis(rows: list[dict]) -> dict:
    """
    For each book, report average vig on pre-game captures.
    Groups rows by (event_id, book, captured_at) to find matched home+away pairs.
    """
    # Build (event_id, book, captured_at) -> {outcome_name: price}
    snapshots: dict[tuple, dict] = defaultdict(dict)
    meta: dict[str, dict] = {}  # event_id -> {start_time, home_team, away_team}

    for r in rows:
        key = (r["event_id"], r["book"], r["captured_at"])
        snapshots[key][r["outcome_name"]] = r["price"]
        if r["event_id"] not in meta:
            meta[r["event_id"]] = {
                "start_time": r["start_time"],
                "home_team":  r["home_team"],
                "away_team":  r["away_team"],
            }

    # For each event+book, find latest pre-game snapshot with both outcomes
    best: dict[tuple, dict] = {}  # (event_id, book) -> {outcomes, captured_at}

    for (event_id, book, captured_at), outcomes in snapshots.items():
        if len(outcomes) < 2:
            continue
        try:
            cap_dt   = _parse_dt(captured_at)
            start_dt = _parse_dt(meta[event_id]["start_time"])
            if cap_dt >= start_dt:
                continue
        except Exception:
            continue

        k = (event_id, book)
        if k not in best or captured_at > best[k]["captured_at"]:
            best[k] = {"outcomes": outcomes, "captured_at": captured_at, "event_id": event_id}

    # Compute vig per snapshot
    by_book: dict[str, list[float]] = defaultdict(list)
    for (event_id, book), snap in best.items():
        prices = list(snap["outcomes"].values())
        if len(prices) < 2:
            continue
        vig = american_to_prob(prices[0]) + american_to_prob(prices[1]) - 1.0
        by_book[book].append(vig)

    result = {}
    for book in sorted(by_book):
        vigs = sorted(by_book[book])
        n    = len(vigs)
        result[book] = {
            "n_games":    n,
            "avg_vig":    round(sum(vigs) / n, 4),
            "median_vig": round(vigs[n // 2], 4),
            "min_vig":    round(vigs[0], 4),
            "max_vig":    round(vigs[-1], 4),
        }
    return result


# ── section B + C: join Kalshi to Vegas for gap analysis ─────────────────────

def _build_vegas_index(rows: list[dict]) -> dict:
    """Index Vegas rows by (frozenset({home, away}), game_date_utc)."""
    index: dict[tuple, dict] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        try:
            game_date = _parse_dt(r["start_time"]).date()
        except Exception:
            continue
        key = (frozenset({r["home_team"], r["away_team"]}), game_date)
        index[key][r["book"]].append(r)
    return dict(index)


def _build_kalshi_events(markets: list[dict]) -> list[dict]:
    """Group Kalshi markets by event_ticker; extract pre-game price and result."""
    by_event: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        et = m.get("event_ticker", "")
        if et.startswith("KXMLBGAME"):
            by_event[et].append(m)

    events = []
    for et, sides in by_event.items():
        if len(sides) != 2:
            continue
        start_utc = ticker_to_utc(et)
        if not start_utc:
            continue

        side_data = []
        for m in sides:
            try:
                prev      = float(m.get("previous_price_dollars") or 0)
                last      = float(m.get("last_price_dollars") or 0)
                prev_bid  = float(m.get("previous_yes_bid_dollars") or 0)
                prev_ask  = float(m.get("previous_yes_ask_dollars") or 0)
                valid     = abs(prev - last) >= KALSHI_VALID_DIFF
                mid       = (prev_bid + prev_ask) / 2 if valid and prev_bid and prev_ask else None
                sub       = m.get("yes_sub_title", "")
                team      = match_team(sub, MLB_TEAMS)
                result    = m.get("result")  # "yes" / "no"
            except Exception:
                continue
            side_data.append({"sub": sub, "team": team, "mid": mid, "result": result, "valid": valid})

        if len(side_data) != 2:
            continue
        teams = [s["team"] for s in side_data if s["team"]]
        events.append({
            "event_ticker": et,
            "start_utc":    start_utc,
            "game_date":    start_utc.date(),
            "team_set":     frozenset(teams) if len(teams) == 2 else None,
            "sides":        side_data,
        })
    return events


def _get_book_probs(vegas_rows_by_book: dict, start_utc: datetime) -> dict:
    """For each book: find latest pre-game snapshot and return {team: vf_prob, vig, odds}."""
    result = {}
    for book, rows_list in vegas_rows_by_book.items():
        # Filter to pre-game
        pre = [r for r in rows_list if _parse_dt(r["captured_at"]) < start_utc]
        if not pre:
            continue

        # Group by captured_at, take latest
        by_cap: dict[str, dict] = defaultdict(dict)
        home_team = pre[0]["home_team"]
        away_team = pre[0]["away_team"]
        for r in pre:
            by_cap[r["captured_at"]][r["outcome_name"]] = r["price"]

        latest   = max(by_cap.keys())
        outcomes = by_cap[latest]

        if home_team not in outcomes or away_team not in outcomes:
            continue

        h_odds, a_odds = outcomes[home_team], outcomes[away_team]
        h_vf, a_vf    = remove_vig(h_odds, a_odds)
        vig           = american_to_prob(h_odds) + american_to_prob(a_odds) - 1.0

        result[book] = {
            home_team: h_vf,
            away_team: a_vf,
            "_vig":       round(vig, 4),
            "_home_odds": h_odds,
            "_away_odds": a_odds,
            "_home_team": home_team,
            "_away_team": away_team,
        }
    return result


def join_kalshi_to_vegas(kalshi_events: list[dict], vegas_index: dict) -> list[dict]:
    joined = []
    for event in kalshi_events:
        if not event["team_set"]:
            continue

        # Find matching Vegas game (try same date, then ±1 day for UTC boundary edge cases)
        book_probs = None
        for delta in [0, 1, -1]:
            key = (event["team_set"], event["game_date"] + timedelta(days=delta))
            if key in vegas_index:
                book_probs = _get_book_probs(vegas_index[key], event["start_utc"])
                if book_probs:
                    break

        if not book_probs:
            continue

        # Determine canonical home/away from first book found
        first_book = next(iter(book_probs.values()))
        home_team  = first_book["_home_team"]
        away_team  = first_book["_away_team"]

        for side in event["sides"]:
            if not side["team"] or not side["mid"]:
                continue
            if not (KALSHI_MID_RANGE[0] <= side["mid"] <= KALSHI_MID_RANGE[1]):
                continue  # near-settled price, not a real pre-game signal

            gaps = {}
            for book, probs in book_probs.items():
                book_vf = probs.get(side["team"])
                if book_vf is not None:
                    gap = side["mid"] - book_vf
                    gaps[book] = {
                        "gap":      round(gap, 4),
                        "abs_gap":  round(abs(gap), 4),
                        "book_vf":  round(book_vf, 4),
                        "book_vig": probs["_vig"],
                    }

            if gaps:
                joined.append({
                    "event_ticker": event["event_ticker"],
                    "game_date":    str(event["game_date"]),
                    "game":         f"{away_team} @ {home_team}",
                    "team":         side["team"],
                    "side":         "HOME" if side["team"] == home_team else "AWAY",
                    "kalshi_mid":   round(side["mid"], 4),
                    "result":       side["result"],
                    "gaps":         gaps,
                })
    return joined


def compute_kalshi_gaps(joined: list[dict]) -> dict:
    by_book: dict[str, dict] = {}

    for record in joined:
        result_val = record.get("result")
        if not result_val:
            continue
        for book, gd in record["gaps"].items():
            gap    = gd["gap"]
            signal = "BUY_YES" if gap < 0 else "BUY_NO"
            won    = (signal == "BUY_YES" and result_val == "yes") or \
                     (signal == "BUY_NO"  and result_val == "no")
            entry  = {
                "game":       record["game"],
                "team":       record["team"],
                "kalshi_mid": record["kalshi_mid"],
                "book_vf":    gd["book_vf"],
                "gap":        gap,
                "signal":     signal,
                "result":     result_val,
                "won":        won,
            }
            if book not in by_book:
                by_book[book] = {"all": [], "tier_a": [], "tier_b": [], "tier_c": []}
            by_book[book]["all"].append(entry)
            if gd["abs_gap"] >= 0.15:
                by_book[book]["tier_c"].append(entry)
            elif gd["abs_gap"] >= 0.10:
                by_book[book]["tier_b"].append(entry)
            elif gd["abs_gap"] >= 0.05:
                by_book[book]["tier_a"].append(entry)

    summary = {}
    for book, tiers in by_book.items():
        summary[book] = {}
        for tier, entries in tiers.items():
            n    = len(entries)
            wins = sum(1 for e in entries if e["won"])
            summary[book][tier] = {
                "n":        n,
                "wins":     wins,
                "win_rate": round(wins / n, 4) if n > 0 else None,
                "avg_abs_gap": round(sum(abs(e["gap"]) for e in entries) / n, 4) if n else None,
                "trades":   [{"game": e["game"], "team": e["team"], "gap": round(e["gap"], 4),
                              "signal": e["signal"], "result": e["result"], "won": e["won"]}
                             for e in entries],
                "note":     "SMALL SAMPLE — directional only" if n < 50 else "",
            }
    return summary


def compute_book_divergence(joined: list[dict]) -> dict:
    records = []
    for r in joined:
        if "pinnacle" in r["gaps"] and "draftkings" in r["gaps"]:
            pin_vf = r["gaps"]["pinnacle"]["book_vf"]
            dk_vf  = r["gaps"]["draftkings"]["book_vf"]
            div    = abs(pin_vf - dk_vf)
            records.append({
                "game":       r["game"],
                "team":       r["team"],
                "kalshi_mid": r["kalshi_mid"],
                "pin_vf":     pin_vf,
                "dk_vf":      dk_vf,
                "divergence": round(div, 4),
                "dk_higher":  dk_vf > pin_vf,
                "result":     r["result"],
            })

    if not records:
        return {"n": 0, "note": "No games with both Pinnacle and DraftKings pre-game data"}

    n         = len(records)
    avg_div   = sum(r["divergence"] for r in records) / n
    dk_higher = sum(1 for r in records if r["dk_higher"])
    return {
        "n":                  n,
        "avg_divergence":     round(avg_div, 4),
        "dk_higher_pct":      round(dk_higher / n, 4),
        "note":               "Small sample — interpret directionally only",
        "games":              records,
    }


# ── section D: OddsPortal calibration ────────────────────────────────────────

def compute_oddsportal_calibration() -> dict:
    df = pd.read_csv(ODDSPORTAL)
    records = []
    for _, row in df.iterrows():
        try:
            market = ast.literal_eval(str(row["1x2_market"]))
            home_dec = away_dec = None
            for entry in market:
                if "BetMGM" in str(entry.get("bookmaker_name", "")):
                    home_dec = float(entry["1"])
                    away_dec = float(entry["2"])
                    break
            if not home_dec or not away_dec or home_dec <= 1 or away_dec <= 1:
                continue
            # 2-way renorm, ignoring the draw line
            home_raw = 1 / home_dec
            away_raw = 1 / away_dec
            home_vf  = home_raw / (home_raw + away_raw)
            home_won = int(row["home_score"]) > int(row["away_score"])
            records.append({"home_vf": home_vf, "home_won": int(home_won)})
        except Exception:
            continue

    buckets = [
        (0.00, 0.48, "<48%"),
        (0.48, 0.52, "48-52%"),
        (0.52, 0.56, "52-56%"),
        (0.56, 0.60, "56-60%"),
        (0.60, 0.65, "60-65%"),
        (0.65, 1.01, "65%+"),
    ]
    calibration = []
    for lo, hi, label in buckets:
        bucket = [r for r in records if lo <= r["home_vf"] < hi]
        if not bucket:
            continue
        n          = len(bucket)
        avg_imp    = sum(r["home_vf"] for r in bucket) / n
        actual_win = sum(r["home_won"] for r in bucket) / n
        calibration.append({
            "bucket":           label,
            "n":                n,
            "avg_implied_prob": round(avg_imp, 4),
            "actual_win_rate":  round(actual_win, 4),
            "diff":             round(actual_win - avg_imp, 4),
        })

    total  = len(records)
    brier  = sum((r["home_vf"] - r["home_won"]) ** 2 for r in records) / total if total else None
    return {
        "bookmaker":  "BetMGM",
        "n_games":    total,
        "brier_score": round(brier, 4) if brier else None,
        "calibration": calibration,
        "note":       "2024-2025 season, OddsPortal historical data. No Kalshi price overlap.",
    }


# ── printing ──────────────────────────────────────────────────────────────────

def print_report(report: dict) -> None:
    meta = report["metadata"]
    print(f"\n{'═'*70}")
    print(f"  BOOKMAKER COMPARISON ANALYSIS")
    print(f"  Generated: {meta['generated_at']}")
    print(f"  Vegas files: {meta['vegas_files_loaded']}  |  "
          f"Vegas rows: {meta['vegas_rows_loaded']}")
    print(f"  Kalshi markets: {meta['kalshi_markets_loaded']}")
    print(f"{'═'*70}")

    # Section A
    print(f"\n[A] BOOK VIG COMPARISON (pre-game only)")
    print(f"  {'Book':<14} {'Games':>6}  {'Avg vig':>8}  {'Median':>8}  {'Range':>14}")
    print(f"  {'─'*58}")
    for book, s in sorted(report["section_a_book_vig"].items(),
                           key=lambda x: x[1]["avg_vig"]):
        rng = f"{s['min_vig']:.1%}–{s['max_vig']:.1%}"
        print(f"  {book:<14} {s['n_games']:>6}  {s['avg_vig']:>8.2%}  "
              f"{s['median_vig']:>8.2%}  {rng:>14}")
    print(f"\n  Interpretation: DK/FanDuel apply roughly 2× Pinnacle's vig.")
    print(f"  A 5% gap vs DK represents less true edge than 5% vs Pinnacle.")

    # Section B
    print(f"\n[B] KALSHI vs EACH BOOK — Gap Win Rates")
    print(f"  *** SMALL SAMPLE: ~23 games with valid pre-game Kalshi prices ***")
    b = report["section_b_kalshi_gaps"]
    if not b:
        print("  No joined records found.")
    else:
        for book in sorted(b):
            print(f"\n  {book.upper()}")
            for tier in ["tier_a", "tier_b", "tier_c", "all"]:
                s = b[book].get(tier, {})
                n = s.get("n", 0)
                if n == 0:
                    print(f"    {tier:8s}: no data")
                    continue
                wr   = s.get("win_rate")
                gaps = s.get("avg_abs_gap")
                print(f"    {tier:8s}: {n:>2} trades  "
                      f"win_rate={wr:.1%}  avg_gap={gaps:.1%}  {s.get('note','')}")

    # Section C
    print(f"\n[C] CROSS-BOOK DIVERGENCE (Pinnacle vs DraftKings)")
    c = report["section_c_divergence"]
    if c.get("n", 0) == 0:
        print(f"  {c.get('note','No data')}")
    else:
        print(f"  Games with both pre-game: {c['n']}")
        print(f"  Avg divergence:           {c['avg_divergence']:.2%}")
        print(f"  DK higher than Pinnacle:  {c['dk_higher_pct']:.0%} of the time")
        print(f"\n  Per-game detail:")
        print(f"  {'Game':<42} {'Team':<22} {'Pin':>5}  {'DK':>5}  {'Div':>5}  {'K mid':>5}  Res")
        print(f"  {'─'*100}")
        for g in c["games"]:
            print(f"  {g['game']:<42} {g['team']:<22} "
                  f"{g['pin_vf']:.1%}  {g['dk_vf']:.1%}  {g['divergence']:.1%}  "
                  f"{g['kalshi_mid']:.1%}  {g.get('result','?')}")

    # Section D
    print(f"\n[D] ODDSPORTAL / BETMGM CALIBRATION (2024-2025, home team)")
    d = report["section_d_oddsportal"]
    print(f"  Games: {d['n_games']}  |  Brier score: {d['brier_score']}")
    print(f"\n  {'Bucket':<10} {'n':>5}  {'Implied':>8}  {'Actual':>8}  {'Diff':>7}")
    print(f"  {'─'*42}")
    for row in d["calibration"]:
        diff_str = f"{row['diff']:>+.1%}"
        print(f"  {row['bucket']:<10} {row['n']:>5}  {row['avg_implied_prob']:>8.1%}  "
              f"{row['actual_win_rate']:>8.1%}  {diff_str:>7}")
    print(f"\n  Interpretation: BetMGM is well-calibrated within ~3%. "
          f"Use as a cheap baseline for future multi-book comparisons.")

    print(f"\n[DATA LIMITATIONS]")
    for lim in meta["data_limitations"]:
        print(f"  • {lim}")
    print()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true", help="Save JSON to outputs/")
    args = parser.parse_args()

    # Load
    print("Loading Vegas rows...", end=" ", flush=True)
    vegas_rows   = load_vegas_rows()
    vegas_files  = len(glob.glob(os.path.join(VEGAS_MLB_DIR, "*.json")))
    print(f"{len(vegas_rows)} rows from {vegas_files} files")

    print("Loading Kalshi markets...", end=" ", flush=True)
    kalshi_mkts  = load_kalshi_markets()
    print(f"{len(kalshi_mkts)} markets")

    # Build indexes
    vegas_index    = _build_vegas_index(vegas_rows)
    kalshi_events  = _build_kalshi_events(kalshi_mkts)
    valid_events   = [e for e in kalshi_events if any(s["valid"] and s["mid"] for s in e["sides"])]

    # Join
    joined = join_kalshi_to_vegas(kalshi_events, vegas_index)

    # Compute sections
    vig_analysis   = compute_vig_analysis(vegas_rows)
    kalshi_gaps    = compute_kalshi_gaps(joined)
    divergence     = compute_book_divergence(joined)
    calibration    = compute_oddsportal_calibration()

    report = {
        "metadata": {
            "generated_at":         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "vegas_files_loaded":   vegas_files,
            "vegas_rows_loaded":    len(vegas_rows),
            "kalshi_markets_loaded": len(kalshi_mkts),
            "kalshi_events_total":  len(kalshi_events),
            "kalshi_events_with_valid_price": len(valid_events),
            "joined_records":       len(joined),
            "data_limitations": [
                "Kalshi candle data is empty — settled markets return no OHLC history from the API.",
                f"Valid pre-game Kalshi prices exist for only ~{len(valid_events)//2} games "
                f"(previous_price differs from settlement by >{KALSHI_VALID_DIFF:.0%}).",
                "Kalshi previous_price_dollars is the last-traded price before settlement, NOT necessarily "
                "time-matched to Vegas captures — timing mismatch inflates Section B gaps artificially.",
                "DK pre-game capture rate ~12%, FanDuel ~5% — most retail captures are post-game.",
                "OddsPortal data (2024-2025) predates meaningful Kalshi MLB presence — Section D is independent.",
                "All win-rate findings in Section B are directional only (n < 50). "
                "Section A vig comparison is the most statistically robust finding.",
            ],
        },
        "section_a_book_vig":    vig_analysis,
        "section_b_kalshi_gaps": kalshi_gaps,
        "section_c_divergence":  divergence,
        "section_d_oddsportal":  calibration,
    }

    print_report(report)

    if args.save:
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
