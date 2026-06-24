from dotenv import load_dotenv
import os

load_dotenv()

ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE: str = os.getenv("ODDS_API_BASE", "https://api.the-odds-api.com/v4")
KALSHI_API_BASE: str = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
DB_PATH: str = os.getenv("DB_PATH", "data/trade_log.db")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
