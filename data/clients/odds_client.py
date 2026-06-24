import requests
from config.config import ODDS_API_KEY, ODDS_API_BASE
from core.logger import logger

NBA_KEY = "basketball_nba"
NFL_KEY = "americanfootball_nfl"
MLB_KEY = "baseball_mlb"

SPORT_KEYS = {
    "nba": NBA_KEY,
    "nfl": NFL_KEY,
    "mlb": MLB_KEY,
}

_HEADERS = lambda: {"x-api-key": ODDS_API_KEY}

CREDIT_STOP_THRESHOLD = 100


def get_remaining_credits() -> int:
    """Return remaining daily credits from /me/."""
    r = requests.get(f"{ODDS_API_BASE}/me/", headers=_HEADERS(), timeout=10)
    r.raise_for_status()
    return r.json()["data"]["remaining"]


def get_historical_odds(sport_key: str, from_iso: str, to_iso: str) -> list[dict]:
    """Return flat odds rows captured between from_iso and to_iso.

    Each row is one outcome update:
      event_id, home_team, away_team, start_time, book, market,
      outcome_name, price, point, captured_at

    Raises RuntimeError if remaining credits drop below CREDIT_STOP_THRESHOLD.
    """
    url = f"{ODDS_API_BASE}/historical/odds"
    params = {
        "sport_key": sport_key,
        "from": from_iso,
        "to": to_iso,
        "markets": "h2h",
        "bookmakers": "pinnacle,draftkings,fanduel",
        "oddsFormat": "american",
    }
    resp = requests.get(url, params=params, headers=_HEADERS(), timeout=30)
    resp.raise_for_status()
    data = resp.json()

    rows = data.get("data", [])
    meta = data.get("meta", {})
    logger.info(
        f"historical_odds [{sport_key} {from_iso[:10]}] — "
        f"{len(rows)} rows, total_rows={meta.get('total_rows')}"
    )

    remaining = get_remaining_credits()
    logger.info(f"Credits remaining: {remaining}")
    if remaining < CREDIT_STOP_THRESHOLD:
        raise RuntimeError(
            f"Odds API credits critically low: {remaining} remaining. Halting."
        )

    return [r for r in rows if r.get("market") == "h2h"]


def get_live_odds(sport_key: str) -> list[dict]:
    """Return current live moneylines in the live nested format.

    Each item: event_id, home_team, away_team, start_time, books[{book, outcomes}]
    Used in Phase 3.
    """
    url = f"{ODDS_API_BASE}/odds/"
    params = {
        "sport_key": sport_key,
        "markets": "h2h",
        "bookmakers": "pinnacle,draftkings,fanduel",
        "oddsFormat": "american",
    }
    resp = requests.get(url, params=params, headers=_HEADERS(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])
