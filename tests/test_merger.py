import pandas as pd
from analysis.merger import merge_feeds


def _kalshi():
    return pd.DataFrame([
        {"game_date": "2025-01-10", "home_team": "Boston Celtics", "away_team": "Miami Heat",
         "kalshi_home_prob": 0.62},
        {"game_date": "2025-01-11", "home_team": "LAL", "away_team": "GSW",
         "kalshi_home_prob": 0.55},
    ])


def _vegas():
    return pd.DataFrame([
        {"game_date": "2025-01-10", "home_team": "Boston Celtics", "away_team": "Miami Heat",
         "vegas_home_prob": 0.60, "vegas_away_prob": 0.40},
        {"game_date": "2025-01-11", "home_team": "Los Angeles Lakers", "away_team": "Golden State Warriors",
         "vegas_home_prob": 0.52, "vegas_away_prob": 0.48},
    ])


def test_merge_exact_match():
    merged = merge_feeds(_kalshi(), _vegas(), "nba")
    assert len(merged) == 2


def test_merge_normalizes_abbreviations():
    merged = merge_feeds(_kalshi(), _vegas(), "nba")
    teams = merged["home_team"].tolist()
    assert "Los Angeles Lakers" in teams


def test_merge_missing_game_drops_row():
    kalshi = _kalshi()
    vegas = _vegas().iloc[:1]  # only first game
    merged = merge_feeds(kalshi, vegas, "nba")
    assert len(merged) == 1
