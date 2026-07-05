#!/usr/bin/env python3
"""
live_gap_detector.py

Pulls live Kalshi prices and live Vegas moneylines, normalises both to
implied probability, computes the gap, and prints a ranked table.

Gap = kalshi_prob − vegas_prob
  Positive → Kalshi overprices the team vs. Vegas
  Negative → Kalshi underprices the team vs. Vegas

Usage:
    python live_gap_detector.py              # both MLB + NBA
    python live_gap_detector.py --sport mlb
    python live_gap_detector.py --sport nba
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from dotenv import load_dotenv

load_dotenv()

from core.utils import remove_vig
from core.desk_loader import get_desk, get_active_desks

KALSHI_BASE = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
ODDS_BASE   = os.getenv("ODDS_API_BASE",   "https://api.theoddsapi.com")
ODDS_KEY    = os.getenv("ODDS_API_KEY",    "")

BOOK_PRIORITY = ["pinnacle"]


# ── Kalshi ────────────────────────────────────────────────────────────────────

def fetch_kalshi_open(series_ticker: str) -> list[dict]:
    resp = requests.get(
        f"{KALSHI_BASE}/markets",
        params={"series_ticker": series_ticker, "status": "open", "limit": 200},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("markets", [])


def kalshi_mid(market: dict) -> float | None:
    """Midpoint of yes_bid / yes_ask as implied probability (0–1)."""
    bid = float(market.get("yes_bid_dollars") or 0)
    ask = float(market.get("yes_ask_dollars") or 1)
    if bid == 0 and ask >= 0.99:
        return None  # no market yet
    return (bid + ask) / 2.0


def group_by_event(markets: list[dict]) -> dict[str, list[dict]]:
    """Group open markets by event_ticker (one event = one game)."""
    events: dict[str, list[dict]] = {}
    for m in markets:
        evt = m.get("event_ticker", "")
        if evt:
            events.setdefault(evt, []).append(m)
    return events


# ── Vegas ─────────────────────────────────────────────────────────────────────

def fetch_vegas(sport_key: str) -> list[dict]:
    resp = requests.get(
        f"{ODDS_BASE}/odds/",
        params={
            "sport_key": sport_key,
            "markets": "h2h",
            "bookmakers": "pinnacle,draftkings,fanduel",
            "oddsFormat": "american",
        },
        headers={"x-api-key": ODDS_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def best_book_outcomes(game: dict) -> tuple[str, dict] | tuple[None, None]:
    """Return (book_name, {team: price}) for the highest-priority available book."""
    books = {b["book"]: b for b in game.get("books", [])}
    for book in BOOK_PRIORITY:
        if book in books:
            outcomes = {o["name"]: o["price"] for o in books[book].get("outcomes", [])}
            return book, outcomes
    return None, None


# ── Matching ──────────────────────────────────────────────────────────────────

def normalise(kalshi_sub: str, alias_map: dict) -> str:
    """Map a Kalshi yes_sub_title to the keyword we'll search in Vegas team names."""
    return alias_map.get(kalshi_sub, kalshi_sub)


def match_to_vegas(kalshi_sub: str, vegas_teams: list[str], alias_map: dict) -> str | None:
    """Return the Vegas team name that best matches a Kalshi sub_title."""
    keyword = normalise(kalshi_sub, alias_map).lower()
    for team in vegas_teams:
        if keyword in team.lower():
            return team
    return None


# ── Core logic ────────────────────────────────────────────────────────────────

def build_gaps(desk) -> list[dict]:
    """desk: core.desk_loader.DeskConfig."""
    series    = desk.series_ticker
    sport_key = desk.sport_key
    alias_map = desk.alias_map

    # 1. Live Kalshi
    raw = fetch_kalshi_open(series)
    events = group_by_event(raw)

    # 2. Live Vegas
    vegas_games = fetch_vegas(sport_key)
    # Index by (home, away) for fast lookup
    by_teams: dict[tuple, dict] = {
        (g["home_team"], g["away_team"]): g for g in vegas_games
    }
    vegas_all_teams = [(h, a) for h, a in by_teams]

    gaps = []

    for sides in events.values():
        if len(sides) < 2:
            continue  # need both teams

        # Compute Kalshi midpoint per side
        for s in sides:
            s["prob"] = kalshi_mid(s)
        sides = [s for s in sides if s["prob"] is not None]
        if len(sides) < 2:
            continue

        kalshi_names = [s["yes_sub_title"] for s in sides]

        # Try to find the Vegas game by matching both Kalshi team names
        matched_game = None
        side_map: dict[str, str] = {}  # kalshi_sub → vegas_full_name

        for (home_v, away_v) in vegas_all_teams:
            both_v = [home_v, away_v]
            mapping = {}
            for ks in kalshi_names:
                m = match_to_vegas(ks, both_v, alias_map)
                if m:
                    mapping[ks] = m
            if len(mapping) == 2 and len(set(mapping.values())) == 2:
                matched_game = by_teams[(home_v, away_v)]
                side_map = mapping
                break

        if not matched_game:
            continue

        book, outcomes = best_book_outcomes(matched_game)
        if not book or not outcomes:
            continue

        home_v = matched_game["home_team"]
        away_v = matched_game["away_team"]
        if home_v not in outcomes or away_v not in outcomes:
            continue

        vegas_home_prob, vegas_away_prob = remove_vig(outcomes[home_v], outcomes[away_v])

        # Map each Kalshi side to home/away
        for s in sides:
            vs_name = side_map.get(s["yes_sub_title"])
            if not vs_name:
                continue
            is_home = (vs_name == home_v)
            vegas_prob = vegas_home_prob if is_home else vegas_away_prob
            gap = s["prob"] - vegas_prob

            game_label = f"{away_v} @ {home_v}"
            start = s.get("occurrence_datetime", "")[:16].replace("T", " ") + "Z"

            gaps.append({
                "sport":       desk.sport_display_key,
                "game":        game_label,
                "team":        vs_name,
                "side":        "HOME" if is_home else "AWAY",
                "start":       start,
                "kalshi":      s["prob"],
                "vegas":       vegas_prob,
                "gap":         gap,
                "abs_gap":     abs(gap),
                "book":        book,
                "volume_fp":   s.get("volume_fp", "0"),
            })

    return gaps


# ── Output ────────────────────────────────────────────────────────────────────

def print_table(rows: list[dict]) -> None:
    if not rows:
        print("  (no matched games with active markets)\n")
        return

    hdr = f"{'TEAM':<28} {'GAME':<38} {'START':<17} {'K':>6} {'V':>6} {'GAP':>7}  DIR  BOOK"
    print(hdr)
    print("─" * len(hdr))

    for r in rows:
        gap_pct = f"{r['gap']:+.1%}"
        direction = "▲K" if r["gap"] > 0 else "▼K"
        print(
            f"{r['team']:<28} {r['game']:<38} {r['start']:<17}"
            f"  {r['kalshi']:.1%}  {r['vegas']:.1%}  {gap_pct:>7}  {direction}  {r['book']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desk", default=None,
                        help="Desk to scan (e.g. MLB, WNBA). See desks/*.yaml.")
    parser.add_argument("--all-desks", action="store_true",
                        help="Scan all ACTIVE desks. Default when --desk is omitted.")
    args = parser.parse_args()

    desks = [get_desk(args.desk)] if args.desk and not args.all_desks else get_active_desks()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'═'*110}")
    print(f"  LIVE GAP DETECTOR   {ts}")
    print(f"{'═'*110}\n")

    all_gaps: list[dict] = []

    for desk in desks:
        if not desk.is_active:
            continue
        print(f"Fetching {desk.desk_id}...", end="  ", flush=True)
        try:
            gaps = build_gaps(desk)
            print(f"{len(gaps)} team-sides matched")
            all_gaps.extend(gaps)
        except Exception as e:
            print(f"ERROR — {e}")

    if not all_gaps:
        print("\nNo live games matched right now.")
        return

    all_gaps.sort(key=lambda x: x["abs_gap"], reverse=True)

    print(f"\n  Ranked by gap size (Kalshi implied prob − Vegas vig-free prob):\n")
    print_table(all_gaps)

    games_shown = len(all_gaps) // 2
    over3 = sum(1 for g in all_gaps if g["abs_gap"] >= 0.03)
    avg   = sum(g["abs_gap"] for g in all_gaps) / len(all_gaps)

    print(f"\n  {'─'*60}")
    print(f"  Matched games   : {games_shown}")
    print(f"  Team-sides      : {len(all_gaps)}")
    print(f"  Gap ≥ 3%        : {over3}  ({over3/len(all_gaps)*100:.0f}% of sides)")
    print(f"  Avg |gap|       : {avg:.1%}")
    print(f"  Max gap         : {all_gaps[0]['gap']:+.1%}  ({all_gaps[0]['team']}  {all_gaps[0]['game']})")
    print()


if __name__ == "__main__":
    main()
