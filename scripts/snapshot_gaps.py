"""
snapshot_gaps.py

Captures a pre-game price snapshot: Kalshi live prices vs Pinnacle Vegas lines.
Run this BEFORE games start each day to build a clean historical track record.

Each snapshot saves:
  - Kalshi ticker (so we can look up the settled result later)
  - Kalshi mid-price at capture time
  - Pinnacle vig-free probability
  - Gap and favorites-filter flag

Output:
  data/snapshots/2026-06-23_1540.json   — one file per run
  data/snapshots/master_log.json        — appended on every run

Usage:
    python scripts/snapshot_gaps.py                # MLB + NBA
    python scripts/snapshot_gaps.py --sport mlb
    python scripts/snapshot_gaps.py --dry-run      # print only, don't save
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from dotenv import load_dotenv

load_dotenv()

from core.utils import remove_vig, ticker_to_utc

KALSHI_BASE = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
ODDS_BASE   = os.getenv("ODDS_API_BASE",   "https://api.theoddsapi.com")
ODDS_KEY    = os.getenv("ODDS_API_KEY",    "")

SERIES     = {"mlb": "KXMLBGAME", "nba": "KXNBAGAME"}
SPORT_KEYS = {"mlb": "baseball_mlb", "nba": "basketball_nba"}

SNAPSHOT_DIR = "data/snapshots"
MASTER_LOG   = os.path.join(SNAPSHOT_DIR, "master_log.json")

GAP_THRESHOLD = 0.05  # flag any side where |kalshi - pinnacle| >= this

KALSHI_ALIAS = {
    "A's":           "Athletics",
    "Chicago C":     "Cubs",
    "Chicago WS":    "White Sox",
    "Los Angeles A": "Angels",
    "Los Angeles D": "Dodgers",
    "New York M":    "Mets",
    "New York Y":    "Yankees",
    "LA":            "Lakers",
    "LA C":          "Clippers",
    "GS":            "Warriors",
    "NY":            "Knicks",
    "NO":            "Pelicans",
    "OKC":           "Thunder",
    "SA":            "Spurs",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def normalise(sub: str) -> str:
    return KALSHI_ALIAS.get(sub, sub)


def match_team(kalshi_sub: str, vegas_teams: list[str]) -> str | None:
    kw = normalise(kalshi_sub).lower()
    for t in vegas_teams:
        if kw in t.lower():
            return t
    return None


def kalshi_mid(market: dict) -> float | None:
    bid = float(market.get("yes_bid_dollars") or 0)
    ask = float(market.get("yes_ask_dollars") or 1)
    if bid == 0 and ask >= 0.99:
        return None
    return (bid + ask) / 2.0


# ── fetch ─────────────────────────────────────────────────────────────────────

def fetch_kalshi_open(series_ticker: str) -> list[dict]:
    resp = requests.get(
        f"{KALSHI_BASE}/markets",
        params={"series_ticker": series_ticker, "status": "open", "limit": 200},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("markets", [])


def fetch_pinnacle(sport_key: str) -> list[dict]:
    resp = requests.get(
        f"{ODDS_BASE}/odds/",
        params={
            "sport_key":   sport_key,
            "markets":     "h2h",
            "bookmakers":  "pinnacle",
            "oddsFormat":  "american",
        },
        headers={"x-api-key": ODDS_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


# ── build snapshot rows ───────────────────────────────────────────────────────

def build_rows(sport: str) -> list[dict]:
    raw_markets = fetch_kalshi_open(SERIES[sport])
    vegas_games = fetch_pinnacle(SPORT_KEYS[sport])

    # Index Vegas by (home, away)
    by_teams = {(g["home_team"], g["away_team"]): g for g in vegas_games}

    # Group Kalshi by event
    events: dict[str, list[dict]] = {}
    for m in raw_markets:
        events.setdefault(m["event_ticker"], []).append(m)

    rows = []

    for sides in events.values():
        if len(sides) < 2:
            continue

        for s in sides:
            s["_mid"] = kalshi_mid(s)
        sides = [s for s in sides if s["_mid"] is not None]
        if len(sides) < 2:
            continue

        k_names = [s["yes_sub_title"] for s in sides]

        # Match to Pinnacle game
        matched_game = None
        side_map: dict[str, str] = {}

        for (home_v, away_v) in by_teams:
            mapping = {}
            for ks in k_names:
                m = match_team(ks, [home_v, away_v])
                if m:
                    mapping[ks] = m
            if len(mapping) == 2 and len(set(mapping.values())) == 2:
                matched_game = by_teams[(home_v, away_v)]
                side_map = mapping
                break

        if not matched_game:
            continue

        home_v = matched_game["home_team"]
        away_v = matched_game["away_team"]

        # Get Pinnacle outcomes
        pinnacle_outcomes = {}
        for book in matched_game.get("books", []):
            if book["book"] == "pinnacle":
                for o in book.get("outcomes", []):
                    pinnacle_outcomes[o["name"]] = o["price"]

        if home_v not in pinnacle_outcomes or away_v not in pinnacle_outcomes:
            continue

        v_home, v_away = remove_vig(pinnacle_outcomes[home_v], pinnacle_outcomes[away_v])

        for s in sides:
            vs_name = side_map.get(s["yes_sub_title"])
            if not vs_name:
                continue
            is_home = (vs_name == home_v)
            v_prob  = v_home if is_home else v_away
            k_prob  = s["_mid"]
            gap     = k_prob - v_prob

            # Flag any side where the gap is large enough in either direction
            # gap < 0 → Kalshi underprices (BUY_YES)   gap > 0 → Kalshi overprices (BUY_NO)
            fav_flag  = abs(gap) >= GAP_THRESHOLD
            signal    = "BUY_YES" if gap < 0 else "BUY_NO"
            _start    = ticker_to_utc(s.get("event_ticker", ""))
            start_utc = _start.strftime("%Y-%m-%dT%H:%M:%SZ") if _start else s.get("occurrence_datetime", "")

            rows.append({
                "sport":          sport.upper(),
                "game":           f"{away_v} @ {home_v}",
                "team":           vs_name,
                "side":           "HOME" if is_home else "AWAY",
                "start_utc":      start_utc,
                "kalshi_ticker":  s["ticker"],
                "event_ticker":   s["event_ticker"],
                "k_prob":         round(k_prob, 4),
                "k_bid":          float(s.get("yes_bid_dollars") or 0),
                "k_ask":          float(s.get("yes_ask_dollars") or 0),
                "v_prob":         round(v_prob, 4),
                "pinnacle_price": pinnacle_outcomes[vs_name],
                "gap":            round(gap, 4),
                "abs_gap":        round(abs(gap), 4),
                "fav_flag":       fav_flag,
                "signal":         signal if fav_flag else None,
                "result":         None,
            })

    return rows


# ── save ──────────────────────────────────────────────────────────────────────

def save_snapshot(rows: list[dict], ts: str, dry_run: bool = False) -> str:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    filename = os.path.join(SNAPSHOT_DIR, f"{ts}.json")

    payload = {"snapshot_time": ts, "rows": rows}

    if not dry_run:
        with open(filename, "w") as f:
            json.dump(payload, f, indent=2)

        # Append to master log
        log = []
        if os.path.exists(MASTER_LOG):
            with open(MASTER_LOG) as f:
                log = json.load(f)
        log.append(payload)
        with open(MASTER_LOG, "w") as f:
            json.dump(log, f, indent=2)

    return filename


# ── print ─────────────────────────────────────────────────────────────────────

def print_snapshot(rows: list[dict], ts: str) -> None:
    flagged = [r for r in rows if r["fav_flag"]]

    print(f"\n{'═'*100}")
    print(f"  SNAPSHOT  {ts}  ({len(rows)} team-sides across {len(rows)//2} games)")
    print(f"{'═'*100}\n")

    print(f"  {'TEAM':<28} {'GAME':<40} {'START':<17} {'K%':>5} {'V%':>5} {'GAP':>7}  FLAG")
    print(f"  {'─'*105}")
    for r in sorted(rows, key=lambda x: x["abs_gap"], reverse=True):
        flag = " ★ FAVORITES FILTER" if r["fav_flag"] else ""
        start = r["start_utc"][:16].replace("T", " ") + "Z"
        print(
            f"  {r['team']:<28} {r['game']:<40} {start:<17}"
            f"  {r['k_prob']:.1%}  {r['v_prob']:.1%}  {r['gap']:>+6.1%}{flag}"
        )

    print(f"\n  {'─'*60}")
    print(f"  Total game-sides     : {len(rows)}")
    print(f"  Flagged trades ★     : {len(flagged)}  (|Kalshi − Pinnacle| ≥ {GAP_THRESHOLD:.0%})")
    if flagged:
        print(f"\n  ★ TRADES:")
        for r in sorted(flagged, key=lambda x: x["abs_gap"], reverse=True):
            start  = r["start_utc"][:16].replace("T", " ") + "Z"
            signal = r.get("signal", "")
            edge   = abs(r["gap"])
            action = f"{signal} {r['team']}"
            print(f"    {action:<40}  Kalshi={r['k_prob']:.1%}  Pinnacle={r['v_prob']:.1%}  edge={edge:.1%}  {start}")
    print()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=["mlb", "nba", "both"], default="both")
    parser.add_argument("--dry-run", action="store_true", help="Print only, don't save")
    args = parser.parse_args()

    sports = ["mlb", "nba"] if args.sport == "both" else [args.sport]
    ts     = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")

    all_rows: list[dict] = []

    for sport in sports:
        print(f"Fetching {sport.upper()}...", end="  ", flush=True)
        try:
            rows = build_rows(sport)
            print(f"{len(rows)} sides matched")
            all_rows.extend(rows)
        except Exception as e:
            print(f"ERROR — {e}")

    if not all_rows:
        print("\nNo live games found.")
        return

    print_snapshot(all_rows, ts)

    if args.dry_run:
        print("  [dry-run] Nothing saved.\n")
    else:
        path = save_snapshot(all_rows, ts)
        print(f"  Saved → {path}")
        print(f"  Master log → {MASTER_LOG}\n")


if __name__ == "__main__":
    main()
