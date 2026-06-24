# EdgeFund — Prediction Market Alpha System

![Python](https://img.shields.io/badge/Python-3.13-blue) ![Status](https://img.shields.io/badge/Status-Phase%201%20Live-green) ![Trading](https://img.shields.io/badge/Trading-Paper-yellow)

A paper-trading system that detects and logs pricing gaps between **Kalshi prediction markets** and **Pinnacle sportsbook**, with an AI research agent that filters each signal before logging. Built toward a statistical track record for a YC application.

---

## Table of Contents

1. [What This Project Does](#1-what-this-project-does)
2. [Project Goal & Timeline](#2-project-goal--timeline)
3. [Tech Stack](#3-tech-stack)
4. [Environment Setup](#4-environment-setup)
5. [.env File](#5-env-file)
6. [Directory & File Map](#6-directory--file-map)
7. [Data Flow](#7-data-flow)
8. [File-by-File Breakdown — Written Files](#8-file-by-file-breakdown--written-files)
9. [File-by-File Breakdown — Stubs](#9-file-by-file-breakdown--stubs)
10. [Data File Schemas](#10-data-file-schemas)
11. [Automation — LaunchAgents (macOS)](#11-automation--launchagents-macos)
12. [APIs — Keys, Endpoints, Cost](#12-apis--keys-endpoints-cost)
13. [Research Agent — Architecture](#13-research-agent--architecture)
14. [Running the Dashboard](#14-running-the-dashboard)
15. [Current Performance](#15-current-performance)
16. [Phase 2 Roadmap](#16-phase-2-roadmap)
17. [Known Issues & Edge Cases](#17-known-issues--edge-cases)
18. [What Is and Isn't Committed](#18-what-is-and-isnt-committed)

---

## 1. What This Project Does

**The thesis:** Kalshi retail bettors systematically misprice game contracts relative to Pinnacle's sharp consensus. When the gap between Kalshi's implied probability and Pinnacle's vig-free probability exceeds a threshold, we paper-trade the discrepancy and log outcomes to build a statistical track record.

**Example:**
```
Marlins win probability
  Kalshi:            49%
  Pinnacle (vig-free): 61%
  Gap:               -12.2%   (Kalshi underpricing the home team)

Signal: BUY_YES on KXMLBGAME-26JUN241210TEXMIA-MIA
```

The system is **not** live trading real money. It is building a statistical track record to demonstrate edge before deploying capital.

---

## 2. Project Goal & Timeline

**Target:** 30+ clean resolved Tier 1 trades with win rate ≥ 58% and p < 0.05

**Current state (June 24, 2026):**

| Metric | Value |
|---|---|
| Trades logged | 14 |
| Resolved | 5 (3W / 2L, 60% win rate) |
| Clean Tier 1 resolved | 4 (2W / 2L, 50%) |
| Open positions | 9 (mostly timing-suspect early snapshots) |
| p-value | 0.50 (need ~30 more trades) |

**Key deadlines:**
- **July 21, 2026** — TheOddsAPI business plan expires (renew or find alternative)
- **July 27, 2026** — YC application deadline

---

## 3. Tech Stack

| Layer | Library | Version |
|---|---|---|
| Language | Python | 3.13 |
| Package manager | Miniconda | — |
| Data | pandas, numpy, scipy | `>=2.0` |
| HTTP | requests | — |
| Env | python-dotenv | — |
| Logging | loguru | — |
| Dashboard | streamlit | 1.58.0 |
| Charts | plotly | 6.8.0 |
| Auto-refresh | streamlit-autorefresh | — |
| AI Agent | anthropic SDK | 0.111.0 |
| AI Model | claude-sonnet-4-6 | — |
| Testing | pytest | — |
| Automation | macOS LaunchAgents (launchd) | — |

---

## 4. Environment Setup

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/Ananthak2324/prediction-market-fund.git
cd prediction-market-fund

# 2. Install conda (macOS)
brew install --cask miniconda

# 3. Create the environment
conda create -n edgefund python=3.13
conda activate edgefund

# 4. Install all dependencies
pip install -r requirements.txt
```

> **Non-Mac:** The LaunchAgents won't work on Linux/Windows. Convert the `.plist` files to `systemd` services or `cron` jobs. The Python scripts themselves are cross-platform.

### Running scripts

Always run from the **project root**, not from inside `scripts/`:

```bash
cd "/path/to/prediction-market-fund"
python scripts/update_outcomes.py   # correct
```

Every script adds the project root to `sys.path` via:
```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

---

## 5. .env File

Create a file named `.env` in the project root. It is **never committed to git**.

```bash
cp .env.example .env
# then fill in your keys
```

```dotenv
ODDS_API_KEY=your_theoddsapi_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Optional — defaults shown:
KALSHI_API_BASE=https://api.elections.kalshi.com/trade-api/v2
ODDS_API_BASE=https://api.theoddsapi.com
```

| Key | Where to get it | Notes |
|---|---|---|
| `ODDS_API_KEY` | [the-odds-api.com](https://the-odds-api.com) | Business plan needed for historical pulls; free plan (500 req/mo) is too low for production |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | ~$0.013/trade with caching; agent defaults to MONITOR if key is missing |
| Kalshi | N/A | No key required for public market data |

---

## 6. Directory & File Map

```
prediction-market-fund/
│
├── .env                               ← NOT in git — create from .env.example
├── .env.example                       ← template
├── .gitignore
├── .streamlit/
│   └── config.toml                    ← dark theme
├── requirements.txt
├── README.md
│
├── com.predictionfund.snapshot.plist  ← LaunchAgent: schedule_snapshots.py (q10m)
├── com.predictionfund.outcomes.plist  ← LaunchAgent: update_outcomes.py (2 AM)
│
├── agent/
│   ├── research_agent.py              ← WRITTEN  — AI trade filter (513 lines)
│   ├── decision_engine.py             ← STUB     — Phase 2
│   └── signal_framework.py            ← STUB     — Phase 2
│
├── analysis/
│   ├── normalizer.py                  ← WRITTEN  — team name normalization
│   ├── backtest.py                    ← PARTIAL  — backtest scaffolding
│   ├── gap_calculator.py              ← PARTIAL  — gap computation helpers
│   └── merger.py                      ← PARTIAL  — joins Kalshi + Vegas data
│
├── config/
│   └── config.py                      ← WRITTEN  — env vars + constants
│
├── core/
│   ├── utils.py                       ← WRITTEN  — vig removal, prob conversion
│   ├── logger.py                      ← WRITTEN  — loguru setup
│   └── db.py                          ← STUB     — Phase 2 (SQLAlchemy ORM)
│
├── dashboard/
│   ├── app.py                         ← WRITTEN  — Streamlit terminal (903 lines)
│   ├── charts.py                      ← PARTIAL  — legacy matplotlib, unused
│   └── reporter.py                    ← STUB     — Phase 3
│
├── data/
│   ├── clients/
│   │   ├── kalshi_client.py           ← WRITTEN  — Kalshi REST API client
│   │   └── odds_client.py             ← WRITTEN  — TheOddsAPI client
│   ├── fetcher.py                     ← PARTIAL  — unified data fetching
│   ├── paper_trades.json              ← LIVE DATA — trade ledger (14 trades)
│   ├── performance_summary.json       ← LIVE DATA — aggregated stats + p-value
│   ├── agent_cost_log.csv             ← LIVE DATA — per-call Anthropic costs
│   ├── snapshots/                     ← NOT in git — runtime snapshot files
│   └── raw/                           ← NOT in git — historical odds pulls
│
├── scripts/
│   ├── schedule_snapshots.py          ← WRITTEN  — LaunchAgent data collector
│   ├── snapshot_gaps.py               ← WRITTEN  — manual snapshot runner
│   ├── update_outcomes.py             ← WRITTEN  — nightly outcome resolution
│   ├── fetch_odds_history.py          ← WRITTEN  — historical Pinnacle data pull
│   ├── pull_kalshi_markets.py         ← WRITTEN  — fetch Kalshi market list
│   ├── pull_kalshi_candles.py         ← WRITTEN  — fetch Kalshi price history
│   ├── backtest_gap.py                ← WRITTEN  — run historical gap analysis
│   ├── test_agent.py                  ← WRITTEN  — test agent on recent trades
│   └── test_favorites_filter.py       ← WRITTEN  — test favorites filter logic
│
└── tests/
    ├── test_agent_cost.py             ← WRITTEN  — validates agent cost/caching
    ├── test_merger.py                 ← PARTIAL
    ├── test_normalizer.py             ← PARTIAL
    └── test_utils.py                  ← PARTIAL
```

---

## 7. Data Flow

```
Every 10 minutes (LaunchAgent)
  schedule_snapshots.py
    ├── Poll Kalshi API for upcoming MLB/NBA games
    ├── For any game 110–130 min away (20-min window):
    │     ├── Fetch live Kalshi bid/ask → compute k_prob (mid-price)
    │     ├── Fetch Pinnacle moneyline → strip vig → v_prob
    │     ├── gap = k_prob - v_prob
    │     └── Save to data/snapshots/YYYY-MM-DD_HHMM.json
    └── Missed windows → data/snapshots/missed_snapshots.json

Every 2 AM daily (LaunchAgent)
  update_outcomes.py
    ├── Ingest new trades from snapshots (abs_gap >= 5%):
    │     ├── Call research_agent.run()  ← AI BARRIER
    │     │     ├── SKIP    → data/skipped_trades.json
    │     │     └── TRADE / MONITOR → data/paper_trades.json
    │     └── Replace early snapshots with clean 2h-window ones
    ├── Resolve settled trades via Kalshi API → update WIN/LOSS
    └── Rebuild data/performance_summary.json

On demand
  python scripts/update_outcomes.py   # run anytime
  python scripts/snapshot_gaps.py     # manual snapshot
  python scripts/test_agent.py        # test agent on recent trades
  streamlit run dashboard/app.py      # launch dashboard
```

### AI Agent barrier

For every new trade candidate before it's logged:

```
research_agent.run(game_dict)
  ├── Check 1: Re-fetch Pinnacle now
  │     If abs(current - snapshot) >= 3pp → auto SKIP (sharp money moved)
  ├── Check 2: Web search via Claude
  │     Query: "{home} {away} starting pitcher lineup injury scratch {date}"
  └── Check 3: Synthesize verdict as JSON
        TRADE / MONITOR → logged to paper_trades.json (with agent fields)
        SKIP            → logged to skipped_trades.json only
```

---

## 8. File-by-File Breakdown — Written Files

### `scripts/schedule_snapshots.py`

The core data collection engine. Runs every 10 minutes via LaunchAgent.

**What it does:**
1. Calls `GET /markets?series_ticker=KXMLBGAME` (and `KXNBAGAME`) to list all open games
2. For each game 110–130 minutes away:
   - Fetches live Kalshi bid/ask → `k_prob = (bid + ask) / 2`
   - Fetches Pinnacle moneyline from TheOddsAPI → strips vig → `v_prob`
   - Computes `gap = k_prob - v_prob`
   - Saves snapshot JSON to `data/snapshots/`
3. Games whose window passed without capture → `missed_snapshots.json`
4. Writes heartbeat to `scheduler_log.txt` every run

**Why 2 hours:** Starting lineups are confirmed, sharp money has moved in, but retail hasn't fully adjusted. Enough time remains for the gap to persist.

**Snapshot file format:**
```json
{
  "snapshot_time": "2026-06-23_2338",
  "rows": [
    {
      "sport": "MLB",
      "game": "Chicago Cubs @ New York Mets",
      "team": "New York Mets",
      "side": "HOME",
      "start_utc": "2026-06-26T02:10:00Z",
      "kalshi_ticker": "KXMLBGAME-...-NYM",
      "event_ticker": "KXMLBGAME-...",
      "k_prob": 0.505,
      "k_bid": 0.50,
      "k_ask": 0.51,
      "v_prob": 0.5087,
      "pinnacle_price": -114,
      "gap": -0.0037,
      "abs_gap": 0.0037,
      "fav_flag": false,
      "result": null
    }
  ]
}
```

> **Note:** All `snapshot_time` values are UTC and match the filename. The dashboard converts them to CT for display.

---

### `scripts/update_outcomes.py`

Nightly script that ingests new trades, resolves settled ones, and rebuilds performance stats.

**`ingest_new_trades()`**
- Reads all snapshot files, finds rows where `fav_flag=True` and `abs_gap >= 0.05`
- For each new game not yet in `paper_trades.json`:
  - Calls `research_agent.run()` — agent SKIP goes to `skipped_trades.json`
  - TRADE/MONITOR verdict attaches agent fields and logs the trade

**`resolve_trades()`**
- For each open trade where game started > 0.5h ago: fetches `GET /markets/{ticker}` and reads `settled_price` (1.0 = WIN, 0.0 = LOSS)

**`build_summary()`**
- Computes win rates by tier and gap bucket, p-value via `scipy.stats.binomtest`
- Saves to `performance_summary.json`

**Trade categories:**

| Category | Condition | Counts for YC stat? |
|---|---|---|
| Clean | snapshot ≤ 3h before game | Yes |
| Timing-suspect | snapshot > 3h before game | No |
| Tier 1 | abs_gap ≥ 10% | High conviction |
| Tier 2 | abs_gap 5–10% | Moderate conviction |

**Replacement logic:** If the same `event_ticker` appears in a later snapshot (closer to game time), the later snapshot replaces the earlier one in `paper_trades.json`.

---

### `agent/research_agent.py`

AI barrier between gap detection and trade logging. Uses Claude `claude-sonnet-4-6` with the `web_search_20250305` beta.

**Input:**
```python
{
    "home_team": "Miami Marlins",
    "away_team": "Texas Rangers",
    "sport": "MLB",
    "side": "HOME",
    "game_time": "2026-06-24T19:10:00Z",
    "snapshot_time": "2026-06-24T17:08:00Z",
    "kalshi_prob": 0.295,
    "pinnacle_prob": 0.55,
    "gap": -0.255,
    "tier": 1,
    "hours_before_game": 2.03,
    "timing_suspect": False
}
```

**Output:**
```python
{
    "recommendation": "SKIP",        # TRADE / SKIP / MONITOR
    "confidence": "HIGH",            # HIGH / MEDIUM / LOW
    "reasoning": "...",
    "gap_type": "INFORMATIONAL",     # BEHAVIORAL or INFORMATIONAL
    "news_found": True,
    "news_detail": "...",
    "pinnacle_stable": True,
    "pinnacle_movement": 0.01
}
```

**Decision rules:**

| SKIP if | TRADE if |
|---|---|
| Pitcher scratched | Roster confirmed, starter healthy |
| Key player injured | Pinnacle line stable 3h+ |
| Pinnacle moved ≥ 3pp | Behavioral bias (favorite-longshot) |
| Wind 15+ mph blowing in | No injury/weather news |

**Cost optimizations:**
- System prompt cached with `cache_control=ephemeral` → 90% cost reduction after first call (5-min TTL)
- Single web search per trade (was 4 separate queries)
- Actual cost: ~$0.010–$0.014/trade vs $0.05 budget

**Fallback:** Any exception → returns `recommendation=MONITOR`, trade is logged conservatively. The agent never blocks data collection.

---

### `dashboard/app.py`

Dark-theme Streamlit trading terminal. Auto-refreshes every 60 seconds.

**Run with:** `streamlit run dashboard/app.py`

**Tabs:**

| Tab | Contents |
|---|---|
| 📡 Live Gaps | All games from last 24h snapshots, deduplicated by `event_ticker`, sorted by gap. Filters out started games. |
| 📋 Trade Log | Full `paper_trades.json` with Sport/Outcome/Tier filters. Agent verdict columns. |
| 📈 Performance | 7-day rolling win rate chart, gap bucket bars, outcomes donut, agent stats, p-value callout. |
| ⚙️ System | Pipeline heartbeat, agent status, outcome updater last run, live trading readiness checklist. |

**Timezone:** All times in Central Time (CT). Snapshot filenames are UTC — `fmt_snap_time()` converts them.

---

### `core/utils.py`

Critical math shared across the project.

```python
american_to_prob(odds: int) -> float
# Positive (underdog): 100 / (odds + 100)
# Negative (favorite): abs(odds) / (abs(odds) + 100)

remove_vig(prob_a: float, prob_b: float) -> tuple[float, float]
# Divides each raw prob by their sum.
# e.g. (-150, +130) → raw (0.600, 0.435) → vig-free (0.579, 0.421)
```

Kalshi prices are already vig-free. Pinnacle embeds ~2–3% vig which must be removed before comparing the two.

---

### `data/clients/kalshi_client.py`

Thin wrapper around Kalshi's public REST API. No auth required.

```
Base URL: https://api.elections.kalshi.com/trade-api/v2

get_markets(series_ticker)   → list active markets
get_market(ticker)           → single market (includes settlement result)
get_orderbook(ticker)        → live bid/ask

Series tickers: KXMLBGAME (MLB), KXNBAGAME (NBA)
```

**Ticker format:** `KXMLBGAME-26JUN241210TEXMIA-MIA`
- `26JUN24` = June 24, 2026
- `1210` = 12:10 PM ET start
- `TEXMIA` = away-home abbreviations
- `-MIA` = YES side (home team wins)

---

### `data/clients/odds_client.py`

Wrapper around TheOddsAPI for Pinnacle moneylines. Requires `ODDS_API_KEY`.

```
get_odds(sport_key, markets, bookmakers)
  sport_key: "baseball_mlb" or "basketball_nba"

get_historical_odds(sport_key, date, markets, bookmakers)
  Requires Business plan. Used for backtest data collection only.
```

---

### `analysis/normalizer.py`

Maps all team name variants to a canonical form. Critical for joining Kalshi data (e.g., `"New York Yankees"`) with TheOddsAPI data (e.g., `"NY Yankees"`).

Contains `NBA_MAP` and `MLB_MAP` dicts. If you see merge failures, the team name is missing — add it here.

---

## 9. File-by-File Breakdown — Stubs

These files contain `raise NotImplementedError(...)`. **Do not import them in production code** without implementing first.

| File | Phase | Planned Purpose |
|---|---|---|
| `agent/decision_engine.py` | 2 | Position sizing: convert verdict + gap → trade size using Kelly criterion and bankroll limits |
| `agent/signal_framework.py` | 2 | Sport-specific system prompts (MLB starter, NBA back-to-backs, NFL injury report) |
| `core/db.py` | 2 | SQLAlchemy ORM migration from flat JSON (tables: TradeLog, GapRecord, AgentDecision) |
| `dashboard/reporter.py` | 3 | Morning email/Slack digest with overnight resolutions and p-value update |

**Also not yet built (no file exists):**
- `trading/live_trader.py` — actual Kalshi order placement
- `trading/position_manager.py` — open position tracking, P&L, max exposure
- `LIVE_MODE` flag — routes to real vs paper trades

---

## 10. Data File Schemas

### `data/paper_trades.json`

JSON array of trade objects. The track record — **commit this file with every update.**

```json
{
  "trade_id":           "2026-06-23_0048|KXMLBGAME-...",
  "snapshot_time":      "2026-06-23_0048",
  "sport":              "MLB",
  "game":               "Texas Rangers @ Miami Marlins",
  "team":               "Miami Marlins",
  "side":               "HOME",
  "start_utc":          "2026-06-24T19:10:00Z",
  "kalshi_ticker":      "KXMLBGAME-26JUN241210TEXMIA-MIA",
  "event_ticker":       "KXMLBGAME-26JUN241210TEXMIA",
  "k_prob":             0.49,
  "v_prob":             0.6102,
  "gap":                -0.1202,
  "abs_gap":            0.1202,
  "signal":             "BUY_YES",
  "outcome":            null,
  "correct":            null,
  "resolution_price":   null,
  "resolved_at":        null,
  "hours_before_game":  42.37,
  "timing_suspect":     true,
  "agent_verdict":      null,
  "agent_confidence":   null,
  "agent_reasoning":    null,
  "gap_type":           null,
  "news_found":         null,
  "pinnacle_stable":    null,
  "pinnacle_movement":  null
}
```

- `signal=BUY_YES` → Kalshi underpricing (k_prob < v_prob), buy the YES contract
- `signal=BUY_NO` → Kalshi overpricing (k_prob > v_prob), buy the NO contract
- `agent_verdict=null` on the 14 existing trades — they predate the agent wiring on June 24

---

### `data/performance_summary.json`

Regenerated on every `update_outcomes.py` run. Do not edit manually.

Key fields: `total_logged`, `total_resolved`, `win_rate_overall`, `win_rate_tier1`, `win_rate_tier2`, `p_value`, `by_gap_bucket`, `clean_trades`, `suspect_trades`, `agent_stats`

---

### `data/agent_cost_log.csv`

Appended on every `research_agent.run()` call.

Columns: `timestamp`, `game`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `search_calls`, `estimated_cost_usd`

**Pricing (claude-sonnet-4-6, June 2026):**

| Token type | Cost per million |
|---|---|
| Input | $3.00 |
| Cache read | $0.30 |
| Output | $15.00 |

---

### `data/skipped_trades.json`

Auto-created when agent returns `SKIP`. Contains full trade dict + agent verdict. Not in `paper_trades.json`. Useful for auditing agent accuracy over time.

---

## 11. Automation — LaunchAgents (macOS)

macOS uses `launchd` instead of `cron`. Two `.plist` files in the project root control the pipeline.

### `com.predictionfund.snapshot.plist` — every 10 minutes

```bash
# Install
cp com.predictionfund.snapshot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.predictionfund.snapshot.plist

# Check status
launchctl list | grep predictionfund

# Restart after code changes
launchctl kickstart -k gui/$(id -u)/com.predictionfund.snapshot

# Logs
tail -f data/snapshots/launchd_err.log
```

### `com.predictionfund.outcomes.plist` — 2:00 AM nightly

```bash
cp com.predictionfund.outcomes.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.predictionfund.outcomes.plist

# Logs
tail -f data/snapshots/outcomes_err.log
```

> **Important:** The Python path is hardcoded to `/opt/homebrew/Caskroom/miniconda/base/bin/python3`. If your conda lives elsewhere, update `ProgramArguments` in both `.plist` files before loading.

### Linux / non-Mac equivalent

```cron
*/10 * * * * /path/to/python /path/to/scripts/schedule_snapshots.py
0 2 * * *   /path/to/python /path/to/scripts/update_outcomes.py
```

---

## 12. APIs — Keys, Endpoints, Cost

### Kalshi REST API

```
Base URL: https://api.elections.kalshi.com/trade-api/v2
Auth: none required for public data
Rate limit: ~1 req/sec is safe (undocumented)

GET /markets?series_ticker=KXMLBGAME&status=open  → list open MLB markets
GET /markets/{ticker}                              → single market + settlement result
GET /markets/{ticker}/orderbook                    → live bid/ask
```

### TheOddsAPI

```
Base URL: https://api.theoddsapi.com
Auth: ODDS_API_KEY

GET /v4/sports/{sport_key}/odds
  ?apiKey=...&regions=us&markets=h2h&bookmakers=pinnacle
  sport_key: "baseball_mlb" or "basketball_nba"

GET /v4/sports/{sport_key}/odds-history   (Business plan required)
  ?apiKey=...&date={ISO}
```

We use **Pinnacle exclusively** as the sharp benchmark. Low vig (~2–3%), accepts sharp money, doesn't limit winners.

### Anthropic API

```
Model: claude-sonnet-4-6
Beta: web-search-2025-03-05

client.beta.messages.create(
    betas=["web-search-2025-03-05"],
    tools=[{"type": "web_search_20250305", "name": "web_search"}]
)
```

Web search runs **server-side** inside Claude — no `tool_use` blocks appear in the response. `stop_reason = "end_turn"` directly.

---

## 13. Research Agent — Architecture

The agent is called from `update_outcomes.py` for every new trade candidate and acts as a barrier: only `TRADE` or `MONITOR` verdicts reach `paper_trades.json`.

```
update_outcomes.py
  → _build_agent_game_dict(trade)
  → research_agent.run(game_dict)
       → _fetch_current_pinnacle()     Check 1: re-fetch Pinnacle, compare to snapshot
       → _build_user_message()         Build prompt with game context
       → _make_api_call()              Claude + web search
       → parse JSON from response
       → _print_and_log_cost()
       → return verdict
  → SKIP: append to skipped_trades.json, continue
  → TRADE/MONITOR: attach agent fields, append to paper_trades.json
```

**Prompt design:**
- System prompt (~1,500 tokens) is cached after first call — 90% cost reduction
- User message is built fresh per trade with game details, gap magnitude, stability result, and pre-built search query
- Early snapshots (`hours_before_game > 3`) and large gaps (`abs_gap >= 0.20`) trigger extra scrutiny warnings

**Error handling:** Any exception returns `recommendation=MONITOR`. The agent is never a point of failure that blocks data collection.

---

## 14. Running the Dashboard

```bash
# From project root:
streamlit run dashboard/app.py
```

Opens at [http://localhost:8501](http://localhost:8501). Auto-refreshes every 60 seconds.

All times are displayed in **Central Time (CT)**. To change timezone, update `ET = ZoneInfo("America/Chicago")` in `dashboard/app.py`.

For network access:
```bash
streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port 8501
```

---

## 15. Current Performance

*As of June 24, 2026*

```
14 trades logged
 5 resolved  →  3W / 2L  (60% overall)
 4 clean Tier 1 resolved  →  2W / 2L  (50%)
 9 open (mostly timing-suspect from early snapshots)

p-value: 0.50  →  need ~30 resolved clean trades for p < 0.05
```

**Win rate needed for significance:**

| Trades | Win rate for p ≈ 0.10 | Win rate for p ≈ 0.05 |
|---|---|---|
| 30 | 60% (18W/12L) | 67% (20W/10L) |
| 50 | 58% (29W/21L) | 62% (31W/19L) |

**Projection:** ~1–3 Tier 1 signals per day in MLB season → 25–40 resolved trades by July 27.

---

## 16. Phase 2 Roadmap

In order of priority:

1. **Live trading module** (`trading/live_trader.py`)
   - Kalshi order placement API (requires trading-enabled API key)
   - `LIVE_MODE` flag to switch from paper to real trades

2. **Position manager** (`trading/position_manager.py`)
   - Open P&L tracking, max exposure per game/day
   - Kelly criterion sizing based on gap magnitude and win rate

3. **`agent/decision_engine.py`**
   - Convert agent verdict + gap → position size
   - Bankroll percentage limits, max daily exposure

4. **`agent/signal_framework.py`**
   - Sport-specific system prompts (MLB starter, NBA rest/back-to-back, NFL official injury report)

5. **`core/db.py`**
   - SQLite migration via SQLAlchemy when trades exceed ~500
   - Tables: `TradeLog`, `GapRecord`, `AgentDecision`

6. **`dashboard/reporter.py`**
   - Morning email/Slack digest: resolutions, new trades, win rate, p-value

7. **NFL extension (September 2026)**
   - `KXNFLGAME` series on Kalshi
   - `american_football_nfl` on TheOddsAPI

---

## 17. Known Issues & Edge Cases

**Snapshot UTC vs display CT**
All snapshot filenames are UTC. A snapshot taken at 11 PM CT has a filename from the next UTC day. The dashboard handles this with a 24-hour lookback window rather than filtering by date string.

**Timing-suspect open trades**
The 9 open trades are early snapshots (`hours_before_game > 3`). They will be replaced when a clean 2h-window snapshot fires. Until then, they sit as open in `paper_trades.json`.

**Agent fields null on existing trades**
All 14 current trades predate agent wiring (June 24). Their `agent_verdict` fields are null. `agent_stats` in `performance_summary.json` will stay at zero until post-agent trades resolve.

**Kalshi API pagination**
`/markets` returns up to 200 results. The current code doesn't paginate. Fine for MLB mid-season; may truncate near season start.

**Team name matching**
Kalshi ↔ TheOddsAPI matching uses team name normalization + ±2h time window. If a team isn't in `analysis/normalizer.py`, the match silently fails and the snapshot won't have Pinnacle data. Fix: add the team to the normalizer maps.

**No NBA games until October**
NBA Finals ended. The system handles 0 games gracefully.

---

## 18. What Is and Isn't Committed

**Committed (source of truth):**
- All `.py` files (including stubs)
- `requirements.txt`, `README.md`, `.env.example`
- `.streamlit/config.toml`
- `.plist` automation files
- `data/paper_trades.json` — **this is the track record, commit it with every update**
- `data/performance_summary.json`
- `data/agent_cost_log.csv`

**Not committed (gitignored):**
- `.env` — contains API keys, **never commit**
- `data/snapshots/` — ephemeral runtime data (~100MB/month)
- `data/raw/` — large historical odds pulls (~240 JSON files)
- `__pycache__/`, `*.pyc`, `.DS_Store`

> **Before committing:** run `python scripts/update_outcomes.py` to ensure `paper_trades.json` has all current resolutions, then commit that file along with your code changes so the git history tracks both the codebase and the live track record together.
