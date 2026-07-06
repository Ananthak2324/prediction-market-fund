# EdgeFund — Full System Stats

**Generated:** 2026-07-06 03:53 UTC · Desks: MLB, WNBA

---

## Headline

| Metric | Value |
|---|---|
| Trades logged | 38 |
| Paused (excluded, pre-rebuild contamination) | 12 |
| Resolved | 21 |
| Valid (for analysis) | 17 |
| Open | 5 |
| Win rate (overall, valid) | 64.7% |
| P-value (vs. 50% null) | 0.1662 |
| Avg gap — winners | 9.0% |
| Avg gap — losers | 11.4% |

## Tier Performance

| Tier | Status | Kelly x | Resolved | Wins | Win Rate | P-value | EV/$ |
|---|---|---|---|---|---|---|---|
| A | ACTIVE_FULL | 25% | 11 | 9 | 81.8% | 0.0327 | 0.6068 |
| B | ACTIVE_REDUCED | 10% | 8 | 3 | 37.5% | 0.8555 | 0.1308 |
| C | SHADOW_ONLY | 0% | 0 (shadow) | 0 | — | — | — |

*Tier B validation: 8/20 resolved trades needed. 20 resolved trades with win rate > 55% upgrades to full sizing; 20 resolved trades with win rate < 45% moves to shadow only*

*Tier C note: EV -1.00 confirmed — investigating*

## Signal Performance

| Signal | Status | Resolved | Win Rate |
|---|---|---|---|
| BUY_YES | ACTIVE | 16 | 62.5% |
| BUY_NO | SHADOW_ONLY | 0 (shadow) | — |

*BUY_NO note: 37.5% WR — suspended pending investigation*

## Win Rate by Gap Bucket

| Bucket | Trades | Win Rate |
|---|---|---|
| 10-15% | 8 | 37.5% |
| 15-plus% | 2 | 50.0% |
| 5-7% | 7 | 85.7% |
| 7-10% | 4 | 75.0% |

## Win Rate by Book

| Book | Trades | Win Rate |
|---|---|---|
| pinnacle | 21 | 61.9% |

## Clean vs. Timing-Suspect Trades

**Clean (≤3h before game):** 4 resolved (1W/3L), win rate 25.0%, 0 open, p=—
**Suspect (>3h before game):** 13 resolved (10W/3L), win rate 76.9%, 5 open, p=0.0461

## Research Agent Stats

| Metric | Value |
|---|---|
| Total evaluated | 154 |
| Trade recommendations | 12 |
| Skip recommendations | 142 |
| Skip rate | 92.2% |
| Win rate (agent-approved) | 28.6% |
| Win rate (unvetted / suspect) | 78.6% |
| High confidence win rate | 0.0% |
| Medium confidence win rate | 40.0% |
| News found rate | 80.5% |
| Pinnacle unstable rate | 46.8% |
| Shadow resolved (skip-decision audit) | 135 |
| Shadow win rate (what skipped trades would've done) | 37.8% |

## Portfolio Metrics

| Metric | Value |
|---|---|
| Avg EV per trade | 0.453 |
| Sandbox Sharpe | — |
| Sandbox max drawdown | — |
| Sandbox total return | — |
| Sandbox closed trades | 0 |

**EV by gap bucket:**
- 10-15%: 0.2924
- 5-7%: 0.6244
- 7-10%: 0.428

## Risk Controls

| Metric | Value |
|---|---|
| Current drawdown | 0.0% |
| Circuit breaker active | False |
| Max concurrent positions | 4 |
| Concurrent open now | 0 |

## Sandbox

| Metric | Value |
|---|---|
| Start date | 2026-07-05 |
| Starting bankroll | $1,000.00 |
| Current bankroll | $1,000.00 |
| Realized P&L | $0.00 |
| Open positions | 0 |
| Closed positions | 0 |

## Agent Cost

| Metric | Value |
|---|---|
| Total Anthropic calls logged | 0 |
| Total estimated cost | $0.00 |
| Avg cost per call | — |

## Edge Discovery Funnel (most recent cycle per desk)

**MLB** (as of —):
- run_at: 2026-07-05T05:27:31.003201+00:00
- sport: MLB
- total_scanned: 28
- above_threshold: 0
- already_traded: 0
- on_cooldown: 0
- pre_filter_skipped: 0
- researched: 0
- trade_verdicts: 0
- skip_verdicts: 0
- monitor_verdicts: 0
- shadow_verdicts: 0
- api_cost_usd: 0.0

**WNBA** (as of —):
- run_at: 2026-07-05T05:27:31.241321+00:00
- sport: WNBA
- total_scanned: 0
- above_threshold: 0
- already_traded: 0
- on_cooldown: 0
- pre_filter_skipped: 0
- researched: 0
- trade_verdicts: 0
- skip_verdicts: 0
- monitor_verdicts: 0
- shadow_verdicts: 0
- api_cost_usd: 0.0

## Skipped Trades

Total logged: 142

## Shadow Trades

Total logged: 0
Resolved: 0

