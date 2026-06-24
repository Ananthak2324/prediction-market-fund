"""
execution/position_sizer.py
Quarter-Kelly position sizing for Kalshi YES/NO contracts.

Entry prices are always the contract we're BUYING:
  BUY_YES → kalshi_price = k_prob,     pinnacle_prob = v_prob
  BUY_NO  → kalshi_price = 1 - k_prob, pinnacle_prob = 1 - v_prob

Payout structure: each contract pays $1.00 on resolution.
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "paper_trades.db")


def calculate_position(bankroll: float, kalshi_price: float, pinnacle_prob: float) -> dict:
    """
    Quarter-Kelly sizing capped at 10% of bankroll per position.

    Args:
        bankroll:      current total bankroll in dollars
        kalshi_price:  contract entry price (0-1, the side we're buying)
        pinnacle_prob: Pinnacle vig-free probability that our bet wins

    Returns dict with: full_kelly, quarter_kelly, position_fraction,
                       position_dollars, shares, actual_cost
    """
    payout_ratio      = (1.0 - kalshi_price) / kalshi_price
    p                 = pinnacle_prob
    q                 = 1.0 - p
    full_kelly        = (p * payout_ratio - q) / payout_ratio
    full_kelly        = max(0.0, full_kelly)
    quarter_kelly     = full_kelly * 0.25
    position_fraction = min(quarter_kelly, 0.10)
    position_dollars  = bankroll * position_fraction
    shares            = int(position_dollars / kalshi_price)
    actual_cost       = shares * kalshi_price

    return {
        "full_kelly":        round(full_kelly, 4),
        "quarter_kelly":     round(quarter_kelly, 4),
        "position_fraction": round(position_fraction, 4),
        "position_dollars":  round(position_dollars, 2),
        "shares":            shares,
        "actual_cost":       round(actual_cost, 2),
    }


def get_available_cash(conn: sqlite3.Connection) -> tuple[float, float]:
    """
    Returns (available_cash, total_bankroll).

    total_bankroll  = bankroll_start + sum(pnl_dollars on CLOSED trades)
    available_cash  = total_bankroll  - sum(actual_cost  on OPEN  trades)
    """
    row = conn.execute("SELECT bankroll_start FROM sandbox_config WHERE id = 1").fetchone()
    if not row:
        return 0.0, 0.0
    bankroll_start   = row[0]
    realized_pnl     = conn.execute(
        "SELECT COALESCE(SUM(pnl_dollars), 0) FROM sandbox_trades WHERE status = 'CLOSED'"
    ).fetchone()[0] or 0.0
    capital_deployed = conn.execute(
        "SELECT COALESCE(SUM(actual_cost), 0) FROM sandbox_trades WHERE status = 'OPEN'"
    ).fetchone()[0] or 0.0

    total_bankroll = bankroll_start + realized_pnl
    available_cash = total_bankroll - capital_deployed
    return round(available_cash, 2), round(total_bankroll, 2)
