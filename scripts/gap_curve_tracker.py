"""
scripts/gap_curve_tracker.py

Continuous market monitor: polls Kalshi open markets for MLB and WNBA every
POLL_INTERVAL seconds (default 300 = 5 minutes), fetches Pinnacle vig-free
probability for each matched game, and writes a gap_curves row to
data/gap_curves.db.

Captures the full price lifecycle from market open to market close, enabling
gap-vs-time curve analysis across the entire market window — not just the
2-hour pre-game window captured by the existing snapshot_gaps.py system.

Run as a long-running daemon (systemd Type=simple, Restart=always):
    python scripts/gap_curve_tracker.py

Or smoke-test locally with a short interval:
    GAP_TRACKER_INTERVAL=30 python scripts/gap_curve_tracker.py
"""
import os
import sys
import sqlite3
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils import remove_vig, ticker_to_utc
from scripts.snapshot_gaps import match_team, kalshi_mid

BASE          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KALSHI_BASE   = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
ODDS_BASE     = os.getenv("ODDS_API_BASE",   "https://api.theoddsapi.com")
ODDS_KEY      = os.getenv("ODDS_API_KEY",    "")
POLL_INTERVAL = int(os.getenv("GAP_TRACKER_INTERVAL", "300"))
DB_PATH       = os.path.join(BASE, "data", "gap_curves.db")
LOG_FILE      = os.path.join(BASE, "data", "gap_tracker.log")

MIN_REQ_INTERVAL = 0.2  # 5 req/sec ceiling — well under Kalshi's rate limit

SERIES = {
    "mlb":  "KXMLBGAME",
    "wnba": "KXWNBAGAME",
}
SPORT_KEYS = {
    "mlb":  "baseball_mlb",
    "wnba": "basketball_wnba",
}

_last_req_ts: float = 0.0

# Pinnacle data changes slowly (~1-5 updates/hour per game).
# Cache it for 30 minutes to reduce round-trips and speed up poll cycles.
# Odds API limit is 6,667 calls/day (daily reset); uncached we'd use 576/day
# so quota is not a risk — this is purely a latency/efficiency optimization.
PINNACLE_CACHE_TTL = int(os.getenv("PINNACLE_CACHE_TTL", "1800"))  # 30 min default
_pinnacle_cache: dict[str, tuple[float, dict]] = {}  # sport_key → (fetch_ts, index)


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _get(url: str, **kwargs) -> requests.Response:
    """Rate-limited GET shared across all API calls within a poll cycle."""
    global _last_req_ts
    wait = MIN_REQ_INTERVAL - (time.time() - _last_req_ts)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, **kwargs)
    _last_req_ts = time.time()
    resp.raise_for_status()
    return resp


# ── Database ──────────────────────────────────────────────────────────────────

def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS gap_curves (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            sport              TEXT    NOT NULL,
            series_ticker      TEXT    NOT NULL,
            event_ticker       TEXT    NOT NULL,
            market_ticker      TEXT    NOT NULL,
            game               TEXT    NOT NULL,
            team               TEXT    NOT NULL,
            side               TEXT    NOT NULL,
            game_start_utc     TEXT    NOT NULL,
            market_open_time   TEXT,
            market_close_time  TEXT,
            snapshot_utc       TEXT    NOT NULL,
            snapshot_minute    TEXT    NOT NULL,
            seconds_since_open REAL,
            seconds_to_close   REAL,
            k_prob             REAL,
            k_bid              REAL,
            k_ask              REAL,
            v_prob             REAL,
            gap                REAL,
            abs_gap            REAL,
            volume_fp          REAL,
            created_at         TEXT    DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(market_ticker, snapshot_minute)
        );
        CREATE INDEX IF NOT EXISTS idx_gc_event  ON gap_curves(event_ticker, snapshot_utc);
        CREATE INDEX IF NOT EXISTS idx_gc_market ON gap_curves(market_ticker);
        CREATE INDEX IF NOT EXISTS idx_gc_sport  ON gap_curves(sport, snapshot_utc);
    """)
    conn.commit()
    conn.close()


def write_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.executemany(
        """
        INSERT OR IGNORE INTO gap_curves (
            sport, series_ticker, event_ticker, market_ticker,
            game, team, side, game_start_utc,
            market_open_time, market_close_time,
            snapshot_utc, snapshot_minute,
            seconds_since_open, seconds_to_close,
            k_prob, k_bid, k_ask, v_prob, gap, abs_gap, volume_fp
        ) VALUES (
            :sport, :series_ticker, :event_ticker, :market_ticker,
            :game, :team, :side, :game_start_utc,
            :market_open_time, :market_close_time,
            :snapshot_utc, :snapshot_minute,
            :seconds_since_open, :seconds_to_close,
            :k_prob, :k_bid, :k_ask, :v_prob, :gap, :abs_gap, :volume_fp
        )
        """,
        rows,
    )
    written = cur.rowcount
    conn.commit()
    conn.close()
    return written


# ── API Fetchers ──────────────────────────────────────────────────────────────

def fetch_open_markets(series_ticker: str) -> list[dict]:
    resp = _get(
        f"{KALSHI_BASE}/markets",
        params={"series_ticker": series_ticker, "status": "open", "limit": 200},
        timeout=30,
    )
    return resp.json().get("markets", [])


def fetch_pinnacle(sport_key: str) -> dict[tuple, dict]:
    """Return {(home, away): {team_name: vf_prob}} for all current Pinnacle lines.

    Results are cached for PINNACLE_CACHE_TTL seconds (default 30 min) to stay
    within the Odds API monthly quota. Pinnacle lines move slowly enough that a
    30-min stale read has negligible impact on gap accuracy.
    """
    global _pinnacle_cache
    cached_ts, cached_data = _pinnacle_cache.get(sport_key, (0.0, {}))
    if time.time() - cached_ts < PINNACLE_CACHE_TTL:
        return cached_data

    resp = _get(
        f"{ODDS_BASE}/odds/",
        params={
            "sport_key":  sport_key,
            "markets":    "h2h",
            "bookmakers": "pinnacle",
            "oddsFormat": "american",
        },
        headers={"x-api-key": ODDS_KEY},
        timeout=30,
    )
    index: dict[tuple, dict] = {}
    for g in resp.json().get("data", []):
        home = g["home_team"]
        away = g["away_team"]
        key  = (home, away)
        if key in index:
            continue
        for bk in g.get("books", []):
            if bk.get("book") != "pinnacle":
                continue
            outcomes = {o["name"]: o["price"] for o in bk.get("outcomes", [])}
            if home not in outcomes or away not in outcomes:
                continue
            h_vf, a_vf = remove_vig(outcomes[home], outcomes[away])
            index[key] = {home: h_vf, away: a_vf}

    _pinnacle_cache[sport_key] = (time.time(), index)
    return index


# ── Row Builder ───────────────────────────────────────────────────────────────

def build_rows(
    sport: str,
    series_ticker: str,
    sport_key: str,
    now_utc: datetime,
) -> list[dict]:
    markets   = fetch_open_markets(series_ticker)
    pin_index = fetch_pinnacle(sport_key)

    if not markets or not pin_index:
        return []

    # Group Kalshi markets by event
    events: dict[str, list[dict]] = {}
    for m in markets:
        events.setdefault(m.get("event_ticker", ""), []).append(m)

    snap_utc    = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    snap_minute = snap_utc[:16]  # YYYY-MM-DDTHH:MM — deduplication key
    rows: list[dict] = []

    for event_ticker, sides in events.items():
        if len(sides) < 2:
            continue

        for s in sides:
            s["_mid"] = kalshi_mid(s)
        sides = [s for s in sides if s["_mid"] is not None]
        if len(sides) < 2:
            continue

        # Reliable game-start time from ticker (avoids Kalshi occurrence_datetime UTC bug)
        game_start_utc = ticker_to_utc(event_ticker)
        if game_start_utc is None:
            continue

        # Match this Kalshi event to a Pinnacle game
        k_names     = [s["yes_sub_title"] for s in sides]
        matched_key: tuple | None = None
        for pair in pin_index:
            home_v, away_v = pair
            mapping = {ks: match_team(ks, [home_v, away_v]) for ks in k_names}
            mapping = {k: v for k, v in mapping.items() if v}
            if len(mapping) == 2 and len(set(mapping.values())) == 2:
                matched_key = pair
                break

        if not matched_key:
            continue

        home_v, away_v = matched_key
        game_label     = f"{away_v} @ {home_v}"

        for s in sides:
            team = match_team(s["yes_sub_title"], [home_v, away_v])
            if not team:
                continue
            v_prob = pin_index[matched_key].get(team)
            if v_prob is None:
                continue

            k_prob = s["_mid"]
            gap    = k_prob - v_prob

            # Seconds since market opened (None if field absent)
            sec_since_open: float | None = None
            market_open = s.get("open_time")
            if market_open:
                try:
                    open_dt = datetime.fromisoformat(market_open.replace("Z", "+00:00"))
                    sec_since_open = max(0.0, (now_utc - open_dt).total_seconds())
                except Exception:
                    pass

            # Seconds until game starts (negative after game begins)
            sec_to_close = (game_start_utc - now_utc).total_seconds()

            rows.append({
                "sport":              sport.upper(),
                "series_ticker":      series_ticker,
                "event_ticker":       event_ticker,
                "market_ticker":      s["ticker"],
                "game":               game_label,
                "team":               team,
                "side":               "HOME" if team == home_v else "AWAY",
                "game_start_utc":     game_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "market_open_time":   market_open,
                "market_close_time":  s.get("close_time"),
                "snapshot_utc":       snap_utc,
                "snapshot_minute":    snap_minute,
                "seconds_since_open": sec_since_open,
                "seconds_to_close":   sec_to_close,
                "k_prob":             round(k_prob, 4),
                "k_bid":              float(s.get("yes_bid_dollars") or 0),
                "k_ask":              float(s.get("yes_ask_dollars") or 0),
                "v_prob":             round(v_prob, 4),
                "gap":                round(gap, 4),
                "abs_gap":            round(abs(gap), 4),
                "volume_fp":          float(s.get("volume_fp") or 0),
            })

    return rows


# ── Main Loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    init_db()
    log(f"Gap curve tracker started — polling every {POLL_INTERVAL}s")
    log(f"Sports: {', '.join(s.upper() for s in SERIES)}")
    log(f"DB: {DB_PATH}")

    while True:
        cycle_start = datetime.now(timezone.utc)
        total_written = 0

        for sport, series_ticker in SERIES.items():
            sport_key = SPORT_KEYS[sport]
            try:
                rows    = build_rows(sport, series_ticker, sport_key, cycle_start)
                written = write_rows(rows)
                total_written += written
                if rows:
                    log(f"  {sport.upper()}: {len(rows)} sides matched, {written} new rows")
                else:
                    log(f"  {sport.upper()}: no open markets")
            except Exception as e:
                log(f"  ERROR [{sport.upper()}]: {e}")

        if total_written:
            log(f"  Cycle done — {total_written} new rows total")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
