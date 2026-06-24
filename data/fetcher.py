import pandas as pd
from data.clients.odds_client import get_historical_odds
from core.utils import remove_vig  # noqa: F401 — re-exported for legacy callers
from core.logger import logger


def fetch_vegas_for_date_range(sport: str, dates: list[str]) -> pd.DataFrame:
    """Pull Vegas odds at open for each date in dates, return cleaned DataFrame."""
    rows = []
    for date_iso in dates:
        try:
            games = get_historical_odds(sport, date_iso)
        except Exception as e:
            logger.warning(f"Skipping {date_iso} for {sport}: {e}")
            continue

        for game in games:
            home = game.get("home_team", "")
            away = game.get("away_team", "")
            commence = game.get("commence_time", "")
            bookmakers = game.get("bookmakers", [])
            if not bookmakers:
                continue
            # use first bookmaker as representative (Pinnacle preferred if present)
            bk = next((b for b in bookmakers if b["key"] == "pinnacle"), bookmakers[0])
            markets = {m["key"]: m for m in bk.get("markets", [])}
            h2h = markets.get("h2h", {})
            outcomes = {o["name"]: o["price"] for o in h2h.get("outcomes", [])}
            if home not in outcomes or away not in outcomes:
                continue
            home_prob, away_prob = remove_vig(outcomes[home], outcomes[away])
            rows.append({
                "sport": sport,
                "game_date": commence[:10],
                "home_team": home,
                "away_team": away,
                "vegas_home_prob": home_prob,
                "vegas_away_prob": away_prob,
            })

    df = pd.DataFrame(rows)
    logger.info(f"Vegas fetch complete: {len(df)} games for {sport}")
    return df
