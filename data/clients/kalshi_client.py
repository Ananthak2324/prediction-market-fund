import time
import requests
from config.config import KALSHI_API_BASE
from core.logger import logger

NBA_SERIES = "KXNBAGAME"
NFL_SERIES = "KXNFLGAME"
MLB_SERIES = "KXMLBGAME"

SPORT_SERIES = {
    "nba": NBA_SERIES,
    "nfl": NFL_SERIES,
    "mlb": MLB_SERIES,
}


def get_settled_markets(series_ticker: str) -> list[dict]:
    """Paginate through all settled markets for a given series ticker.

    Calls GET /markets with status=settled and loops via the cursor field
    until the API returns no cursor or an empty page.

    Args:
        series_ticker: One of NBA_SERIES, NFL_SERIES, MLB_SERIES.

    Returns:
        Full list of all market dicts for the series.
    """
    url = f"{KALSHI_API_BASE}/markets"
    all_markets: list[dict] = []
    cursor: str | None = None
    page = 0

    while True:
        params: dict = {
            "series_ticker": series_ticker,
            "status": "settled",
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        markets = data.get("markets", [])
        if not markets:
            break

        all_markets.extend(markets)
        page += 1
        logger.info(f"{series_ticker} page {page}: +{len(markets)} markets ({len(all_markets)} total)")

        cursor = data.get("cursor")
        if not cursor:
            break

        time.sleep(0.3)

    logger.info(f"{series_ticker}: {len(all_markets)} total settled markets retrieved")
    return all_markets


def get_market_candlesticks(ticker: str, period_interval: int = 60) -> list[dict]:
    """Fetch OHLC candlestick data for a single market.

    Args:
        ticker:          The market ticker (e.g. KXNBAGAMES-25BOSNYKNOV19).
        period_interval: Candle width in minutes. 60 = hourly, 1440 = daily.

    Returns:
        List of candle dicts from the API.
    """
    url = f"{KALSHI_API_BASE}/markets/{ticker}/candlesticks"
    params = {"period_interval": period_interval}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("candlesticks", [])


def get_historical_cutoff() -> dict:
    """Return the boundary timestamp between live and historical data tiers.

    Returns:
        JSON response dict from GET /historical/cutoff.
    """
    url = f"{KALSHI_API_BASE}/historical/cutoff"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()
