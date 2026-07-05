"""
schedule_snapshots.py

Runs every 10 min via LaunchAgent.
Game start times are decoded from the Kalshi event ticker (YYMMMDDHHMM in ET),
NOT from occurrence_datetime which carries a known 3-hour UTC/ET confusion error.

Ticker format: KXMLBGAME-26JUN241210TEXMIA
  26   = year 2026
  JUN  = June
  24   = 24th
  1210 = 12:10 PM Eastern Time  →  16:10 UTC

For each upcoming game whose start is 110-130 min away (20-min window):
  - Triggers snapshot_gaps.py to capture live Kalshi + Pinnacle prices

Logs:
  data/snapshots/scheduler_log.txt     — timing + status every 10 min
  data/snapshots/missed_snapshots.json — games whose window was missed
"""
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from dotenv import load_dotenv

load_dotenv()

from core.utils import ticker_to_utc
from core.desk_loader import get_active_desks

KALSHI_BASE  = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
SNAPSHOT_DIR = "data/snapshots"
LOG_FILE     = os.path.join(SNAPSHOT_DIR, "scheduler_log.txt")
MISSED_LOG   = os.path.join(SNAPSHOT_DIR, "missed_snapshots.json")

# 20-minute window centered at 2h before game: [start − 2h10m, start − 1h50m]
WINDOW_MIN = 110   # minutes before game (window opens)
WINDOW_MAX = 130   # minutes before game (window closes)


# ── helpers ───────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line = f"[{ts}] {msg}"
    print(line)
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def fetch_games(series_ticker: str) -> list[dict]:
    """
    Return one entry per unique event_ticker: {event_ticker, start_utc, label}.

    start_utc is parsed from the ticker (e.g. '1210' = 12:10 PM ET → UTC),
    NOT from occurrence_datetime which is 3 hours off on all Kalshi markets.
    Games whose ticker can't be parsed are silently skipped.
    """
    try:
        resp = requests.get(
            f"{KALSHI_BASE}/markets",
            params={"series_ticker": series_ticker, "status": "open", "limit": 200},
            timeout=30,
        )
        resp.raise_for_status()
        markets = resp.json().get("markets", [])
    except Exception as e:
        log(f"  Kalshi fetch failed ({series_ticker}): {e}")
        return []

    seen: dict[str, dict] = {}
    for m in markets:
        et = m.get("event_ticker", "")
        if not et or et in seen:
            continue
        start_utc = ticker_to_utc(et)
        if start_utc is None:
            continue
        seen[et] = {
            "event_ticker": et,
            "start_utc":    start_utc,
            "label":        m.get("yes_sub_title", et),
        }

    return list(seen.values())


def get_well_captured_tickers(games: list[dict]) -> set[str]:
    """
    Return event_tickers that already have a properly-timed snapshot
    (taken 1.5h–2.5h before game start, using ticker-derived start times).
    """
    start_by_ticker = {g["event_ticker"]: g["start_utc"] for g in games}
    well_captured: set[str] = set()

    for snap_file in glob.glob(os.path.join(SNAPSHOT_DIR, "????-??-??_????.json")):
        try:
            with open(snap_file) as f:
                data = json.load(f)
            snap_time_str = data.get("snapshot_time", "")
            snap_dt = datetime.strptime(snap_time_str, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
            for row in data.get("rows", []):
                et = row.get("event_ticker")
                if not et or et not in start_by_ticker:
                    continue
                hours_before = (start_by_ticker[et] - snap_dt).total_seconds() / 3600
                if 1.5 <= hours_before <= 2.5:
                    well_captured.add(et)
        except Exception:
            pass

    return well_captured


def log_missed(game: dict, now: datetime) -> None:
    """Append a MISSED_SNAPSHOT record if this game hasn't been logged already."""
    record = {
        "event_ticker":     game["event_ticker"],
        "label":            game["label"],
        "game_start_utc":   game["start_utc"].isoformat(),
        "window_open_utc":  (game["start_utc"] - timedelta(minutes=WINDOW_MAX)).isoformat(),
        "window_close_utc": (game["start_utc"] - timedelta(minutes=WINDOW_MIN)).isoformat(),
        "detected_at":      now.isoformat(),
    }

    existing: list[dict] = []
    if os.path.exists(MISSED_LOG):
        try:
            with open(MISSED_LOG) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass

    if record["event_ticker"] in {r["event_ticker"] for r in existing}:
        return

    existing.append(record)
    with open(MISSED_LOG, "w") as f:
        json.dump(existing, f, indent=2)

    log(
        f"  MISSED_SNAPSHOT: {game['event_ticker']} — "
        f"window was {record['window_open_utc'][:16]} → {record['window_close_utc'][:16]} UTC"
    )


def run_snapshot() -> bool:
    """
    Invoke snapshot_gaps.py across all active desks and return True on success.
    Previously hardcoded to "--sport mlb" — meant WNBA games in this scheduler's
    own window never actually triggered a snapshot (only the separate widescan
    job covered them). --all-desks fixes that gap as a side effect.
    """
    result = subprocess.run(
        [sys.executable, "scripts/snapshot_gaps.py", "--all-desks"],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    if result.returncode == 0:
        print(result.stdout)
        return True
    log(f"  snapshot_gaps.py failed: {result.stderr[:300]}")
    return False


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(timezone.utc)
    log(f"Scheduler check — {now.strftime('%Y-%m-%d %H:%M UTC')}")

    all_games: list[dict] = []
    for desk in get_active_desks():
        games = fetch_games(desk.series_ticker)
        log(f"  {desk.desk_id}: {len(games)} open games found")
        all_games.extend(games)

    if not all_games:
        log("  No upcoming games found. Nothing to do.")
        return

    captured_tickers = get_well_captured_tickers(all_games)

    in_window:    list[dict] = []
    missed_games: list[dict] = []

    for game in all_games:
        minutes_away = (game["start_utc"] - now).total_seconds() / 60
        already      = game["event_ticker"] in captured_tickers

        if WINDOW_MIN <= minutes_away <= WINDOW_MAX:
            if already:
                log(f"  IN WINDOW (already snapped): {game['event_ticker']}")
            else:
                in_window.append(game)
                log(f"  IN WINDOW — needs snapshot: {game['event_ticker']} "
                    f"starts in {minutes_away:.0f} min")

        elif 0 < minutes_away < WINDOW_MIN and not already:
            missed_games.append(game)

    for game in missed_games:
        log_missed(game, now)

    if not in_window:
        future = [g for g in all_games if (g["start_utc"] - now).total_seconds() / 60 > WINDOW_MIN]
        if future:
            next_game = min(future, key=lambda g: g["start_utc"])
            mins_left = (next_game["start_utc"] - now).total_seconds() / 60 - WINDOW_MAX
            log(f"  No games in window. Next window opens in {mins_left:.0f} min "
                f"({next_game['event_ticker']})")
        else:
            log("  No upcoming games outside current window.")
        return

    log(f"  Triggering snapshot for {len(in_window)} game(s) in window...")
    if run_snapshot():
        snap_ts = now.strftime("%Y-%m-%d %H:%M UTC")
        for game in in_window:
            hours_before = (game["start_utc"] - now).total_seconds() / 3600
            log(
                f"  TIMING: {game['event_ticker']} | "
                f"game_start_utc={game['start_utc'].strftime('%Y-%m-%d %H:%M UTC')} | "
                f"snapshot_taken_utc={snap_ts} | "
                f"hours_before_game={hours_before:.2f}h"
            )
        log("  Snapshot complete.")
    else:
        log("  Snapshot FAILED — check logs.")


if __name__ == "__main__":
    main()
