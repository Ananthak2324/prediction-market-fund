def american_to_prob(odds: int) -> float:
    """Convert American moneyline to raw implied probability (vig not removed).

    Positive odds (underdog): 100 / (odds + 100)
    Negative odds (favorite): abs(odds) / (abs(odds) + 100)
    """
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def remove_vig(home_odds: int, away_odds: int) -> tuple[float, float]:
    """Remove the bookmaker's vig from a two-sided moneyline.

    Converts each side to a raw implied probability, then normalises both
    by dividing by their sum so the pair sums to exactly 1.0.

    Returns:
        (home_prob, away_prob) — vig-free probabilities summing to 1.0.
    """
    raw_home = american_to_prob(home_odds)
    raw_away = american_to_prob(away_odds)
    total = raw_home + raw_away
    return raw_home / total, raw_away / total


def kalshi_to_prob(yes_price_cents: float) -> float:
    """Convert Kalshi yes_price (0–100 cent scale) to a probability (0.0–1.0).

    Kalshi contracts trade in cents, so a yes_price of 65 means the market
    implies a 65% probability of the event occurring.
    """
    return yes_price_cents / 100.0


def compute_edge(our_prob: float, market_prob: float) -> float:
    """Return the edge of our probability estimate vs the market price.

    Positive result means we believe the market is underpricing the event.
    Negative result means we believe the market is overpricing it.
    """
    return our_prob - market_prob


def kelly_fraction(edge: float, odds: float) -> float:
    """Standard Kelly criterion bet sizing.

    Args:
        edge:  our_prob - market_prob (must be positive to bet).
        odds:  decimal payout odds (e.g. 2.0 for even money, 1.5 for -200).

    Returns:
        Fraction of bankroll to wager, clamped to [0.0, 0.25].
        Never bets more than 25% of bankroll regardless of Kelly output.
    """
    if odds <= 1 or edge <= 0:
        return 0.0
    kelly = edge / (odds - 1)
    return min(kelly, 0.25)
