# EdgeFund — Prediction Market Alpha System

![Python](https://img.shields.io/badge/Python-3.13-blue) ![Status](https://img.shields.io/badge/Status-Phase%202%20Live-green) ![Trading](https://img.shields.io/badge/Trading-Paper%20%2B%20Sandbox-yellow)

A paper-trading system that detects and logs pricing gaps between **Kalshi prediction markets** and **Pinnacle sportsbook**, with an AI research agent that filters each signal before logging, and a sandbox portfolio simulation layer that applies Kelly-criterion position sizing and tracks simulated P&L in real time.

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
14. [Sandbox Portfolio Simulation](#14-sandbox-portfolio-simulation)
15. [Running the Dashboard](#15-running-the-dashboard)
16. [Current Performance](#16-current-performance)
17. [Phase 3 Roadmap](#17-phase-3-roadmap)
18. [Known Issues & Edge Cases](#18-known-issues--edge-cases)
19. [What Is and Isn't Committed](#19-what-is-and-isnt-committed)

---

## 1. What This Project Does

**The thesis:** Kalshi retail bettors systematically misprice game contracts relative to Pinnacle's sharp consensus. When the gap between Kalshi's implied probability and Pinnacle's vig-free probability exceeds a threshold, we paper-trade the discrepancy and log outcomes to build a statistical track record.

**Example:**
```
Marlins win probability
  Kalshi:              49%
  Pinnacle (vig-free): 61%
  Gap:                 -12.2%   (Kalshi underpricing the home team)

Signal: BUY_YES on KXMLBGAME-26JUN241210TEXMIA-MIA
```

The system is **not** live trading real money. It is building a statistical track record to demonstrate edge before deploying capital. A parallel **sandbox simulation** applies real position sizing to every paper trade, so we can also track what the P&L curve would look like on a $1,000 bankroll.

---

## 2. Project Goal & Timeline

**Target:** 30+ clean resolved Tier 1 trades with win rate ≥ 58% and p < 0.05

**Current state (June 24, 2026):**

| Metric | Value |
|---|---|
| Trades logged | 14 |
| Valid resolved | 2 (both suspect wins, 100%) |
| Excluded (post-game snapshots) | 4 |
| Clean (≤3h) resolved | 0 — still building |
| Open positions | 8 |
| Sandbox bankroll | $1,000.00 |
| Sandbox deployed | $77.09 (2 open positions) |

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
| Database | SQLite (stdlib sqlite3) | — |
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

### One-time sandbox setup

```bash
python scripts/backfill_sandbox.py   # creates DB, opens positions for Jun 25+ trades
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
│   ├── research_agent.py              ← WRITTEN  — AI trade filter
│   ├── decision_engine.py             ← STUB     — Phase 3
│   └── signal_framework.py            ← STUB     — Phase 3
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
│   ├── utils.py                       ← WRITTEN  — vig removal, prob conversion, ticker_to_utc
│   ├── logger.py                      ← WRITTEN  — loguru setup
│   └── db.py                          ← STUB     — Phase 3 (SQLAlchemy ORM)
│
├── dashboard/
│   ├── app.py                         ← WRITTEN  — Streamlit terminal (5 tabs)
│   ├── charts.py                      ← PARTIAL  — legacy matplotlib, unused
│   └── reporter.py                    ← STUB     — Phase 3
│
├── data/
│   ├── clients/
│   │   ├── kalshi_client.py           ← WRITTEN  — Kalshi REST API client
│   │   └── odds_client.py             ← WRITTEN  — TheOddsAPI client
│   ├── fetcher.py                     ← PARTIAL  — unified data fetching
│   ├── paper_trades.json              ← LIVE DATA — trade ledger
│   ├── performance_summary.json       ← LIVE DATA — aggregated stats + p-value
│   ├── paper_trades.db                ← LIVE DATA — sandbox SQLite DB
│   ├── agent_cost_log.csv             ← LIVE DATA — per-call Anthropic costs
│   ├── skipped_trades.json            ← LIVE DATA — agent-rejected trade candidates
│   ├── snapshots/                     ← NOT in git — runtime snapshot files
│   └── raw/                           ← NOT in git — historical odds pulls
│
├── execution/
│   ├── position_sizer.py              ← WRITTEN  — Quarter-Kelly sizing + cash tracking
│   ├── position_manager.py            ← WRITTEN  — sandbox DB open/poll/settle
│   ├── paper_trader.py                ← STUB     — Phase 3 (live order router)
│   └── live_trader.py                 ← STUB     — Phase 3 (Kalshi order placement)
│
├── scripts/
│   ├── schedule_snapshots.py          ← WRITTEN  — LaunchAgent data collector
│   ├── snapshot_gaps.py               ← WRITTEN  — manual snapshot runner
│   ├── update_outcomes.py             ← WRITTEN  — nightly outcome resolution + sandbox settle
│   ├── backfill_sandbox.py            ← WRITTEN  — one-time sandbox DB setup
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
    ├── Derive game start time from ticker (NOT occurrence_datetime — see §18)
    ├── For any game 110–130 min away (20-min window):
    │     ├── Fetch live Kalshi bid/ask → compute k_prob (mid-price)
    │     ├── Fetch Pinnacle moneyline → strip vig → v_prob
    │     ├── gap = k_prob - v_prob
    │     └── Save to data/snapshots/YYYY-MM-DD_HHMM.json
    └── Missed windows → data/snapshots/missed_snapshots.json

Every 2 AM daily (LaunchAgent)
  update_outcomes.py
    ├── Ingest new trades from snapshots (abs_gap >= 5%):
    │     ├── Filter post-game snapshots via ticker_to_utc()
    │     ├── Call research_agent.run()  ← AI BARRIER
    │     │     ├── SKIP    → data/skipped_trades.json
    │     │     └── TRADE / MONITOR → data/paper_trades.json
    │     ├── Replace early snapshots with clean 2h-window ones
    │     └── Open sandbox position for Jun 25+ trades
    ├── Resolve settled trades via Kalshi API → update WIN/LOSS
    ├── Settle sandbox positions at resolution (1.0 WIN / 0.0 LOSS)
    └── Rebuild data/performance_summary.json

Live poll (run manually or as LaunchAgent)
  python execution/position_manager.py
    └── Every 60s during 12PM–11PM ET:
          For each OPEN sandbox position:
            Fetch current Kalshi price
            Apply exit rules (FAIR_VALUE / STOP_LOSS / PROFIT_TARGET / NEAR_RESOLUTION)
            Close position + update data/paper_trades.db

On demand
  python scripts/update_outcomes.py   # run anytime
  python scripts/snapshot_gaps.py     # manual snapshot
  python scripts/backfill_sandbox.py  # one-time sandbox setup
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

### `core/utils.py`

Critical math and timing utilities shared across the project.

```python
ticker_to_utc(event_ticker: str) -> datetime | None
# Parses the accurate game start time from the Kalshi event ticker.
# Ticker format: KXMLBGAME-26JUN241210TEXMIA → June 24 2026, 12:10 PM ET → 16:10 UTC
# Kalshi's occurrence_datetime has a known 3-hour UTC/ET confusion error.
# The ticker itself is always accurate — this is the canonical source.

american_to_prob(odds: int) -> float
# Positive (underdog): 100 / (odds + 100)
# Negative (favorite): abs(odds) / (abs(odds) + 100)

remove_vig(home_odds, away_odds) -> tuple[float, float]
# Divides each raw prob by their sum.
# e.g. (-150, +130) → raw (0.600, 0.435) → vig-free (0.579, 0.421)
```

> **Why `ticker_to_utc`:** The Kalshi API's `occurrence_datetime` field stores Eastern Time values labelled as UTC, causing a systematic ~4-hour error (EDT) on all market start times. Every component that needs a game start time calls `ticker_to_utc()` instead.

---

### `scripts/schedule_snapshots.py`

The core data collection engine. Runs every 10 minutes via LaunchAgent.

**What it does:**
1. Calls `GET /markets?series_ticker=KXMLBGAME` to list all open games
2. Uses `ticker_to_utc()` (not `occurrence_datetime`) to get accurate start times
3. For each game 110–130 minutes away:
   - Fetches live Kalshi bid/ask → `k_prob = (bid + ask) / 2`
   - Fetches Pinnacle moneyline → strips vig → `v_prob`
   - Computes `gap = k_prob - v_prob`
   - Saves snapshot JSON
4. Games whose window was missed → `missed_snapshots.json`
5. Writes heartbeat to `scheduler_log.txt` every run

> **Laptop note:** LaunchAgents only run when the Mac is awake. If the lid is closed during a game's 20-minute window, that snapshot is missed.

---

### `scripts/update_outcomes.py`

Nightly script (also run manually) that ingests new trades, resolves settled ones, and rebuilds performance stats.

**`ingest_new_trades()`**
- Reads all snapshot files, finds rows where `abs_gap >= 0.05`
- Derives accurate game start time via `ticker_to_utc()` — skips any snapshot taken **after** game start (in-game price, not pre-game)
- For each new game not yet in `paper_trades.json`:
  - Calls `research_agent.run()` — agent SKIP goes to `skipped_trades.json`
  - Calls `open_sandbox_position()` for Jun 25+ trades
- If a cleaner snapshot (≤3h) arrives for an existing timing-suspect trade, replaces prices/timing while preserving any resolved outcome

**`resolve_trades()`**
- For each open trade where game started > 0.5h ago: fetches `GET /markets/{ticker}` and reads `result` field

**`settle_resolved_positions()`**
- After resolution: closes matching OPEN sandbox positions at resolution_price (1.0 WIN / 0.0 LOSS)

**Trade categories:**

| Category | Condition | Counts for analysis? |
|---|---|---|
| Clean | snapshot ≤ 3h before game | Yes — primary track record |
| Timing-suspect | snapshot > 3h before game | Yes — but flagged |
| Invalid | snapshot taken after game started | No — excluded |
| Tier 1 | abs_gap ≥ 10% | High conviction |
| Tier 2 | abs_gap 5–10% | Moderate conviction |

---

### `execution/position_sizer.py`

Quarter-Kelly position sizing for Kalshi YES/NO contracts.

```python
calculate_position(bankroll, kalshi_price, pinnacle_prob) -> dict
```

Entry prices are always the contract we're buying:
- `BUY_YES` → `kalshi_price = k_prob`, `pinnacle_prob = v_prob`
- `BUY_NO`  → `kalshi_price = 1 - k_prob`, `pinnacle_prob = 1 - v_prob`

**Kelly formula:**
```
payout_ratio    = (1 - kalshi_price) / kalshi_price
full_kelly      = (p × payout_ratio − q) / payout_ratio
quarter_kelly   = full_kelly × 0.25
position_frac   = min(quarter_kelly, 0.10)   ← hard cap at 10%
shares          = int(bankroll × position_frac / kalshi_price)
```

**Example (LAD @ MIN, 32% Kalshi / 44.4% Pinnacle):**
```
Payout ratio  : 2.125x
Full Kelly    : 18.25%
Quarter Kelly : 4.56%
Position      : $45.62 → 142 shares × $0.32 = $45.44
```

---

### `execution/position_manager.py`

Manages the sandbox SQLite DB and position lifecycle. Three main functions:

**`init_db()`** — creates tables, inserts `$1,000` config row (idempotent)

**`open_sandbox_position(paper_trade_dict)`** — called for every new Jun 25+ paper trade:
1. Checks entry_date ≥ 2026-06-25 (ET date of game start)
2. Calculates position via `calculate_position()`
3. Skips if shares == 0 or cost > available cash
4. Inserts OPEN row in `sandbox_trades`, logs to `sandbox_bankroll_history`

**`poll_open_positions()`** — called every 60s during game hours (12PM–11PM ET):

| Rule | Condition | Exit type |
|---|---|---|
| Fair value | current_price ≥ pinnacle_prob | `FAIR_VALUE` |
| Stop loss | P&L ≤ −40% | `STOP_LOSS` |
| Profit target | P&L ≥ +80% | `PROFIT_TARGET` |
| Near resolution | < 2h to est. game end AND P&L > +10% | `NEAR_RESOLUTION` |

**`settle_resolved_positions(paper_trades)`** — called by `update_outcomes.py` at resolution

Run the poll loop as a standalone process:
```bash
python execution/position_manager.py
```

---

### `scripts/backfill_sandbox.py`

One-time setup script. Run once after cloning:
```bash
python scripts/backfill_sandbox.py
```
1. Creates `data/paper_trades.db` with 3 tables
2. Inserts `$1,000` sandbox config row
3. Opens positions for all paper trades with game date ≥ June 25, 2026

---

### `scripts/snapshot_gaps.py`

Manual snapshot runner. Also called by `schedule_snapshots.py` when a game is in window.

- Uses `ticker_to_utc()` to write accurate `start_utc` in every snapshot row
- Flags rows where `abs_gap >= 5%`
- Saves to `data/snapshots/YYYY-MM-DD_HHMM.json` and appends to `master_log.json`

---

### `agent/research_agent.py`

AI barrier between gap detection and trade logging. Uses `claude-sonnet-4-6` with `web_search_20250305` beta.

**Decision rules:**

| SKIP if | TRADE if |
|---|---|
| Pitcher scratched | Roster confirmed, starter healthy |
| Key player injured | Pinnacle line stable 3h+ |
| Pinnacle moved ≥ 3pp | Behavioral bias (favorite-longshot) |
| Wind 15+ mph blowing in | No injury/weather news |

**Cost:** ~$0.010–$0.014/trade with prompt caching (90% reduction after first call per session)

---

### `dashboard/app.py`

Dark-theme Streamlit trading terminal. Auto-refreshes every 60 seconds.

**Run with:** `streamlit run dashboard/app.py`

**Tabs:**

| Tab | Contents |
|---|---|
| 📡 Live Gaps | All games from last 24h snapshots, deduplicated by event_ticker, sorted by gap. Filters out started games. |
| 📋 Trade Log | Full paper_trades.json with Sport / Outcome / Tier / Game Date filters. Shows Game Date, Game Start, Captured time, Signal + team name, Agent verdict. |
| 📈 Performance | 7-day rolling win rate chart, gap bucket bars, outcomes donut, agent stats, p-value callout. |
| 💰 Sandbox | Bankroll over time, P&L per trade, open positions with live Kalshi prices and unrealized P&L, closed positions table, exit rule breakdown. |
| ⚙️ System | Pipeline heartbeat, agent status, outcome updater last run, live trading readiness checklist. |

**Timezone:** All times in Central Time (CT). Snapshot filenames are UTC — `fmt_snap_time()` converts them.

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
- `26` = year 2026, `JUN` = June, `24` = 24th, `1210` = 12:10 PM **Eastern Time**
- `TEXMIA` = away-home abbreviations, `-MIA` = YES side (home team wins)

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

## 9. File-by-File Breakdown — Stubs

These files contain `raise NotImplementedError(...)`. **Do not import them in production code** without implementing first.

| File | Phase | Planned Purpose |
|---|---|---|
| `agent/decision_engine.py` | 3 | Convert agent verdict + gap into live order parameters |
| `agent/signal_framework.py` | 3 | Sport-specific system prompts (MLB, NBA, NFL) |
| `core/db.py` | 3 | SQLAlchemy ORM migration from flat JSON (paper_trades.json → SQL) |
| `execution/paper_trader.py` | 3 | Route TRADE decisions to Kalshi order placement |
| `execution/live_trader.py` | 3 | Actual Kalshi REST order API calls |
| `dashboard/reporter.py` | 3 | Morning email/Slack digest |

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
  "start_utc":          "2026-06-24T16:10:00Z",
  "kalshi_ticker":      "KXMLBGAME-26JUN241210TEXMIA-MIA",
  "event_ticker":       "KXMLBGAME-26JUN241210TEXMIA",
  "k_prob":             0.49,
  "v_prob":             0.6102,
  "gap":                -0.1202,
  "abs_gap":            0.1202,
  "signal":             "BUY_YES",
  "hours_before_game":  39.37,
  "timing_suspect":     true,
  "valid_for_analysis": true,
  "outcome":            null,
  "correct":            null,
  "resolution_price":   null,
  "resolved_at":        null,
  "agent_verdict":      null,
  "agent_confidence":   null,
  "agent_reasoning":    null,
  "gap_type":           null,
  "news_found":         null,
  "pinnacle_stable":    null,
  "pinnacle_movement":  null,
  "replacement_flags":  []
}
```

- `valid_for_analysis=false` means the snapshot was taken **after** game start — excluded from all statistics
- `start_utc` is derived from the ticker via `ticker_to_utc()`, not from Kalshi's `occurrence_datetime`

---

### `data/performance_summary.json`

Regenerated on every `update_outcomes.py` run. Do not edit manually.

Key fields: `total_logged`, `total_resolved`, `total_valid`, `total_excluded`, `win_rate_overall`, `win_rate_tier1`, `p_value`, `by_gap_bucket`, `clean_trades`, `suspect_trades`, `agent_stats`

---

### `data/paper_trades.db` — SQLite

Three tables managed by `execution/position_manager.py`.

**`sandbox_config`** (one row)
```
id              INTEGER  DEFAULT 1
bankroll_start  REAL     DEFAULT 1000.00
start_date      DATE     DEFAULT '2026-06-25'
created_at      DATETIME
```

**`sandbox_trades`**
```
id                INTEGER  PK AUTOINCREMENT
paper_trade_id    TEXT     → trade_id from paper_trades.json
entry_date        DATE     ET date of game start
game              TEXT
home_team / away_team TEXT
kalshi_ticker     TEXT     individual market ticker for live price fetch
signal            TEXT     BUY_YES | BUY_NO
tier              INTEGER  1 or 2
gap               REAL
entry_price       REAL     k_prob (BUY_YES) or 1-k_prob (BUY_NO)
pinnacle_prob     REAL     v_prob (BUY_YES) or 1-v_prob (BUY_NO)
full_kelly        REAL
quarter_kelly     REAL
position_fraction REAL
shares            INTEGER
actual_cost       REAL
bankroll_before   REAL
start_utc         TEXT     for near-resolution exit rule
exit_price        REAL     null until closed
exit_type         TEXT     FAIR_VALUE | STOP_LOSS | PROFIT_TARGET | NEAR_RESOLUTION | RESOLUTION
exit_time         DATETIME
resolution_price  REAL
pnl_dollars       REAL
pnl_pct           REAL
bankroll_after    REAL
status            TEXT     OPEN | CLOSED
created_at        DATETIME
```

**`sandbox_bankroll_history`**
```
id          INTEGER  PK
timestamp   DATETIME
bankroll    REAL     available cash after TRADE_OPEN; total equity after TRADE_CLOSE
event_type  TEXT     TRADE_OPEN | TRADE_CLOSE
trade_id    INTEGER  → sandbox_trades.id
note        TEXT
```

---

### `data/agent_cost_log.csv`

Appended on every `research_agent.run()` call.

Columns: `timestamp`, `game`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `search_calls`, `estimated_cost_usd`

---

### `data/skipped_trades.json`

Auto-created when agent returns `SKIP`. Contains full trade dict + agent verdict. Useful for auditing agent accuracy over time.

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
tail -f data/snapshots/scheduler_log.txt
```

### `com.predictionfund.outcomes.plist` — 2:00 AM nightly

```bash
cp com.predictionfund.outcomes.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.predictionfund.outcomes.plist
```

> **Important:** LaunchAgents only run when the Mac is awake. If the laptop is sleeping during a game's 20-minute snapshot window, that window is missed. For reliable data collection, consider running the scheduler on a cloud VPS (~$4/mo DigitalOcean) that stays on 24/7.

> **Python path:** hardcoded to `/opt/homebrew/Caskroom/miniconda/base/bin/python3`. Update `ProgramArguments` in both `.plist` files if your conda lives elsewhere.

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
```

### TheOddsAPI

```
Base URL: https://api.theoddsapi.com
Auth: ODDS_API_KEY

GET /v4/sports/{sport_key}/odds
  ?apiKey=...&regions=us&markets=h2h&bookmakers=pinnacle
  sport_key: "baseball_mlb" or "basketball_nba"
```

We use **Pinnacle exclusively** as the sharp benchmark. Low vig (~2–3%), accepts sharp money, doesn't limit winners.

### Anthropic API

```
Model: claude-sonnet-4-6
Beta: web-search-2025-03-05
Cost: ~$0.010–$0.014/trade (with prompt caching)
```

---

## 13. Research Agent — Architecture

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
  → open_sandbox_position(trade)       Jun 25+ trades get sandbox position
```

**Error handling:** Any exception returns `recommendation=MONITOR`. The agent never blocks data collection.

---

## 14. Sandbox Portfolio Simulation

The sandbox is a simulation layer that sits on top of paper trading. It does **not** replace or modify `paper_trades.json` — it is additive.

**What it tracks:**
- Simulated $1,000 bankroll starting June 25, 2026
- Quarter-Kelly position sizing on every Jun 25+ paper trade
- Intra-game exit rules (fair value, stop loss, profit target, near resolution)
- Final resolution settlement (WIN = $1.00/share, LOSS = $0.00/share)
- Full bankroll history for charting

**Quick start:**
```bash
# Initialize and backfill
python scripts/backfill_sandbox.py

# Start live poll loop (run in background)
python execution/position_manager.py
```

**Kelly example (LAD @ MIN, 32% Kalshi, 44.4% Pinnacle):**
```
Full Kelly    : 18.25%   (what pure Kelly says to bet)
Quarter Kelly :  4.56%   (our fraction)
Hard cap      : 10.00%   (max per position)
Position      : $45.62 → 142 shares × $0.32
```

---

## 15. Running the Dashboard

```bash
streamlit run dashboard/app.py
```

Opens at [http://localhost:8501](http://localhost:8501). Auto-refreshes every 60 seconds.

All times displayed in **Central Time (CT)**. To change timezone, update `ET = ZoneInfo("America/Chicago")` in `dashboard/app.py`.

---

## 16. Current Performance

*As of June 24, 2026*

```
14 trades logged
 6 resolved:
   2 valid (both timing-suspect wins)  →  100% win rate
   4 excluded (post-game snapshots, valid_for_analysis=False)
 8 open (all timing-suspect, waiting for clean 2h-window replacements)

Clean (≤3h) resolved: 0  →  need 30 for p < 0.05
p-value: N/A  (insufficient clean resolved trades)
```

**Win rate needed for significance (clean trades only):**

| Trades | Win rate for p ≈ 0.10 | Win rate for p ≈ 0.05 |
|---|---|---|
| 30 | 60% (18W/12L) | 67% (20W/10L) |
| 50 | 58% (29W/21L) | 62% (31W/21L) |

**Projection:** ~1–3 Tier 1 clean signals per day → 25–40 resolved clean trades by July 27.

---

## 17. Phase 3 Roadmap

In order of priority:

1. **Move scheduler to cloud** — VPS (~$4/mo) so snapshot windows aren't missed when laptop sleeps

2. **Live trading module** (`execution/live_trader.py`)
   - Kalshi order placement API (requires trading-enabled API key)
   - `LIVE_MODE` flag to switch from paper to real trades

3. **`agent/decision_engine.py`** — convert verdict + gap → live order parameters

4. **`agent/signal_framework.py`** — sport-specific prompts (MLB, NBA, NFL)

5. **`core/db.py`** — SQLAlchemy ORM migration for paper_trades.json → SQL when trades exceed ~500

6. **`dashboard/reporter.py`** — morning email/Slack digest

7. **NFL extension (September 2026)** — `KXNFLGAME` series + `american_football_nfl` TheOddsAPI key

---

## 18. Known Issues & Edge Cases

**Kalshi `occurrence_datetime` is wrong by ~4 hours**
Kalshi stores Eastern Time values in the `occurrence_datetime` field but labels them as UTC. This causes a ~4-hour error (EDT in summer). All timing logic uses `ticker_to_utc()` from `core/utils.py` which parses the correct ET time directly from the ticker string (e.g., `1210` in `KXMLBGAME-26JUN241210TEXMIA` = 12:10 PM ET). Never use `occurrence_datetime` for time calculations.

**Snapshot windows require laptop to be awake**
LaunchAgents pause when macOS sleeps. If the lid is closed during a game's 20-minute window (110–130 min before start), that snapshot is missed and the trade remains timing-suspect. See Phase 3 roadmap item 1.

**Timing-suspect open trades**
Open trades snapped > 3h before game will be replaced when a clean 2h-window snapshot fires. Until then, they show as `timing_suspect=True`.

**Snapshot UTC vs display CT**
Snapshot filenames are UTC. A snapshot at 11 PM CT has a next-UTC-day filename. The dashboard uses a 24-hour lookback window, not date-string filtering.

**Kalshi API pagination**
`/markets` returns up to 200 results. No pagination implemented. Fine mid-season; may truncate near season start.

**Team name matching**
Kalshi ↔ TheOddsAPI matching uses `analysis/normalizer.py`. Missing team → silent match failure → no Pinnacle data. Fix by adding the team to `MLB_MAP` or `NBA_MAP`.

**Agent fields null on pre-June 24 trades**
The 14 existing trades predate agent wiring. `agent_verdict` is null on all of them. `agent_stats` will stay at zero until post-agent trades resolve.

---

## 19. What Is and Isn't Committed

**Committed (source of truth):**
- All `.py` files (including stubs)
- `requirements.txt`, `README.md`, `.env.example`
- `.streamlit/config.toml`, `.plist` automation files
- `data/paper_trades.json` — **commit after every update_outcomes.py run**
- `data/performance_summary.json`
- `data/paper_trades.db` — sandbox SQLite database
- `data/agent_cost_log.csv`
- `data/skipped_trades.json`

**Not committed (gitignored):**
- `.env` — contains API keys, **never commit**
- `data/snapshots/` — ephemeral runtime data
- `data/raw/` — large historical odds pulls
- `__pycache__/`, `*.pyc`, `.DS_Store`

> **Before committing:** run `python scripts/update_outcomes.py` to ensure `paper_trades.json` and `paper_trades.db` have all current resolutions, then commit both along with your code changes.
