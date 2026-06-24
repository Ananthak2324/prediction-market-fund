import pytest
from core.utils import american_to_prob, remove_vig, kalshi_to_prob, compute_edge, kelly_fraction


# --- american_to_prob ---

def test_american_to_prob_even_money_positive():
    assert american_to_prob(100) == pytest.approx(0.5)


def test_american_to_prob_even_money_negative():
    assert american_to_prob(-100) == pytest.approx(0.5)


def test_american_to_prob_negative_favorite():
    assert american_to_prob(-110) == pytest.approx(110 / 210)


def test_american_to_prob_heavy_favorite():
    assert american_to_prob(-400) == pytest.approx(400 / 500)


def test_american_to_prob_heavy_underdog():
    assert american_to_prob(300) == pytest.approx(100 / 400)


def test_american_to_prob_positive_underdog():
    assert american_to_prob(130) == pytest.approx(100 / 230)


# --- remove_vig ---

def test_remove_vig_sums_to_one():
    home, away = remove_vig(-110, -110)
    assert home + away == pytest.approx(1.0)


def test_remove_vig_even_line():
    home, away = remove_vig(-110, -110)
    assert home == pytest.approx(0.5)
    assert away == pytest.approx(0.5)


def test_remove_vig_favorite_is_higher():
    home, away = remove_vig(-150, 130)
    assert home > away


def test_remove_vig_heavy_favorite():
    home, away = remove_vig(-300, 250)
    assert home > 0.7
    assert home + away == pytest.approx(1.0)


def test_remove_vig_underdog_line():
    home, away = remove_vig(120, -140)
    assert away > home
    assert home + away == pytest.approx(1.0)


# --- kalshi_to_prob ---

def test_kalshi_to_prob_midpoint():
    assert kalshi_to_prob(65) == pytest.approx(0.65)


def test_kalshi_to_prob_zero():
    assert kalshi_to_prob(0) == pytest.approx(0.0)


def test_kalshi_to_prob_max():
    assert kalshi_to_prob(100) == pytest.approx(1.0)


def test_kalshi_to_prob_fractional():
    assert kalshi_to_prob(33) == pytest.approx(0.33)


# --- compute_edge ---

def test_compute_edge_positive():
    assert compute_edge(0.60, 0.55) == pytest.approx(0.05)


def test_compute_edge_zero():
    assert compute_edge(0.50, 0.50) == pytest.approx(0.0)


def test_compute_edge_negative():
    assert compute_edge(0.40, 0.55) == pytest.approx(-0.15)


# --- kelly_fraction ---

def test_kelly_no_edge_returns_zero():
    assert kelly_fraction(0.0, 2.0) == 0.0


def test_kelly_negative_edge_returns_zero():
    assert kelly_fraction(-0.05, 2.0) == 0.0


def test_kelly_positive_edge():
    result = kelly_fraction(0.05, 2.0)
    assert result > 0
    assert result <= 0.25


def test_kelly_clamped_at_25_percent():
    # Very large edge — raw Kelly would exceed 0.25
    result = kelly_fraction(0.5, 2.0)
    assert result == pytest.approx(0.25)


def test_kelly_odds_at_one_returns_zero():
    assert kelly_fraction(0.1, 1.0) == 0.0


def test_kelly_odds_below_one_returns_zero():
    assert kelly_fraction(0.1, 0.9) == 0.0


def test_kelly_standard_calculation():
    # edge=0.05, odds=2.0 → kelly = 0.05 / (2.0 - 1) = 0.05
    assert kelly_fraction(0.05, 2.0) == pytest.approx(0.05)
