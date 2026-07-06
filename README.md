# EdgeFund — Prediction Market Alpha System

![Python](https://img.shields.io/badge/Python-3.13-blue) ![Status](https://img.shields.io/badge/Status-Post--Rebuild%20Live-green) ![Trading](https://img.shields.io/badge/Trading-Paper%20%2B%20Sandbox-yellow)

A paper-trading system that detects and logs pricing gaps between **Kalshi prediction markets** and sportsbooks (**Pinnacle**, DraftKings, FanDuel), with an AI research agent that filters each signal before logging, a tiered signal-gate system that sizes or shadow-tracks trades by conviction, and a sandbox portfolio simulation layer that applies Kelly-criterion position sizing and tracks simulated P&L in real time.

Runs 24/7 on a Google Cloud VPS via systemd — not a local/laptop-dependent pipeline.

---

## Table of Contents

1. [What This Project Does](#1-what-this-project-does)
2. [The 2026-07-04 Rebuild](#2-the-2026-07-04-rebuild)
3. [Project Goal & Current State](#3-project-goal--current-state)
4. [Tech Stack](#4-tech-stack)
5. [Environment Setup](#5-environment-setup)
6. [.env File](#6-env-file)
7. [Desk Configuration](#7-desk-configuration)
8. [Directory & File Map](#8-directory--file-map)
9. [Data Flow](#9-data-flow)
10. [File-by-File Breakdown — Written Files](#10-file-by-file-breakdown--written-files)
11. [File-by-File Breakdown — Stubs](#11-file-by-file-breakdown--stubs)
12. [Data File Schemas](#12-data-file-schemas)
13. [Automation — systemd (VPS)](#13-automation--systemd-vps)
14. [APIs — Keys, Endpoints, Cost](#14-apis--keys-endpoints-cost)
15. [Research Agent — Architecture](#15-research-agent--architecture)
16. [Sandbox Portfolio Simulation](#16-sandbox-portfolio-simulation)
17. [Running the Dashboard](#17-running-the-dashboard)
18. [Current Performance](#18-current-performance)
19. [Roadmap](#19-roadmap)
20. [Known Issues & Edge Cases](#20-known-issues--edge-cases)
21. [What Is and Isn't Committed](#21-what-is-and-isnt-committed)

---

## 1. What This Project Does

**The thesis:** Kalshi retail bettors systematically misprice game contracts relative to sharp sportsbook consensus (Pinnacle). When the gap between Kalshi's implied probability and Pinnacle's vig-free probability exceeds a threshold, the system paper-trades the discrepancy and logs outcomes to build a statistical track record.

**Example:**
```
Marlins win probability
  Kalshi:              49%
  Pinnacle (vig-free): 61%
  Gap:                 -12.2%   (Kalshi underpricing the home team)

Signal: BUY_YES on KXMLBGAME-26JUN241210TEXMIA-MIA → Tier B (10-15% gap)
```

The system is **not** live trading real money. It is building a statistically significant track record to demonstrate edge before deploying capital. A parallel **sandbox simulation** applies real Kelly-based position sizing to every paper trade, tracking what the P&L curve would look like on a $1,000 bankroll.

Trades are sorted into conviction tiers, and only the tiers with an established track record are actually sized and tracked as live P&L — the rest are shadow-tracked (see below).

---

## 2. The 2026-07-04 Rebuild

A strategy-analysis report found two structural bugs contaminating every performance number the system had produced up to that point:

1. **Two independent trade-origination pipelines shared one ledger.** `agent/edge_discovery_agent.py` (the protected pipeline, with pre-filtering) and `scripts/update_outcomes.py`'s own `ingest_new_trades()` (unprotected, no pre-filter) both wrote to the same trade file independently. The unprotected pipeline also starved the protected one of candidates via shared cooldown/already-traded checks.
   **Fix:** `ingest_new_trades()` is permanently disabled (kept as `_ingest_new_trades_impl()` for audit, never called). `edge_discovery_agent.py` is now the sole origination path — every trade carries `"pipeline_source": "edge_discovery_agent"`.

2. **A silent MONITOR-relabeling bug.** Re-evaluating an already-logged trade on a cleaner snapshot unconditionally overwrote its fields, even when the new verdict was a downgrade to MONITOR/SKIP.
   **Fix:** downgrades now set `status: "PAUSED"` (with a `paused_reason`) instead of silently overwriting. 12 contaminated trades were fixed retroactively and are excluded from all win-rate/EV math — still visible in the raw trade log for audit.

The rebuild also introduced:
- **A/B/C signal-gate tier system** (replacing the old Tier 1 / Tier 2 split) — see §7.
- **A concurrent-position cap (4) and a 20%-drawdown circuit breaker** in `execution/position_manager.py`.
- **A desk-config architecture** (`desks/*.yaml` + `core/desk_loader.py`) replacing hardcoded per-sport constants scattered across ~10 files, including three independently-diverged team-alias dictionaries that had silently drifted apart.
- **Desk-namespaced data directories** (`data/mlb/`, `data/wnba/`, `data/nfl/`) so trades, skips, and shadow trades never mix across sports.
- **A clean sandbox reset** — starting bankroll reset to $1,000 on 2026-07-05; all pre-rebuild sandbox data archived (`sandbox_trades_pre_rebuild_bak`).

Treat any trade dated before 2026-07-04 as pre-rebuild data — visible for audit but excluded from live performance stats.

---

## 3. Project Goal & Current State

**Target:** 30+ clean resolved Tier A trades with win rate ≥ 58% and p < 0.05, then deploy real capital with proper Kelly sizing.

**Current state (post-rebuild, as of 2026-07-05):**

| Metric | Value |
|---|---|
| Trades logged (MLB + WNBA) | 38 |
| Resolved | 21 |
| Paused (pre-rebuild contamination, excluded from stats) | 12 |
| Tier A (5-10% gap) win rate | 81.8% (11 resolved) |
| Tier B (10-15% gap) win rate | 37.5% (8 resolved, still in 20-trade validation window) |
| Tier C (15%+ gap) | Shadow-only, 0 resolved so far |
| Sandbox bankroll | $1,000.00 (clean start 2026-07-05) |

**Key deadlines:**
- **July 21, 2026** — TheOddsAPI business plan expires (renew or find alternative)
- **July 27, 2026** — YC application deadline (`scripts/yc_summary.py` generates the clean baseline dataset for this)

---

## 4. Tech Stack

| Layer | Library | Version |
|---|---|---|
| Language | Python | 3.13 |
| Data | pandas, numpy, scipy | `>=2.0` |
| Config | pyyaml | — (desk config) |
| HTTP | requests | — |
| Env | python-dotenv | — |
| Logging | loguru | — |
| Dashboard | streamlit | `>=1.58.0` |
| Charts | plotly | `>=6.0` |
| Auto-refresh | streamlit-autorefresh | — (5-min interval; see §20) |
| AI Agent | anthropic SDK | `>=0.111.0` |
| AI Model | claude-sonnet-4-6 | — |
| Database | SQLite (stdlib sqlite3) | — |
| Testing | pytest | — |
| Automation | systemd (Linux VPS) | — |
| Infrastructure | Google Cloud (us-central1-b) | always-on VPS at `34.134.239.151` |

---

## 5. Environment Setup

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/Ananthak2324/prediction-market-fund.git
cd prediction-market-fund

# 2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install all dependencies
pip install -r requirements.txt
```

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
python scripts/backfill_sandbox.py   # creates DB, opens positions for historical trades
```

### Syncing data from the VPS (local dev)

```bash
scripts/sync_from_vps.sh   # rsyncs data/ from the production VPS so local files stay current
```

---

## 6. .env File

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
| `ODDS_API_KEY` | [the-odds-api.com](https://the-odds-api.com) | Business plan needed for historical pulls; 6,667 calls/day quota on current plan |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | ~$0.01-0.014/trade with caching; agent defaults to MONITOR if key is missing |
| Kalshi | N/A | No key required for public market data |

---

## 7. Desk Configuration

Since the 2026-07-04 rebuild, every sport-specific constant (team alias maps, thresholds, Kelly multipliers, agent prompts, risk parameters) lives in a per-sport YAML file under `desks/`, loaded via `core/desk_loader.py` — not hardcoded across a dozen Python files.

```
desks/
├── base.yaml   ← shared defaults: thresholds, schedule cooldowns, risk params, tier definitions
├── mlb.yaml    ← desk_status: ACTIVE — team alias map, MLB-specific agent prompt
├── wnba.yaml   ← desk_status: ACTIVE — WNBA-specific agent prompt
└── nfl.yaml    ← desk_status: PENDING — defined but not yet live (no open Kalshi markets)
```

`core/desk_loader.py`'s `DeskConfig` class deep-merges `base.yaml` with the desk-specific file and exposes typed properties (`series_ticker`, `sport_key`, `alias_map`, `tier_a/b/c`, `cooldown_hours`, `max_concurrent_positions`, `agent_system_prompt`, desk-scoped data paths, etc.), plus a generic `.get("dot.path", default)` for anything else. `get_active_desks()` returns only `ACTIVE` desks (currently MLB, WNBA); `get_all_desks()` includes `PENDING` desks too.

**Signal-gate tiers** (`agent/edge_discovery_agent.py::apply_signal_gates()`), applied to every TRADE verdict from the research agent:

| Tier | Gap size | Status | Kelly multiplier |
|---|---|---|---|
| A | 5–10% | Active, full sizing | 0.25x |
| B | 10–15% | Active, reduced sizing (pending 20-trade validation: upgrades >55% WR, downgrades <45% WR) | 0.10x |
| C | 15%+ | Shadow-only — tracked, never traded | 0.00x |
| BUY_NO (any gap) | — | Shadow-only — tracked, never traded | 0.00x |

Shadow-tier candidates route to `data/<desk>/shadow_trades.json` instead of `paper_trades.json`.

Adding a new sport is a new desk YAML file, not a code change — scripts standardize on `--desk MLB` / `--all-desks` CLI flags.

---

## 8. Directory & File Map

```
prediction-market-fund/
│
├── .env                                ← NOT in git — create from .env.example
├── .env.example                        ← template
├── .gitignore
├── .streamlit/
│   └── config.toml                     ← dark theme
├── requirements.txt
├── README.md
├── system_overview.md                  ← plain-English architecture doc (also fed to the Intelligence Agent)
│
├── desks/
│   ├── base.yaml                       ← shared defaults (thresholds, risk, schedule)
│   ├── mlb.yaml                        ← ACTIVE — alias map, agent prompt
│   ├── wnba.yaml                       ← ACTIVE — alias map, agent prompt
│   └── nfl.yaml                        ← PENDING — defined, not yet live
│
├── deploy/
│   └── systemd/                        ← *.service / *.timer unit files (see §13)
│
├── agent/
│   ├── edge_discovery_agent.py         ← WRITTEN — sole trade-origination pipeline, signal gates
│   ├── research_agent.py               ← WRITTEN — AI trade filter, desk-parameterized
│   ├── pre_filter.py                   ← WRITTEN — cheap pre-agent gap/prob sanity checks
│   ├── memory_agent.py                 ← WRITTEN — Intelligence Agent (dashboard chatbot)
│   ├── decision_engine.py              ← STUB    — Phase 3
│   └── signal_framework.py             ← STUB    — Phase 3
│
├── analysis/
│   ├── normalizer.py                   ← WRITTEN — team name normalization (desk-based + legacy)
│   ├── bookmaker_comparison.py         ← WRITTEN — multi-book consensus checks
│   ├── backtest.py                     ← PARTIAL — backtest scaffolding
│   ├── gap_calculator.py               ← PARTIAL — gap computation helpers
│   └── merger.py                       ← PARTIAL — joins Kalshi + Vegas data
│
├── config/
│   └── config.py                       ← WRITTEN — env vars + constants
│
├── core/
│   ├── desk_loader.py                  ← WRITTEN — DeskConfig class, get_active_desks()/get_desk()
│   ├── utils.py                        ← WRITTEN — vig removal, prob conversion, ticker_to_utc
│   ├── logger.py                       ← WRITTEN — loguru setup
│   ├── notifications.py                ← WRITTEN — iMessage notification queue
│   └── db.py                           ← STUB    — Phase 3 (SQLAlchemy ORM)
│
├── dashboard/
│   ├── app.py                          ← WRITTEN — Streamlit terminal, dynamic per-desk tabs
│   ├── charts.py                       ← PARTIAL — legacy matplotlib, unused
│   └── reporter.py                     ← STUB    — Phase 3
│
├── data/
│   ├── clients/
│   │   ├── kalshi_client.py            ← WRITTEN — Kalshi REST API client
│   │   └── odds_client.py              ← WRITTEN — TheOddsAPI client
│   ├── fetcher.py                      ← PARTIAL — unified data fetching
│   ├── mlb/                            ← LIVE DATA — desk-namespaced (see below)
│   ├── wnba/                           ← LIVE DATA — desk-namespaced
│   ├── nfl/                            ← empty until desk goes ACTIVE
│   ├── paper_trades.json.bak           ← frozen pre-migration snapshot (audit only)
│   ├── skipped_trades.json.bak         ← frozen pre-migration snapshot (audit only)
│   ├── paper_trades.db                 ← LIVE DATA — sandbox SQLite DB (shared across desks)
│   ├── gap_curves.db                   ← LIVE DATA — gap-curve tracker time-series
│   ├── snapshots/                      ← NOT in git — runtime snapshot files
│   └── raw/                            ← NOT in git — historical odds pulls
│
├── data/<mlb|wnba|nfl>/                ← per desk:
│   ├── paper_trades.json               ←   trade ledger (this desk only)
│   ├── skipped_trades.json             ←   agent-rejected candidates
│   ├── shadow_trades.json              ←   Tier C / BUY_NO — tracked, never traded
│   ├── monitor_cache.json              ←   MONITOR cooldown registry
│   ├── performance_summary.json        ←   per-desk win rates, tier/signal breakdowns
│   ├── funnel_log.json                 ←   funnel counts per edge-discovery cycle
│   └── agent_cost_log.csv              ←   per-call Anthropic costs
│
├── execution/
│   ├── position_sizer.py               ← WRITTEN — Kelly sizing (tier-specific multiplier)
│   ├── position_manager.py             ← WRITTEN — sandbox DB open/poll/settle, concurrent cap, circuit breaker
│   ├── paper_trader.py                 ← STUB    — Phase 3 (live order router)
│   └── live_trader.py                  ← STUB    — Phase 3 (Kalshi order placement)
│
├── scripts/
│   ├── schedule_snapshots.py           ← WRITTEN — pre-game snapshot scheduler (desk-aware)
│   ├── snapshot_gaps.py                ← WRITTEN — manual/wide-scan snapshot runner
│   ├── update_outcomes.py              ← WRITTEN — resolves trades, rebuilds performance stats
│   ├── backfill_sandbox.py             ← WRITTEN — one-time sandbox DB setup
│   ├── migrate_desk_data.py            ← WRITTEN — one-time desk-namespace migration (idempotent)
│   ├── reset_sandbox_clean_start.py    ← WRITTEN — one-time sandbox reset (idempotent)
│   ├── fix_monitor_bug_retroactive.py  ← WRITTEN — one-time MONITOR-bug retroactive fix
│   ├── verify_phase1.py / verify_phase2.py / verify_all_phases.py ← WRITTEN — rebuild verification suites
│   ├── yc_summary.py                   ← WRITTEN — clean baseline dataset for external use
│   ├── reprocess_skipped_trades.py     ← WRITTEN — retroactive SKIP-decision analysis
│   ├── gap_curve_tracker.py            ← WRITTEN — always-on 5-min gap-curve daemon
│   ├── gap_curve_analysis.py           ← WRITTEN — gap-curve summary stats
│   ├── weekly_audit.py                 ← WRITTEN — Sunday statistical audit
│   ├── send_digest.py                  ← WRITTEN — daily iMessage summary
│   ├── relay_notifications.py          ← WRITTEN — Mac-side iMessage relay (VPS can't send iMessages)
│   ├── sync_from_vps.sh                ← WRITTEN — pulls latest data from VPS to local
│   ├── fetch_odds_history.py           ← WRITTEN — historical Pinnacle data pull
│   ├── pull_kalshi_markets.py          ← WRITTEN — fetch Kalshi market list
│   ├── pull_kalshi_candles.py          ← WRITTEN — fetch Kalshi price history
│   ├── backtest_gap.py                 ← WRITTEN — run historical gap analysis (MLB-only)
│   ├── run_backtest.py                 ← WRITTEN — backtest entry point
│   ├── backfill_skipped_tickers.py     ← WRITTEN — one-time backfill utility
│   ├── test_agent.py                   ← WRITTEN — test agent on recent trades
│   └── test_favorites_filter.py        ← WRITTEN — test favorites filter logic
│
└── tests/
    ├── test_agent_cost.py              ← WRITTEN — validates agent cost/caching
    ├── test_merger.py                  ← PARTIAL
    ├── test_normalizer.py              ← PARTIAL
    └── test_utils.py                   ← PARTIAL
```

---

## 9. Data Flow

```
Every 2 hours (systemd timer, unconditional)
  snapshot_gaps.py --all-desks
    └── Wide scan across all open Kalshi markets for every active desk

Every 10 minutes, gated (systemd timer)
  schedule_snapshots.py
    └── Precision pre-game snapshot within 110-130 min of each game start

Always-on (systemd service)
  gap_curve_tracker.py
    └── Polls every 5 min, writes to data/gap_curves.db (gap evolution time-series)

Every 30 minutes (systemd timer) — SOLE trade-origination pipeline
  edge_discovery_agent.py --all-desks
    ├── Live API calls to Kalshi + Pinnacle (+ DK/FanDuel for consensus)
    ├── Classifies gap ≥ 5% into an edge type (BEHAVIORAL_RETAIL, MULTI_BOOK_CONSENSUS,
    │     SHARP_SIGNAL, RETAIL_BOOK_SOFT, MARKET_ANOMALY)
    ├── Checks per-desk cooldown caches (paper_trades / skipped_trades / monitor_cache,
    │     1h cooldown) — only edge_discovery_agent-sourced trades count as "already traded"
    ├── research_agent.run(desk, game)  ← AI BARRIER
    │     └── TRADE / SKIP / MONITOR
    ├── apply_signal_gates(desk, candidate, verdict)  ← tier routing
    │     ├── Tier A/B → data/<desk>/paper_trades.json (status="OPEN", tier, kelly_multiplier_used)
    │     └── Tier C / BUY_NO → data/<desk>/shadow_trades.json (never traded)
    └── SKIP → data/<desk>/skipped_trades.json

Every 15 minutes (systemd timer)
  update_outcomes.py --no-ingest
    ├── ingest_new_trades() is a disabled no-op (legacy pipeline, kept for audit)
    ├── Re-evaluate timing-suspect trades on cleaner snapshots:
    │     TRADE verdict → update fields normally
    │     MONITOR/SKIP verdict → status="PAUSED" (quarantined, not overwritten)
    ├── Resolve settled trades via Kalshi API → WIN/LOSS
    ├── Resolve shadow trades (outcome tracked, no sandbox position)
    ├── Settle sandbox positions at resolution
    └── Rebuild data/<desk>/performance_summary.json (tier_performance, signal_performance,
          drawdown_status, by_gap_bucket, agent_stats, portfolio_metrics, ...)

Always-on (systemd service)
  position_manager.py
    └── Every 60s during game hours:
          For each OPEN sandbox position:
            Enforce concurrent-position cap (4) + 20%-drawdown circuit breaker
            Fetch current Kalshi price, apply exit rules
              (FAIR_VALUE / STOP_LOSS / PROFIT_TARGET / NEAR_RESOLUTION)
            Close position + update data/paper_trades.db

2 AM UTC daily (systemd timer)          Sunday 11 PM UTC (systemd timer)
  send_digest.py                          weekly_audit.py
    └── iMessage summary                    └── Deeper statistical audit

Always-on (systemd service)
  dashboard/app.py (Streamlit, port 8501)
    └── Aggregates across all active desks for headline metrics + Performance tab

On demand
  python scripts/update_outcomes.py --no-ingest   # run anytime
  python scripts/snapshot_gaps.py --all-desks      # manual snapshot
  python scripts/yc_summary.py                     # clean baseline dataset
  streamlit run dashboard/app.py                   # launch dashboard locally
```

### AI Agent barrier

For every new trade candidate before it's logged:

```
research_agent.run(desk, game_dict)
  ├── Check 1: Re-fetch Pinnacle now (desk.sport_key — per-desk, not hardcoded)
  │     If moved beyond desk's pinnacle_move_hard_gate → auto SKIP (sharp money moved)
  ├── Check 2: Web search via Claude
  │     Query: desk.search_query_template (desk-specific — MLB checks starting pitcher, WNBA doesn't)
  └── Check 3: Synthesize verdict as JSON
        TRADE   → apply_signal_gates() routes to paper_trades.json or shadow_trades.json
        MONITOR → held in monitor_cache, re-evaluated next cycle
        SKIP    → logged to skipped_trades.json only
```

---

## 10. File-by-File Breakdown — Written Files

### `core/desk_loader.py`

The desk-config layer introduced in the 2026-07-04 rebuild. `DeskConfig` deep-merges `desks/base.yaml` with a sport-specific file (`desks/mlb.yaml`, etc.) and exposes everything the rest of the codebase used to hardcode: `series_ticker`, `sport_key`, `alias_map`, `abbreviation_map`, `tier_a/b/c` bounds, `gap_tier(abs_gap)`, `cooldown_hours`, `max_concurrent_positions`, `max_drawdown_pause_pct`, `starting_bankroll`, `tier_kelly` multipliers, `agent_system_prompt`, `search_query_template`, and every desk-scoped data path (`paper_trades_path`, `shadow_trades_path`, etc.). `get_active_desks()` / `get_all_desks()` / `get_desk(desk_id)` are the module-level entry points everything else calls.

### `agent/edge_discovery_agent.py`

The sole trade-origination pipeline. Runs on a 30-minute systemd timer per active desk. `classify_edge()` reads all its thresholds from `desk.get("thresholds.*")` rather than hardcoded module constants. `apply_signal_gates(desk, candidate, verdict)` is the tier router (§7) — called on every TRADE verdict before it's logged, deciding Tier A/B (paper_trades.json) vs. Tier C/BUY_NO (shadow_trades.json). Every trade record is tagged `pipeline_source`, `desk_id`, `tier`, `kelly_multiplier_used`, `status`.

### `core/utils.py`

Critical math and timing utilities shared across the project.

```python
ticker_to_utc(event_ticker: str) -> datetime | None
# Parses the accurate game start time from the Kalshi event ticker.
# Ticker format: KXMLBGAME-26JUN241210TEXMIA → June 24 2026, 12:10 PM ET → 16:10 UTC
# Kalshi's occurrence_datetime has a known ~4-hour UTC/ET confusion error.
# The ticker itself is always accurate — this is the canonical source.

american_to_prob(odds: int) -> float
# Positive (underdog): 100 / (odds + 100)
# Negative (favorite): abs(odds) / (abs(odds) + 100)

remove_vig(home_odds, away_odds) -> tuple[float, float]
# Divides each raw prob by their sum.
# e.g. (-150, +130) → raw (0.600, 0.435) → vig-free (0.579, 0.421)
```

> **Why `ticker_to_utc`:** The Kalshi API's `occurrence_datetime` field stores Eastern Time values labelled as UTC, causing a systematic ~4-hour error (EDT). Every component that needs a game start time calls `ticker_to_utc()` instead. This logic is untouched by the rebuild — explicitly on the "do not change" list.

### `agent/pre_filter.py`

Cheap, non-LLM sanity checks (`desk.get("thresholds.v_prob_min/v_prob_max/pinnacle_move_hard_gate")`) run before a candidate ever reaches the research agent — cost control.

### `scripts/update_outcomes.py`

Runs every 15 minutes (also run manually). Resolves settled trades, rebuilds performance stats, per active desk. **`ingest_new_trades()` is permanently disabled** (see §2) — kept as `_ingest_new_trades_impl()` for audit, never called.

**`build_summary(trades, shadow_entries=None)`**
Computes `total_paused`-excluded win rates, `tier_performance` (A/B/C — status, kelly multiplier, resolved/wins/win_rate/p_value/EV), `signal_performance` (BUY_YES active, BUY_NO shadow), `drawdown_status` (current drawdown, circuit-breaker state), `by_gap_bucket`, `agent_stats`, `portfolio_metrics`. Accepts an optional `shadow_entries` override so callers (like the dashboard) can recompute one consistent summary across multiple desks' raw data rather than merging pre-summarized per-desk files.

**Trade categories:**

| Category | Condition | Counts for analysis? |
|---|---|---|
| Clean | snapshot ≤ 3h before game | Yes — primary track record |
| Timing-suspect | snapshot > 3h before game | Yes — but flagged |
| Invalid | snapshot taken after game started | No — excluded |
| Paused | MONITOR/SKIP downgrade on re-evaluation | No — excluded, visible for audit |
| Tier A | abs_gap 5-10% | Active, full sizing |
| Tier B | abs_gap 10-15% | Active, reduced sizing, validation window |
| Tier C | abs_gap ≥ 15% | Shadow-only |

### `execution/position_sizer.py`

Kelly position sizing for Kalshi YES/NO contracts, generalized to accept a tier-specific multiplier (was hardcoded quarter-Kelly pre-rebuild).

```python
calculate_position(bankroll, kalshi_price, pinnacle_prob, kelly_multiplier=0.25) -> dict
```

Entry prices are always the contract being bought:
- `BUY_YES` → `kalshi_price = k_prob`, `pinnacle_prob = v_prob`
- `BUY_NO`  → `kalshi_price = 1 - k_prob`, `pinnacle_prob = 1 - v_prob`

**Kelly formula:**
```
payout_ratio    = (1 - kalshi_price) / kalshi_price
full_kelly      = (p × payout_ratio − q) / payout_ratio
sized_kelly     = full_kelly × kelly_multiplier   (0.25 Tier A, 0.10 Tier B)
position_frac   = min(sized_kelly, 0.10)   ← hard cap at 10%
shares          = int(bankroll × position_frac / kalshi_price)
```

Kelly math itself is unchanged by the rebuild — only the multiplier became tier-dependent instead of a fixed constant.

**Example (LAD @ MIN, 32% Kalshi / 44.4% Pinnacle, Tier A):**
```
Payout ratio  : 2.125x
Full Kelly    : 18.25%
Sized (0.25x) : 4.56%
Position      : $45.62 → 142 shares × $0.32 = $45.44
```

### `execution/position_manager.py`

Manages the sandbox SQLite DB and position lifecycle.

**`init_db()`** — creates tables, inserts `$1,000` config row (idempotent)

**`open_sandbox_position(paper_trade_dict, desk=None)`** — called for every new paper trade:
1. Checks entry_date ≥ desk's `sandbox_start_date` (2026-07-05 post-reset)
2. **Blocks if `count_open_positions() >= MAX_CONCURRENT_POSITIONS` (4)** — added in the rebuild
3. **Blocks if current drawdown ≥ `MAX_DRAWDOWN_PAUSE` (20%)** — circuit breaker, added in the rebuild
4. Calculates position via `calculate_position()` using `trade.get("kelly_multiplier_used")`
5. Inserts OPEN row in `sandbox_trades`, logs to `sandbox_bankroll_history`

**`poll_open_positions()`** — called every 60s during game hours, reads stop-loss/profit-target/near-resolution thresholds from desk config:

| Rule | Condition | Exit type |
|---|---|---|
| Fair value | current_price ≥ pinnacle_prob | `FAIR_VALUE` |
| Stop loss | P&L ≤ desk's `stop_loss_pct` (-40%) | `STOP_LOSS` |
| Profit target | P&L ≥ desk's `profit_target_pct` (+80%) | `PROFIT_TARGET` |
| Near resolution | < `near_resolution_hours` (2h) to game end AND P&L > threshold | `NEAR_RESOLUTION` |

**`count_open_positions(conn)` / `_current_drawdown(conn)`** — new helpers backing the concurrent cap and circuit breaker.

### `agent/research_agent.py`

AI barrier between gap detection and trade logging. Uses `claude-sonnet-4-6` with web search. Fully desk-parameterized post-rebuild (`run(desk, game_dict, ...)`) — this also fixed a bug where WNBA games were silently using MLB's Odds API sport key in the Pinnacle-stability check.

**Decision rules (desk-specific prompt, MLB example):**

| SKIP if | TRADE if |
|---|---|
| Pitcher scratched | Roster confirmed, starter healthy |
| Key player injured | Pinnacle line stable |
| Pinnacle moved beyond hard gate | Behavioral bias (favorite-longshot) |
| Weather (wind blowing in) | No injury/weather news |

**Cost:** ~$0.010–$0.014/trade with prompt caching (90% reduction after first call per session), logged per-desk to `data/<desk>/agent_cost_log.csv`.

### `agent/memory_agent.py`

The Intelligence Agent — the dashboard's chatbot. Loads `system_overview.md` plus aggregated trades/skips/shadow-trades/cost-logs/funnel-logs across all active desks once per session (frozen context, ephemeral-cached). Answers grounded entirely in that loaded data, no live web search. `max_tokens=8192` — broad questions (e.g. "audit the system") can run long.

### `dashboard/app.py`

Dark-theme Streamlit trading terminal. Auto-refreshes every 5 minutes (raised from 60s post-rebuild — a short interval risked Streamlit cancelling a slow in-flight script run, like a long Intelligence Agent answer, mid-request).

**Run with:** `streamlit run dashboard/app.py`

**Tabs are generated dynamically, one per active desk**, plus fixed tabs:

| Tab | Contents |
|---|---|
| [Desk] (e.g. MLB, WNBA) | Open Market Gap Scanner — all currently open Kalshi contracts sorted by gap size, with Action column. |
| 📉 Gap Curves | Time-series of `\|gap\|` vs. hours-to-game per market, from `gap_curves.db`. |
| 📋 Trade Log | Every paper trade across all active desks, with Sport/Outcome/Tier/Game Date filters. |
| 📈 Performance | Win rate over time, gap-bucket bars, outcomes donut, agent stats — rebuilt from combined raw trades across all active desks so it always matches the headline metric cards. |
| 💰 Sandbox | Bankroll over time, open/closed positions, exit rule breakdown. |
| 🔬 Investigation | Tier B validation progress toward the 20-trade threshold, Tier C / BUY_NO shadow win rates. |
| 🧠 Intelligence | Chat interface backed by `agent/memory_agent.py`. |
| ⚙️ System | Pipeline heartbeat, service health. |

**Timezone:** All times in Central Time (CT).

### `data/clients/kalshi_client.py`

Thin wrapper around Kalshi's public REST API. No auth required.

```
Base URL: https://api.elections.kalshi.com/trade-api/v2

get_markets(series_ticker)   → list active markets
get_market(ticker)           → single market (includes settlement result)
get_orderbook(ticker)        → live bid/ask

Series tickers (per desk.series_ticker): KXMLBGAME (MLB), KXWNBAGAME (WNBA), KXNFLGAME (NFL, pending)
```

**Ticker format:** `KXMLBGAME-26JUN241210TEXMIA-MIA`
- `26` = year 2026, `JUN` = June, `24` = 24th, `1210` = 12:10 PM **Eastern Time**
- `TEXMIA` = away-home abbreviations, `-MIA` = YES side (home team wins)

### `data/clients/odds_client.py`

Wrapper around TheOddsAPI for Pinnacle/DK/FanDuel moneylines. Requires `ODDS_API_KEY`.

```
get_odds(sport_key, markets, bookmakers)   # sport_key comes from desk.sport_key
get_historical_odds(sport_key, date, markets, bookmakers)   # requires Business plan
```

---

## 11. File-by-File Breakdown — Stubs

These files contain `raise NotImplementedError(...)`. **Do not import them in production code** without implementing first.

| File | Phase | Planned Purpose |
|---|---|---|
| `agent/decision_engine.py` | 3 | Convert agent verdict + gap into live order parameters |
| `agent/signal_framework.py` | 3 | Additional sport-specific decision frameworks beyond desk config |
| `core/db.py` | 3 | SQLAlchemy ORM migration from flat JSON to SQL |
| `execution/paper_trader.py` | 3 | Route TRADE decisions to Kalshi order placement |
| `execution/live_trader.py` | 3 | Actual Kalshi REST order API calls |
| `dashboard/reporter.py` | 3 | Morning email/Slack digest (separate from the existing iMessage digest) |

---

## 12. Data File Schemas

### `data/<desk>/paper_trades.json`

JSON array of trade objects, one file per active desk. The track record — **commit alongside code changes.**

```json
{
  "trade_id":              "2026-07-05_0130|KXMLBGAME-...",
  "snapshot_time":         "2026-07-05_0130",
  "sport":                 "MLB",
  "desk_id":               "MLB",
  "game":                  "Texas Rangers @ Miami Marlins",
  "team":                  "Miami Marlins",
  "side":                  "HOME",
  "start_utc":             "2026-07-05T16:10:00Z",
  "kalshi_ticker":         "KXMLBGAME-26JUL051210TEXMIA-MIA",
  "event_ticker":          "KXMLBGAME-26JUL051210TEXMIA",
  "k_prob":                0.49,
  "v_prob":                0.6102,
  "gap":                   -0.1202,
  "abs_gap":               0.1202,
  "signal":                "BUY_YES",
  "tier":                  "B",
  "kelly_multiplier_used": 0.10,
  "pipeline_source":       "edge_discovery_agent",
  "status":                "OPEN",
  "paused_reason":         null,
  "hours_before_game":     2.1,
  "timing_suspect":        false,
  "valid_for_analysis":    true,
  "outcome":               null,
  "resolution_price":      null,
  "resolved_at":           null,
  "agent_verdict":         "TRADE",
  "agent_confidence":      "high",
  "agent_reasoning":       "...",
  "gap_type":              "BEHAVIORAL_RETAIL",
  "news_found":            false,
  "pinnacle_stable":       true,
  "pinnacle_movement":     0.01,
  "replacement_flags":     []
}
```

- `status`: `"OPEN"` at creation, `"CLOSED"` on resolution, `"PAUSED"` if a re-evaluation downgraded the verdict (excluded from all performance math, kept for audit).
- `valid_for_analysis=false` means the snapshot was taken **after** game start.
- `start_utc` is derived from the ticker via `ticker_to_utc()`, not Kalshi's `occurrence_datetime`.

### `data/<desk>/shadow_trades.json`

Tier C and BUY_NO candidates — tracked with real outcomes but never opened as a sandbox position. Same shape as `paper_trades.json` plus a `shadow_reason` field and `shadow_outcome` populated on resolution.

### `data/<desk>/performance_summary.json`

Regenerated on every `update_outcomes.py` run. Do not edit manually.

Key fields: `total_logged`, `total_paused`, `total_resolved`, `total_valid`, `win_rate_overall`, `tier_performance` (A/B/C breakdown), `signal_performance` (BUY_YES/BUY_NO), `drawdown_status`, `by_gap_bucket`, `clean_trades`, `suspect_trades`, `agent_stats`, `portfolio_metrics`.

### `data/paper_trades.db` — SQLite (shared across desks)

Three tables managed by `execution/position_manager.py`.

**`sandbox_config`** (one row)
```
id              INTEGER  DEFAULT 1
bankroll_start  REAL     DEFAULT 1000.00
start_date      DATE     DEFAULT '2026-07-05'
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
tier              TEXT     A | B | C
gap               REAL
entry_price       REAL     k_prob (BUY_YES) or 1-k_prob (BUY_NO)
pinnacle_prob     REAL     v_prob (BUY_YES) or 1-v_prob (BUY_NO)
full_kelly        REAL
sized_kelly       REAL
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

Pre-rebuild sandbox data is archived in `sandbox_trades_pre_rebuild_bak` and `sandbox_bankroll_history_pre_rebuild_bak` (created by `scripts/reset_sandbox_clean_start.py`).

**`sandbox_bankroll_history`**
```
id          INTEGER  PK
timestamp   DATETIME
bankroll    REAL     available cash after TRADE_OPEN; total equity after TRADE_CLOSE
event_type  TEXT     TRADE_OPEN | TRADE_CLOSE
trade_id    INTEGER  → sandbox_trades.id
note        TEXT
```

### `data/<desk>/agent_cost_log.csv`

Appended on every `research_agent.run()` call for that desk (and by `memory_agent.py`, tagged `[Intelligence]`, into the shared cost log).

Columns: `timestamp`, `game`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `search_calls`, `estimated_cost_usd`

### `data/<desk>/skipped_trades.json`

Auto-created when the agent returns `SKIP`. Full trade dict + agent verdict, per desk.

---

## 13. Automation — systemd (VPS)

Runs 24/7 on a Google Cloud VPS (`34.134.239.151`, us-central1-b) — not dependent on a laptop staying awake. Unit files live in `deploy/systemd/`.

> **Important:** editing a `.service`/`.timer` file in this repo does **not** auto-update the installed unit at `/etc/systemd/system/`. After any deploy touching a unit file:
> ```bash
> sudo cp deploy/systemd/<file> /etc/systemd/system/
> sudo systemctl daemon-reload
> sudo systemctl restart <service>
> ```

| Service / Timer | Schedule | Purpose |
|---|---|---|
| `prediction-fund-widescan.timer` | Every 2 hours | Unconditional all-market snapshot (`--all-desks`) |
| `prediction-fund-snapshot.timer` | Every 10 min (gated) | Pre-game precision snapshot |
| `prediction-fund-edge-discovery.timer` | Every 30 min | Sole trade-origination pipeline |
| `prediction-fund-outcomes.timer` | Every 15 min | Resolve settled trades, rebuild performance stats |
| `prediction-fund-gap-tracker.service` | Always-on | 5-min gap-curve time-series daemon |
| `prediction-fund-positions.service` | Always-on | Position manager, exit signals, circuit breaker |
| `prediction-fund-dashboard.service` | Always-on | Streamlit dashboard (port 8501) |
| `prediction-fund-digest.timer` | 2 AM UTC daily | iMessage daily summary |
| `prediction-fund-weekly-audit.timer` | Sunday 11 PM UTC | Statistical audit |

Common commands:
```bash
sudo systemctl status prediction-fund-<name>
sudo systemctl restart prediction-fund-<name>
sudo journalctl -u prediction-fund-<name> -f      # live logs
sudo systemctl list-timers                        # next-fire times
```

> **Deploying code changes:** the VPS repo is owned by the `predfund` service user, not the interactive login user. Pull as that user to avoid permission errors:
> ```bash
> sudo -u predfund git -C /opt/prediction-fund pull
> ```

### Local dev (Mac)
- `scripts/sync_from_vps.sh` — pulls latest data from VPS so local files stay current
- `scripts/relay_notifications.py` — forwards the iMessage queue from the VPS to your Messages app (the VPS can't send iMessages directly; only a Mac can)

---

## 14. APIs — Keys, Endpoints, Cost

### Kalshi REST API

```
Base URL: https://api.elections.kalshi.com/trade-api/v2
Auth: none required for public data
Rate limit: ~1 req/sec is safe (undocumented)

GET /markets?series_ticker=<desk.series_ticker>&status=open
GET /markets/{ticker}                              → single market + settlement result
```

### TheOddsAPI

```
Base URL: https://api.theoddsapi.com
Auth: ODDS_API_KEY
Quota: 6,667 calls/day (daily reset)

GET /v4/sports/{sport_key}/odds
  ?apiKey=...&regions=us&markets=h2h&bookmakers=pinnacle,draftkings,fanduel
  sport_key: from desk.sport_key (e.g. "baseball_mlb", "basketball_wnba")
```

We use **Pinnacle** as the primary sharp benchmark (low vig, accepts sharp money) with DraftKings/FanDuel for multi-book consensus checks.

### Anthropic API

```
Model: claude-sonnet-4-6
Beta: web-search-2025-03-05 (research agent only — Intelligence Agent has no web search)
Cost: ~$0.010-0.014/trade (research agent, with prompt caching)
```

---

## 15. Research Agent — Architecture

```
edge_discovery_agent.py
  → apply_signal_gates() is called AFTER the agent returns TRADE, not before
  → research_agent.run(desk, game_dict)
       → _fetch_current_pinnacle(desk, game_dict)   Check 1: re-fetch Pinnacle using desk.sport_key
       → _build_user_message(desk, ...)             Prompt via desk.search_query_template
       → Claude + web search
       → parse JSON verdict
       → _print_and_log_cost(desk, ...)             logged to data/<desk>/agent_cost_log.csv
       → return verdict
  → SKIP: append to data/<desk>/skipped_trades.json, continue
  → TRADE: apply_signal_gates(desk, candidate, verdict)
       → Tier A/B → data/<desk>/paper_trades.json + open_sandbox_position()
       → Tier C / BUY_NO → data/<desk>/shadow_trades.json (no sandbox position)
  → MONITOR: held in data/<desk>/monitor_cache.json, re-checked next cycle (1h cooldown)
```

**Error handling:** any exception returns `recommendation=MONITOR`. The agent never blocks data collection or guesses.

---

## 16. Sandbox Portfolio Simulation

The sandbox is a simulation layer on top of paper trading. It does **not** modify `paper_trades.json` — it is additive, keyed by `paper_trade_id`.

**What it tracks:**
- Simulated $1,000 bankroll, reset to a clean start on 2026-07-05
- Kelly position sizing, multiplier set by tier (0.25x Tier A, 0.10x Tier B; Tier C never sized — shadow-only)
- **Hard cap of 4 concurrent open positions**
- **Automatic circuit breaker** — pauses new positions if drawdown from peak bankroll exceeds 20%
- Intra-game exit rules (fair value, stop loss, profit target, near resolution)
- Final resolution settlement (WIN = $1.00/share, LOSS = $0.00/share)
- Full bankroll history for charting

**Quick start:**
```bash
python scripts/backfill_sandbox.py       # one-time setup
python execution/position_manager.py     # start live poll loop
```

**Kelly example (LAD @ MIN, 32% Kalshi, 44.4% Pinnacle, Tier A):**
```
Full Kelly    : 18.25%   (what pure Kelly says to bet)
Sized (0.25x) :  4.56%
Hard cap      : 10.00%   (max per position)
Position      : $45.62 → 142 shares × $0.32
```

---

## 17. Running the Dashboard

Production runs as a systemd service (`prediction-fund-dashboard.service`) on the VPS, always live at `http://34.134.239.151:8501`.

For local dev:
```bash
streamlit run dashboard/app.py
```

Opens at [http://localhost:8501](http://localhost:8501). Auto-refreshes every 5 minutes (see §20 for why this was raised from 60s).

All times displayed in **Central Time (CT)**. To change timezone, update `ET = ZoneInfo("America/Chicago")` in `dashboard/app.py`.

---

## 18. Current Performance

*Post-rebuild, as of 2026-07-05 — run `python scripts/yc_summary.py` for a live snapshot.*

```
38 trades logged (MLB + WNBA)
21 resolved, 12 paused (pre-rebuild contamination, excluded)

Tier A (5-10% gap, 0.25x Kelly):  11 resolved, 81.8% win rate
Tier B (10-15% gap, 0.10x Kelly):  8 resolved, 37.5% win rate — in 20-trade validation window
Tier C (15%+ gap, shadow-only):    0 resolved so far
BUY_NO (shadow-only):              0 resolved so far

Overall p-value: 0.166 — approaching significance, not yet conclusive
Sandbox bankroll: $1,000.00 (clean start), 0 open positions, circuit breaker inactive
```

**Win rate needed for significance (clean Tier A trades only):**

| Trades | Win rate for p ≈ 0.10 | Win rate for p ≈ 0.05 |
|---|---|---|
| 30 | 60% (18W/12L) | 67% (20W/10L) |
| 50 | 58% (29W/21L) | 62% (31W/21L) |

---

## 19. Roadmap

**Done since the original Phase 3 roadmap was written:**
- ✅ Moved scheduler to a Google Cloud VPS (systemd, always-on)
- ✅ A/B/C tier signal-gate system with shadow tracking
- ✅ Desk-config architecture replacing hardcoded per-sport constants
- ✅ Concurrent-position cap + drawdown circuit breaker

**Remaining, in order of priority:**

1. **Tier B validation** — reach 20 resolved trades to decide upgrade (>55% WR) vs. downgrade (<45% WR) to shadow-only
2. **Tier C / BUY_NO investigation** — both were shadow'd pending investigation into pre-rebuild underperformance; needs enough shadow-resolved trades to re-evaluate
3. **Live trading module** (`execution/live_trader.py`) — Kalshi order placement API, `LIVE_MODE` flag to switch from paper to real trades
4. **`agent/decision_engine.py`** — convert verdict + gap → live order parameters
5. **`core/db.py`** — SQLAlchemy ORM migration for paper_trades.json → SQL when trade volume grows
6. **`dashboard/reporter.py`** — morning email/Slack digest (separate from the existing iMessage digest)
7. **NFL activation** — `desks/nfl.yaml` already exists (`desk_status: PENDING`); flip to `ACTIVE` once Kalshi opens NFL markets for the season

---

## 20. Known Issues & Edge Cases

**Kalshi `occurrence_datetime` is wrong by ~4 hours**
Kalshi stores Eastern Time values in `occurrence_datetime` but labels them UTC. All timing logic uses `ticker_to_utc()` from `core/utils.py`, which parses the correct ET time directly from the ticker string. Never use `occurrence_datetime` for time calculations.

**Dashboard auto-refresh interval (fixed 2026-07-05)**
Was 60 seconds; raised to 5 minutes. A short interval let Streamlit's periodic auto-rerun cancel a still-in-flight script run — e.g. a long Intelligence Agent answer near the 8192-token cap could take 90-150+ seconds to generate, and the 60s timer would kill it mid-request with no exception logged, producing a silently blank response.

**VPS repo ownership**
`/opt/prediction-fund` is owned by the `predfund` service user (systemd units run as this user), not the interactive SSH login user. Use `sudo -u predfund git -C /opt/prediction-fund pull` for deploys, and avoid `chown -R <login-user>` on the repo — it breaks the running services' file access until reverted.

**systemd unit files require manual sync**
Editing `deploy/systemd/*.service` in the repo doesn't update the installed unit at `/etc/systemd/system/`. Must be manually `cp`'d + `daemon-reload`'d after any deploy touching a unit file.

**Timing-suspect open trades**
Open trades snapped > 3h before game get replaced when a clean 2h-window snapshot fires. Until then they show as `timing_suspect=True`.

**Snapshot UTC vs display CT**
Snapshot filenames are UTC. A snapshot at 11 PM CT has a next-UTC-day filename. The dashboard uses a 24-hour lookback window, not date-string filtering.

**Kalshi API pagination**
`/markets` returns up to 200 results. No pagination implemented. Fine mid-season; may truncate near season start.

**Team name matching**
Kalshi ↔ TheOddsAPI matching uses each desk's `alias_map` (`desks/<sport>.yaml`), consolidated in the rebuild from three previously-diverged copies. Missing team → silent match failure → no Pinnacle data. Fix by adding the team to the relevant desk's `teams.alias_map`.

**Sandbox has no per-desk table split**
`sandbox_trades` has no `desk_id` column — all active desks currently share identical `risk.*` config values, so `execution/position_manager.py` reads risk parameters from a single shared desk (`_shared_risk_desk()`, currently MLB) rather than per-trade desk. Revisit if a desk ever needs different risk parameters.

**Paused trades predate the rebuild**
The 12 `status="PAUSED"` trades are a one-time retroactive fix (`scripts/fix_monitor_bug_retroactive.py`) for the MONITOR-relabeling bug — new trades shouldn't accumulate PAUSED status going forward since the bug's root cause (the disabled legacy pipeline) no longer runs.

---

## 21. What Is and Isn't Committed

**Committed (source of truth):**
- All `.py` files (including stubs)
- `requirements.txt`, `README.md`, `system_overview.md`, `.env.example`
- `.streamlit/config.toml`, `deploy/systemd/*.service`/`*.timer`
- `desks/*.yaml` — desk configuration
- `data/<desk>/paper_trades.json`, `skipped_trades.json`, `shadow_trades.json`, `performance_summary.json`, `agent_cost_log.csv` — **commit after every `update_outcomes.py` run**
- `data/paper_trades.db` — sandbox SQLite database
- `data/*.bak` — frozen pre-migration/pre-rebuild snapshots (audit trail, never overwrite)

**Not committed (gitignored):**
- `.env` — contains API keys, **never commit**
- `data/snapshots/` — ephemeral runtime data
- `data/raw/` — large historical odds pulls
- `__pycache__/`, `*.pyc`, `.DS_Store`

> **Before committing:** run `python scripts/update_outcomes.py --no-ingest` to ensure per-desk `paper_trades.json` and `paper_trades.db` have all current resolutions, then commit both along with your code changes. Never `git add -A`/`.` — stage data files explicitly to avoid accidentally committing secrets or large binaries.
