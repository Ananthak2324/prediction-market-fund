# EdgeFund — System Overview

## What Is This?

EdgeFund is an automated sports prediction market trading system. It finds pricing discrepancies between Kalshi (a regulated prediction market where you bet YES/NO on sports outcomes) and traditional sportsbooks like Pinnacle, DraftKings, and FanDuel — then decides whether to place a paper trade based on whether the discrepancy is real or just noise.

Think of it like this: if Pinnacle (the sharpest sportsbook in the world, used by professional bettors) says the Chicago White Sox have a 40% chance of winning, but Kalshi's market is pricing them at 30%, that's a 10% gap. The system asks: is Kalshi wrong because of retail crowd bias, or is there a real reason (injury, lineup change, weather) explaining the difference? If it's bias, it's a trade.

---

## The Core Idea

**Sportsbooks like Pinnacle are extremely accurate** — they employ sharp bettors and move lines quickly based on real information. Kalshi is a retail prediction market where everyday people trade, so it's more susceptible to crowd psychology: people overvalue popular teams, famous players, home games, and recent winning streaks.

When Kalshi's price diverges from what sharp money (Pinnacle) says the true probability is, there's a potential edge. The system captures that edge systematically.

---

## How It Works — Step by Step

> **Rebuilt 2026-07-04.** The system now runs on a desk-config architecture (`desks/base.yaml`, `desks/mlb.yaml`, `desks/wnba.yaml`, `desks/nfl.yaml` loaded via `core/desk_loader.py`) instead of hardcoded per-sport constants, and trade origination was consolidated to a single pipeline after a rebuild fixed two data-contamination bugs (a legacy trade pipeline that bypassed pre-filtering, and a MONITOR-verdict relabeling bug). See "The 2026-07-04 Rebuild" section below for what changed and why.

### 1. Wide Market Scan (every 2 hours, unconditional)
Every 2 hours, the server runs `snapshot_gaps.py --all-desks` across all open Kalshi markets for every active desk (MLB, WNBA) — regardless of how far away the games are. This captures gaps on markets that Kalshi has opened 1-3 days before the game, where the biggest behavioral mispricings tend to appear when retail bettors first encounter a new market.

### 2. Pre-Game Snapshot Capture (every 10 minutes, gated)
Within a tighter 20-minute window centered at 110-130 minutes before each specific game, a more precise snapshot fires. For each game in that window, it:
- Pulls Kalshi's live YES/NO price for each team
- Pulls Pinnacle's vig-free probability (removes the bookmaker's margin to get the "true" implied probability)
- Also pulls DraftKings and FanDuel lines for cross-reference
- Computes the gap between Kalshi and each book
- Saves everything to a timestamped snapshot file in `data/snapshots/`

Both pipelines feed the same snapshot format. The wide scan catches early-open gaps; the gated snapshot catches the pre-game window where lines are most actionable.

### 3. Gap Curve Tracker (always-on daemon)
A separate always-on service polls every 5 minutes and writes a row to `data/gap_curves.db` for every open Kalshi market. This builds a continuous time-series of how the gap between Kalshi and Pinnacle evolves from the moment a market opens until game start. The dashboard's Gap Curves tab reads this database to answer: does the gap close monotonically, does it spike and correct, and what's the optimal entry window?

### 4. Edge Discovery (every 30 minutes) — the sole trade-origination pipeline
Every 30 minutes, the edge discovery agent (`agent/edge_discovery_agent.py`) makes its own live API calls to Kalshi and Pinnacle — independent of the snapshot pipeline — and classifies any gap above 5% into one of 5 edge types. Since the 2026-07-04 rebuild, this is the **only** path a trade can be created through: `scripts/update_outcomes.py`'s old `ingest_new_trades()` (a second, unprotected trade-origination path that shared the same ledger) has been permanently disabled. Every trade record carries `"pipeline_source": "edge_discovery_agent"` for audit.

| Edge Type | What It Means | Default Action |
|-----------|--------------|----------------|
| `BEHAVIORAL_RETAIL` | Kalshi diverges from Pinnacle due to crowd bias | Lean TRADE |
| `MULTI_BOOK_CONSENSUS` | All 3 books agree Kalshi is wrong | Strong TRADE |
| `SHARP_SIGNAL` | Pinnacle alone diverges — sharp money knows something | Lean SKIP |
| `RETAIL_BOOK_SOFT` | Only DK/FanDuel diverge, Pinnacle agrees with Kalshi | MONITOR |
| `MARKET_ANOMALY` | Gap ≥ 20% — almost always real information | SKIP |

Edge discovery scans both pre-game and in-progress markets. In-game gaps are real opportunities — if a team is losing early and Kalshi overreacts, but Pinnacle's pre-game line (already accounting for the team's true quality) suggests the market has overcorrected, that's a behavioral edge worth evaluating.

**Cost control:** Before calling the research agent on any candidate, the system checks three per-desk caches (`data/<desk>/paper_trades.json`, `data/<desk>/skipped_trades.json`, `data/<desk>/monitor_cache.json`), with a 1-hour cooldown on both SKIP and MONITOR re-checks (reduced from 6h/3h in the rebuild once the legacy pipeline stopped starving the clean one of candidates). Only tickers already traded via `edge_discovery_agent` count as "already traded" — this prevents old contamination from blocking re-evaluation of a game.

### 5. Research Agent (triggered per new candidate)
For any gap above 5% that clears the cooldown check, the research agent is called. It:
- Searches the web for breaking news: injuries, lineup scratches, weather
- Checks if Pinnacle's line has moved since the snapshot (sharp money moving = SKIP)
- Validates that the Pinnacle line is plausible (v_prob < 20% is rejected as a data-matching error before the agent is even called)
- Applies decision rules specific to the edge type it was handed
- Returns a verdict: **TRADE**, **SKIP**, or **MONITOR**

A TRADE verdict then passes through **signal gates** (`apply_signal_gates()`) before it's logged: gap 5-10% → **Tier A**, full 0.25x Kelly sizing, actively traded. Gap 10-15% → **Tier B**, reduced 0.10x Kelly, actively traded but pending a 20-resolved-trade validation window (upgrades to full sizing above 55% win rate, downgrades to shadow-only below 45%). Gap ≥15% (**Tier C**) or a `BUY_NO` signal → routed to `data/<desk>/shadow_trades.json` instead — tracked with real outcomes but never actually paper-traded, since both were found to underperform pre-rebuild and are under investigation. MONITOR means "re-evaluate later" and is held in the cooldown cache. SKIP means "real information explains the gap" and is logged to `skipped_trades.json` for tracking. If the Anthropic API call fails for any reason, nothing is logged — the system does not guess.

If a later re-evaluation on a cleaner snapshot downgrades an already-logged trade to MONITOR or SKIP, the trade is marked `status: "PAUSED"` (with a `paused_reason`) rather than silently overwritten — it stays visible in the raw trade log for audit but is excluded from every win-rate/EV calculation.

### 6. Position Manager (always-on daemon)
Watches all open paper trades and:
- Opens a sandbox position when a TRADE is logged, sized by Kelly fraction × the tier's kelly multiplier (0.25 for Tier A, 0.10 for Tier B) against a $1,000 starting bankroll (sandbox reset to a clean start on 2026-07-05)
- Enforces a hard cap of 4 concurrent open positions and an automatic circuit breaker that pauses new positions if drawdown exceeds 20%
- Monitors live Kalshi prices for exit signals
- Resolves trades when games settle
- Sends iMessage notifications for every entry and resolution

### 7. Outcomes Updater (every 15 minutes)
Checks settled Kalshi markets and records whether each paper trade won or lost. Also handles snapshot replacement — if a cleaner snapshot arrives for a game that was logged with a timing-suspect entry (>3 hours before game), it validates and replaces the original record. Updates the performance summary file.

### 8. Daily Digest (2 AM UTC)
Sends a daily iMessage summary: trades logged today, win rate, sandbox bankroll, EV per trade.

### 9. Weekly Audit (Sunday nights)
Runs a deeper analysis: statistical significance of the win rate, whether the research agent is helping or hurting, whether thresholds should be adjusted.

---

## The Infrastructure

### Cloud Server (Google Cloud us-central1-b, always-on)
A Linux VPS at `34.134.239.151` running 24/7. Handles everything:

| Service | Schedule | Purpose |
|---------|----------|---------|
| `prediction-fund-widescan.timer` | Every 2 hours | Unconditional all-market snapshot |
| `prediction-fund-snapshot.timer` | Every 10 min (gated) | Pre-game precision snapshot |
| `prediction-fund-edge-discovery.timer` | Every 30 min | Live gap scan + research agent |
| `prediction-fund-outcomes.timer` | Every 15 min | Resolve settled trades |
| `prediction-fund-gap-tracker.service` | Always-on | 5-min gap curve time-series |
| `prediction-fund-positions.service` | Always-on | Position manager + exit signals |
| `prediction-fund-dashboard.service` | Always-on | Streamlit dashboard (port 8501) |
| `prediction-fund-digest.timer` | 2 AM UTC daily | iMessage daily summary |
| `prediction-fund-weekly-audit.timer` | Sunday 11 PM UTC | Statistical audit |

### Dashboard
Live at `http://34.134.239.151:8501`. Tabs are generated dynamically, one per active desk (currently MLB, WNBA — NFL is defined but `desk_status: PENDING` so it doesn't appear), plus fixed tabs:
- **[Desk] tabs** (e.g. MLB, WNBA) — Open Market Gap Scanner showing all currently open Kalshi contracts sorted by gap size, with Action column (TRADE ✓ / WATCH / —). Only pre-game markets shown.
- **Gap Curves** — Time-series chart of |gap| vs hours-to-game for each market, built from `gap_curves.db`. Shows the full lifecycle of how a gap opens and closes.
- **Trade Log** — Every paper trade with research verdict, gap, outcome, across all active desks.
- **Performance** — Win rate by tier, EV, statistical significance, p-value — rebuilt from the combined raw trades + shadow trades across all active desks so it always matches the headline metric cards.
- **Sandbox** — Simulated bankroll P&L, reset to a clean $1,000 start on 2026-07-05.
- **Investigation** — Tier B validation progress toward its 20-trade upgrade/downgrade threshold, Tier C and BUY_NO shadow win rates.
- **Intelligence** — this chatbot.
- **System** — Health check for all services.

### Your Mac (local)
- `scripts/sync_from_vps.sh` — pulls latest data from VPS so local files stay current
- `scripts/relay_notifications.py` — forwards iMessage queue from server to your Messages app (the VPS can't send iMessages; only a Mac can)

### The Data Flow
```
VPS: snapshot_gaps.py → data/snapshots/YYYY-MM-DD_HHMM.json
VPS: gap_curve_tracker.py → data/gap_curves.db (every 5 min)
VPS: edge_discovery_agent.py → research_agent → paper_trades.json + sandbox
VPS: update_outcomes.py → resolves trades, updates paper_trades.json
        ↓ (sync_from_vps.sh every 5 min)
Mac: data/ folder mirrors VPS
        ↓
Mac: relay_notifications.py → iMessage to your phone
Dashboard reads directly from VPS (always up, no Mac needed)
```

---

## Key Data Files

Every desk (MLB, WNBA, NFL) has its own namespaced data directory since the 2026-07-04 rebuild — `data/<desk_id>/` — so trades, skips, and shadow trades never mix across sports.

| File | What It Contains |
|------|-----------------|
| `data/<desk>/paper_trades.json` | Every logged trade for that desk — price at capture, research verdict, outcome, tier, status (OPEN/CLOSED/PAUSED), pipeline_source |
| `data/<desk>/skipped_trades.json` | SKIP verdicts from research agent — tracked to see if agent filtering helps |
| `data/<desk>/shadow_trades.json` | Tier C and BUY_NO candidates — tracked with real outcomes but never actually paper-traded |
| `data/<desk>/monitor_cache.json` | MONITOR cooldown registry — 1h cooldown |
| `data/<desk>/performance_summary.json` | Per-desk win rates, EV, tier/signal breakdowns |
| `data/gap_curves.db` | SQLite time-series of gap evolution per market (5-min resolution) |
| `data/paper_trades.db` | Sandbox portfolio (SQLite) — shared across desks, no per-desk table split yet |
| `data/snapshots/` | Raw snapshot files, one per run |
| `outputs/edge_discovery_*.json` | Full candidate + verdict output per sport per day |
| `desks/*.yaml` | Per-desk configuration (thresholds, Kelly multipliers, team alias maps, agent prompts) loaded via `core/desk_loader.py` |

Old shared top-level files (`data/paper_trades.json`, etc.) are frozen snapshots from the moment of migration — kept for audit, no longer written to.

---

## API Usage

| API | What It's Used For | Quota |
|-----|--------------------|-------|
| Kalshi (unauthenticated) | Live market prices, open contracts | No quota limit |
| The Odds API (Pinnacle) | Vig-free probability benchmark | 6,667 calls/day (daily reset) |
| The Odds API (DK/FanDuel) | Multi-book consensus check | Same pool |
| Anthropic (Claude) | Research agent — web search + reasoning | Pay-per-token; cooldown system limits calls |

The Odds API is not a quota risk — at 5-minute gap-curve polling plus 30-minute edge discovery, usage is well under 1,000 calls/day. Anthropic cost is the variable to watch: each research agent call costs ~$0.09. With the cooldown system in place, a single candidate is researched at most once every 3 hours.

---

## What Gets Tracked

- **Paper trades** — every trade with: game, team, gap size, Kalshi price, Pinnacle price, research verdict, agent reasoning, outcome, tier, status
- **Win rate** — overall and by tier (Tier A 5-10% gap, Tier B 10-15% gap, Tier C 15%+ gap shadow-only)
- **EV per trade** — expected value, the core metric of whether the edge is real
- **Skipped trades** — trades the research agent rejected; tracked to see if the agent is filtering correctly
- **Shadow trades** — Tier C and BUY_NO candidates tracked with real outcomes but never actually traded
- **Agent cost** — every Claude API call logged with token counts and dollar cost, per desk
- **Sandbox bankroll** — starting at $1,000 (reset 2026-07-05), sized by tier-specific Kelly multiplier, capped at 4 concurrent positions with a 20% drawdown circuit breaker

---

## The 2026-07-04 Rebuild

Prior to this date, a strategy-analysis report found two structural bugs contaminating every performance number:
1. **Two independent trade-origination pipelines shared one ledger.** `edge_discovery_agent.py` and `update_outcomes.py`'s own `ingest_new_trades()` both wrote to `paper_trades.json` independently — 89% of trades came from the unprotected legacy path, which also starved the protected pipeline of candidates via cooldown/already-traded checks. **Fix:** `ingest_new_trades()` permanently disabled; `edge_discovery_agent.py` is now the sole origination path.
2. **A silent MONITOR-relabeling bug.** Re-evaluating an already-logged trade on a cleaner snapshot unconditionally overwrote its fields even when the new verdict was a downgrade to MONITOR/SKIP. **Fix:** downgrades now set `status: "PAUSED"` instead of silently overwriting; 12 contaminated trades were fixed retroactively and excluded from all win-rate math (still visible in the raw log).

The rebuild also added: the A/B/C tier + signal-gate system (replacing the old Tier 1/Tier 2 split), a concurrent-position cap and drawdown circuit breaker, a full desk-config layer (`desks/*.yaml` + `core/desk_loader.py`) replacing scattered hardcoded per-sport constants, and desk-namespaced data directories. Clean data collection began 2026-07-05 with a reset sandbox.

## Current Status

As of the rebuild, treat any trade dated before 2026-07-04 as pre-rebuild/contaminated data (still visible for audit but excluded from live performance stats). For live numbers, query the trade data and per-desk `performance_summary.json` directly rather than relying on any date-stamped summary in this document — this file describes architecture, not a point-in-time snapshot.

---

## What It Is NOT

- Not a real-money trading system (yet) — all trades are paper/simulated
- Not a gambling bot — it operates within Kalshi's regulated, legal prediction market
- Not fully autonomous yet — verdicts are logged but no real capital is deployed until the win rate has statistical significance across a larger sample

---

## The Goal

Build enough of a track record (statistically significant win rate across 50+ trades) to validate the edge is real, then deploy real capital with proper Kelly sizing. The infrastructure is built to scale to new markets (NFL is already defined in `desks/nfl.yaml` but `PENDING` until Kalshi opens markets; adding a new sport is a new desk YAML file, not a code change) via `--desk <ID>` / `--all-desks`.
