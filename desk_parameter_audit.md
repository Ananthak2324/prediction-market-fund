# EdgeFund Desk Parameter Audit

Generated: 2026-07-03

Purpose: catalog every hardcoded market/desk-specific parameter in the codebase as the foundation for a desk-config YAML layer, where each market (MLB, WNBA, NFL, NBA, politics) is defined entirely by a config file rather than scattered constants.

---

## 1. Market Identifiers

| File | Line | Function/Context | Hardcoded Value | Suggested Desk Config Key |
|---|---|---|---|---|
| agent/edge_discovery_agent.py | 44-48 | module level (`SERIES`) | `{"mlb": "KXMLBGAME", "nba": "KXNBAGAME", "nfl": "KXNFLGAME", "wnba": "KXWNBAGAME"}` | desk.series_ticker |
| agent/edge_discovery_agent.py | 49-54 | module level (`SPORT_KEYS`) | `{"mlb": "baseball_mlb", "nba": "basketball_nba", "nfl": "americanfootball_nfl", "wnba": "basketball_wnba"}` | desk.odds_api.sport_key |
| agent/research_agent.py | 40 | module level (`SPORT_KEYS`) | `{"MLB": "baseball_mlb", "NBA": "basketball_nba"}` | desk.odds_api.sport_key |
| agent/research_agent.py | 235 | `_fetch_current_pinnacle` | `SPORT_KEYS.get(sport.upper(), "baseball_mlb")` (MLB default fallback) | desk.odds_api.sport_key (default) |
| analysis/bookmaker_comparison.py | 46-56 | module level (`MLB_TEAMS`) | Full list of 30 MLB team names | desk.team_names |
| analysis/bookmaker_comparison.py | 166 | `_build_kalshi_events` | `et.startswith("KXMLBGAME")` | desk.series_ticker |
| analysis/normalizer.py | 1-32 | module level (`NBA_MAP`) | NBA abbreviation→full-name mapping dict | desk.team_alias_map |
| analysis/normalizer.py | 34-51 | module level (`NFL_MAP`) | NFL abbreviation→full-name mapping dict | desk.team_alias_map |
| analysis/normalizer.py | 53-69 | module level (`MLB_MAP`) | MLB abbreviation→full-name mapping dict | desk.team_alias_map |
| analysis/normalizer.py | 71 | module level (`_MAPS`) | `{"nba": NBA_MAP, "nfl": NFL_MAP, "mlb": MLB_MAP}` | desk.team_alias_map |
| data/clients/kalshi_client.py | 6-8 | module level | `NBA_SERIES = "KXNBAGAME"`, `NFL_SERIES = "KXNFLGAME"`, `MLB_SERIES = "KXMLBGAME"` | desk.series_ticker |
| data/clients/kalshi_client.py | 10-14 | module level (`SPORT_SERIES`) | `{"nba": NBA_SERIES, "nfl": NFL_SERIES, "mlb": MLB_SERIES}` | desk.series_ticker |
| data/clients/odds_client.py | 5-7 | module level | `NBA_KEY = "basketball_nba"`, `NFL_KEY = "americanfootball_nfl"`, `MLB_KEY = "baseball_mlb"` | desk.odds_api.sport_key |
| data/clients/odds_client.py | 9-13 | module level (`SPORT_KEYS`) | `{"nba": NBA_KEY, "nfl": NFL_KEY, "mlb": MLB_KEY}` | desk.odds_api.sport_key |
| live_gap_detector.py | 35 | module level | `SERIES = {"mlb": "KXMLBGAME", "nba": "KXNBAGAME"}` | desk.series_ticker |
| live_gap_detector.py | 36 | module level | `SPORT_KEYS = {"mlb": "baseball_mlb", "nba": "basketball_nba"}` | desk.odds_api.sport_key |
| live_gap_detector.py | 41-57 | module level (`KALSHI_ALIAS`) | Kalshi sub-title → Vegas team-name alias dict, MLB+NBA specific (e.g. `"A's": "Athletics"`, `"OKC": "Thunder"`) | desk.team_alias_map |
| scripts/backtest_gap.py | 32-33 | module level | `KALSHI_FILE = "data/raw/kalshi/mlb_markets.json"`, `VEGAS_GLOB = "data/raw/vegas/mlb/*.json"` | desk.data_paths.kalshi_markets_file / vegas_glob |
| scripts/backtest_gap.py | 37-45 | module level (`KALSHI_ALIAS`) | MLB-only alias dict | desk.team_alias_map |
| scripts/fetch_odds_history.py | 43-46 | module level (`SPORT_CONFIG`) | `{"mlb": {"kalshi_file": "mlb_markets.json", ...}, "nba": {...}}` | desk.data_paths.kalshi_markets_file |
| scripts/gap_curve_tracker.py | 46-49 | module level | `SERIES = {"mlb": "KXMLBGAME", "wnba": "KXWNBAGAME"}`, `SPORT_KEYS = {"mlb": "baseball_mlb", "wnba": "basketball_wnba"}` | desk.series_ticker / desk.odds_api.sport_key |
| scripts/pull_kalshi_candles.py | 21 | module level | `SPORTS = ["nba", "nfl", "mlb"]` | desk.enabled_sports (registry) |
| scripts/run_backtest.py | 19 | module level | `SPORTS = ["nba", "nfl", "mlb"]` | desk.enabled_sports (registry) |
| scripts/schedule_snapshots.py | 46 | module level | `SERIES = {"mlb": "KXMLBGAME", "nba": "KXNBAGAME"}` | desk.series_ticker |
| scripts/snapshot_gaps.py | 41-42 | module level | `SERIES = {"mlb": ..., "nba": ..., "wnba": ...}`, `SPORT_KEYS = {...}` | desk.series_ticker / desk.odds_api.sport_key |
| scripts/snapshot_gaps.py | 49-64 | module level (`KALSHI_ALIAS`) | MLB + NBA alias dict (third near-duplicate copy) | desk.team_alias_map |
| dashboard/app.py | 298 | `fetch_today_schedule` | default arg `series_ticker: str = "KXMLBGAME"` | desk.series_ticker |
| dashboard/app.py | 427-429 | module level (tabs) | `"⚾ MLB", "🏀 WNBA"` tab labels | desk.display_name / desk.icon |
| dashboard/app.py | 444 | tab_mlb | `row.get("sport", "MLB") == "MLB"` | desk.sport_display_key |
| dashboard/app.py | 543 | tab_wnba | `row.get("sport") == "WNBA"` | desk.sport_display_key |
| dashboard/app.py | 593 | tab_wnba | `fetch_today_schedule("KXWNBAGAME")` | desk.series_ticker |
| dashboard/app.py | 641 | tab_curves | `st.selectbox("Sport", ["All", "MLB", "WNBA"], ...)` | desk.enabled_sports (registry) |
| dashboard/app.py | 1707 | footer | `"MLB + WNBA 2026"` display text | desk.display_name (aggregate) |

## 2. Signal Parameters

| File | Line | Function/Context | Hardcoded Value | Suggested Desk Config Key |
|---|---|---|---|---|
| agent/thresholds.json | 2-5 | JSON | `pinnacle_movement_threshold: 0.03`, `large_gap_warn: 0.20`, `tier1_min_gap: 0.10`, `tier2_min_gap: 0.05` | desk.thresholds.* |
| agent/research_agent.py | 47-50 | `_DEFAULT_THRESHOLDS` | Same 4 defaults duplicated as Python fallback | desk.thresholds.* |
| agent/research_agent.py | 104 | `_BASE_SYSTEM` (prompt) | "Pinnacle line has moved > 3 percentage points in the last 3 hours" | desk.thresholds.pinnacle_movement (prompt text) |
| agent/research_agent.py | 170,172,174,210,211 | `_build_verdict_prompt` | Multiple f-string injections of `TIER1_MIN_GAP`/`TIER2_MIN_GAP` into prompt text | desk.thresholds.tier1_min_gap / tier2_min_gap |
| agent/pre_filter.py | 18-20 | module level | `V_PROB_MIN = 0.20`, `V_PROB_MAX = 0.80`, `PINNACLE_MOVE_THRESHOLD = 0.05` | desk.thresholds.v_prob_min / v_prob_max / pinnacle_move_hard_gate |
| agent/pre_filter.py | 48-49 | `pre_filter` (comment) | "Pinnacle never prices an MLB/WNBA team below 20% or above 80%" — sport-specific rationale baked into a generic-looking constant | desk.thresholds.v_prob_min / v_prob_max |
| agent/edge_discovery_agent.py | 56-57 | module level | `MIN_GAP = 0.05`, `TIER1_GAP = 0.10` | desk.thresholds.tier2_min_gap / tier1_min_gap |
| agent/edge_discovery_agent.py | 613,629,647,668,685 | `classify_edge` | Edge-classification thresholds: `0.20` (anomaly), `consensus>=3 and best>=0.05`, `0.07/0.03/0.03` (sharp signal), `0.07/0.03` (retail soft), `0.05/consensus>=2` (behavioral) | desk.thresholds.large_gap_warn / consensus_min_books / sharp_signal_gap / retail_soft_gap / behavioral_gap |
| analysis/gap_calculator.py | 3 | module level | `GAP_THRESHOLD = 0.03` | desk.thresholds.backtest_gap |
| analysis/bookmaker_comparison.py | 42-44 | module level | `KALSHI_VALID_DIFF = 0.05`, `KALSHI_MID_RANGE = (0.10, 0.90)`, `GAP_MIN = 0.05` | desk.thresholds.kalshi_valid_diff / kalshi_mid_range / gap_min |
| analysis/bookmaker_comparison.py | 324,326 | `compute_kalshi_gaps` | tier1 `>=0.10`, tier2 `>=0.05` | desk.thresholds.tier1_min_gap / tier2_min_gap |
| scripts/snapshot_gaps.py | 47 | module level | `GAP_THRESHOLD = 0.05` | desk.thresholds.tier2_min_gap |
| scripts/backtest_gap.py | 263-264 | `print_results` | Gap buckets `[0-3%,3-5%,5-10%,10%+]` | desk.thresholds.gap_buckets |
| scripts/test_favorites_filter.py | 28-29 | module level | `VEGAS_FAVORITE_THRESHOLD = 0.65`, `GAP_THRESHOLD = 0.05` | desk.thresholds.favorite_prob / tier2_min_gap |
| scripts/test_favorites_filter.py | 99,118 | `print_report` | Gap gradient bins and Vegas prob tier bins | desk.thresholds.gap_gradient_bins / favorite_prob_tiers |
| scripts/update_outcomes.py | 74-75 | module level | `GAP_THRESHOLD = 0.05`, `SPREAD_THRESHOLD = 0.06` | desk.thresholds.tier2_min_gap / max_spread |
| scripts/update_outcomes.py | 315 | `ingest_new_trades` | `if v_prob < 0.20:` data-error check | desk.thresholds.v_prob_min |
| scripts/update_outcomes.py | 728-732 | `gap_bucket` | Bucket boundaries `0.07/0.10/0.15` | desk.thresholds.gap_bucket_boundaries |
| scripts/update_outcomes.py | 754-755 | `build_summary` | tier1 defined as `abs_gap >= 0.15` — **inconsistent with edge_discovery_agent's 0.10 tier1 threshold** | desk.thresholds.tier1_min_gap (flag: conflicting value across files) |
| scripts/weekly_audit.py | 39 | module level | `MIN_SAMPLE_FOR_TUNE = 30` | desk.thresholds.min_sample_for_tune |
| dashboard/app.py | 449,465,467,551,566,568 | tab_mlb/tab_wnba | Tier/action thresholds `0.10` (tier1), `0.05` (TRADE), `0.03` (WATCH) — duplicated per tab | desk.thresholds.tier1_min_gap / tier2_min_gap / watch_gap |
| dashboard/app.py | 674,744 | tab_curves | `y=5.0` threshold line; `_gc_action` 0.05/0.03 cutoffs | desk.thresholds.tier2_min_gap / watch_gap |
| dashboard/app.py | 816-818 | tab_log | Tier filter `>=0.10` | desk.thresholds.tier1_min_gap |
| dashboard/app.py | 928,1580-1581 | tab_perf / tab_sys | Win-rate min line `58`; gate conditions `wr_t1_val>=0.58`, `pval<0.10`, `clean_res>=30` | desk.thresholds.min_win_rate / max_p_value / min_sample_size |
| dashboard/app.py | 956 | tab_perf | Gap bucket order `[5-7%,7-10%,10-15%,15%+]` | desk.thresholds.gap_bucket_boundaries |
| dashboard/charts.py | 12-13,41 | `plot_gap_distribution`/`plot_win_rate_by_threshold` | `±3%` threshold lines; `52%` min win-rate line | desk.thresholds.backtest_gap / min_win_rate |
| scripts/gap_curve_analysis.py | 87,194,237 | `_style_ax`/`detect_non_monotonic`/`print_summary` | `0.05` threshold line; spike detection `0.02` and `1.5x`; `>=0.05` ever-flagged count | desk.thresholds.tier2_min_gap / spike_detection |
| scripts/gap_curve_tracker.py | 326-331 | `_BUCKET_SQL` | Time buckets `0-2h/2-6h/6-12h/12-24h/24h+` | desk.thresholds.time_buckets |
| scripts/reprocess_skipped_trades.py | 73 | `_retroactive_verdict` | `pinnacle_movement >= 0.05` | desk.thresholds.pinnacle_move_hard_gate |

## 3. Schedule Parameters

| File | Line | Function/Context | Hardcoded Value | Suggested Desk Config Key |
|---|---|---|---|---|
| agent/edge_discovery_agent.py | 70-71 | module level | `SKIP_COOLDOWN_HOURS = 6.0`, `MONITOR_COOLDOWN_HOURS = 3.0` | desk.schedule.skip_cooldown_hours / monitor_cooldown_hours |
| agent/edge_discovery_agent.py | 74 | module level | `FUNNEL_LOG_MAX = 200` (~4 days of 30-min runs) | desk.schedule.funnel_log_retention |
| agent/edge_discovery_agent.py | 195 | `_make_trade_record` | `hours > 3.0` timing_suspect | desk.schedule.timing_suspect_hours |
| agent/research_agent.py | 364 | `_build_user_message` | `hours_before > 3.0` early-snapshot warning | desk.schedule.timing_suspect_hours |
| scripts/update_outcomes.py | 53-54 | module level | `MONITOR_COOLDOWN_HOURS = 3.0`, `RESOLVE_AFTER = 0.5` | desk.schedule.monitor_cooldown_hours / resolve_after_hours |
| scripts/update_outcomes.py | 260 | `ingest_new_trades` | `hours_before > 3.0` | desk.schedule.timing_suspect_hours |
| scripts/schedule_snapshots.py | 43-44 | module level | `WINDOW_MIN = 110`, `WINDOW_MAX = 130` (minutes before game) | desk.schedule.snapshot_window_min / snapshot_window_max |
| scripts/schedule_snapshots.py | 116 | `get_well_captured_tickers` | `1.5 <= hours_before <= 2.5` | desk.schedule.well_captured_window |
| scripts/gap_curve_tracker.py | 40,44,61 | module level | `POLL_INTERVAL` (300s default), `MIN_REQ_INTERVAL = 0.2`, `PINNACLE_CACHE_TTL` (1800s default) | desk.schedule.poll_interval_seconds / min_request_interval / pinnacle_cache_ttl |
| scripts/fetch_odds_history.py | 40 | module level | `SLEEP_BETWEEN = 1.2` | desk.schedule.api_sleep_between |
| scripts/fetch_odds_history.py | 112-114 | `pull_sport` | 4-hour lookback window ending at game start | desk.schedule.historical_lookback_hours |
| scripts/weekly_audit.py | 38 | module level | `WINDOW_DAYS = 7` | desk.schedule.audit_window_days |
| execution/position_manager.py | 419 | `__main__` poll loop | `12 <= now_et.hour < 23` active poll window | desk.schedule.poll_active_hours |
| execution/position_manager.py | 338,351 | `poll_open_positions` | `estimated_end = start_dt + timedelta(hours=3)` (avg game length); `hours_to_game_end < 2` | desk.schedule.avg_game_duration_hours / near_resolution_hours |
| dashboard/app.py | 278,645 | `load_gap_curves_db` / tab_curves | `hours_window: int = 48` default; window dropdown `{24,48,168}` | desk.schedule.gap_curve_window_hours / options |
| dashboard/app.py | 1453-1455,1466,1547 | tab_sys | `sched_age < 20` (min); `age_min < 120` (pipeline stale); `age_h < 2 / < 12` (outcome updater) | desk.schedule.scheduler_alive_minutes / pipeline_stale_minutes / outcome_updater_stale_hours |

## 4. Benchmark Parameters

| File | Line | Function/Context | Hardcoded Value | Suggested Desk Config Key |
|---|---|---|---|---|
| agent/edge_discovery_agent.py | 55 | module level | `ALL_BOOKS = ["pinnacle", "draftkings", "fanduel"]` | desk.benchmark.books |
| agent/edge_discovery_agent.py | 193,491 | `_make_trade_record`/`compute_gap_matrix` | `c.get("best_book", "pinnacle")` default; Pinnacle used as stability-check anchor | desk.benchmark.primary_book |
| agent/research_agent.py | 89-93 | `_BASE_SYSTEM` (prompt) | "THE PINNACLE STABILITY RULE — Pinnacle is the sharpest sportsbook in the world" | desk.benchmark.primary_book (prompt text) |
| agent/research_agent.py | 243 | `_fetch_current_pinnacle` | `"bookmakers": "pinnacle"` | desk.benchmark.primary_book |
| analysis/bookmaker_comparison.py | 391,433 | `compute_oddsportal_calibration` | `"BetMGM"` used as calibration benchmark | desk.benchmark.calibration_book |
| data/clients/odds_client.py | 42,76 | `get_historical_odds`/`get_live_odds` | `"bookmakers": "pinnacle,draftkings,fanduel"` | desk.benchmark.books |
| live_gap_detector.py | 37,99 | module level/`fetch_vegas` | `BOOK_PRIORITY = ["pinnacle"]`; `"bookmakers": "pinnacle,draftkings,fanduel"` | desk.benchmark.book_priority / books |
| scripts/backtest_gap.py | 34 | module level | `ALL_BOOKS = ["pinnacle", "draftkings", "fanduel"]` | desk.benchmark.books |
| scripts/snapshot_gaps.py | 107,124,291 | `fetch_pinnacle`/`fetch_retail_books`/`build_rows` | `"bookmakers": "pinnacle"`; `"draftkings,fanduel"`; hardcoded `"book": "pinnacle"` on row | desk.benchmark.primary_book / secondary_books |
| scripts/gap_curve_tracker.py | 188 | `fetch_pinnacle` | `"bookmakers": "pinnacle"` | desk.benchmark.primary_book |
| scripts/test_favorites_filter.py | 88 | `print_report` | `["pinnacle", "draftkings", "fanduel"]` iteration | desk.benchmark.books |
| scripts/update_outcomes.py | 291,962 | `ingest_new_trades`/`print_summary` | Hardcoded `"book": "pinnacle"` on trade record; `["pinnacle","draftkings","fanduel"]` iteration | desk.benchmark.primary_book / books |
| dashboard/app.py | 1707 | footer | `"Benchmark: Pinnacle"` display text | desk.benchmark.primary_book (display) |

## 5. Risk Parameters

| File | Line | Function/Context | Hardcoded Value | Suggested Desk Config Key |
|---|---|---|---|---|
| core/utils.py | 88-94 | `kelly_fraction` | `min(kelly, 0.25)` — 25% max Kelly cap | desk.risk.max_kelly_fraction |
| execution/position_sizer.py | 34-35 | `calculate_position` | `quarter_kelly = full_kelly * 0.25`; `position_fraction = min(quarter_kelly, 0.10)` | desk.risk.kelly_multiplier / max_position_pct |
| execution/position_manager.py | 35,54,104,108 | module level/`init_db` | `SANDBOX_START_DATE = "2026-06-25"`; `bankroll_start REAL DEFAULT 1000.00`; insert `$1000` | desk.risk.sandbox_start_date / starting_bankroll |
| execution/position_manager.py | 345,347,349,351 | `poll_open_positions` | Exit rules: FAIR_VALUE (`>=pinnacle_prob`), STOP_LOSS (`<=-0.40`), PROFIT_TARGET (`>=0.80`), NEAR_RESOLUTION (`<2h and >0.10`) | desk.risk.stop_loss_pct / profit_target_pct / near_resolution_min_pnl |
| scripts/backfill_sandbox.py | 60 | `main` | `entry_date < "2026-06-25"` | desk.risk.sandbox_start_date |
| dashboard/app.py | 1228,1244 | tab_sandbox | `sb_config.get("start_date", "2026-06-25")`; `"Start $1,000"` annotation | desk.risk.sandbox_start_date / starting_bankroll |
| dashboard/app.py | 1325,1327 | tab_sandbox | Mirrored STOP_LOSS `-0.40` / PROFIT_TARGET `0.80` display logic | desk.risk.stop_loss_pct / profit_target_pct |
| dashboard/app.py | 1148,1194 | tab_sandbox | `deployed_pct > 0.30` warn threshold; `_sb_max_dd < 0.15` drawdown health threshold | desk.risk.max_deployed_warn_pct / max_drawdown_warn_pct |

## 6. Agent Parameters

| File | Line | Function/Context | Hardcoded Value | Suggested Desk Config Key |
|---|---|---|---|---|
| agent/memory_agent.py | 26 | module level | `MODEL = "claude-sonnet-4-6"` | desk.agent.model |
| agent/memory_agent.py | 29-31 | module level | `PRICE_INPUT=3.00`, `PRICE_CACHE_READ=0.30`, `PRICE_OUTPUT=15.00` | desk.agent.pricing |
| agent/memory_agent.py | 91 | `query` | `max_tokens=2048` | desk.agent.max_tokens |
| agent/memory_agent.py | 34-58 | `_SYSTEM_PROMPT` | "separate MLB from WNBA"; "21 trades is a small sample" — sport/sample-specific prompt text | desk.agent.system_prompt (needs templating) |
| agent/research_agent.py | 38 | module level | `MODEL = "claude-sonnet-4-6"` | desk.agent.model |
| agent/research_agent.py | 72-75 | module level | `PRICE_INPUT=3.00`, `PRICE_CACHE_READ=0.30`, `PRICE_OUTPUT=15.00`, `PRICE_PER_SEARCH=0.01` | desk.agent.pricing |
| agent/research_agent.py | 78-132 | `_BASE_SYSTEM` (prompt) | Entire system prompt is baseball-specific: "starting pitcher", "cleanup hitter", "Acuña, Strider, Seager", "top 3 hitter" | desk.agent.system_prompt (full rewrite per desk) |
| agent/research_agent.py | 126-127,395 | `_BASE_SYSTEM`/`_build_user_message` | Search query template: `"{home_team} {away_team} starting lineup injury scratch last 24 hours {date}"` | desk.agent.search_query_template |
| agent/research_agent.py | 413 | `_build_user_message` | "Report what you find about starting pitchers, injuries, lineup changes, or weather." | desk.agent.search_query_template (framing) |
| agent/research_agent.py | 627,687 | `_make_api_call`/`run` | `max_tok: int = 2048` default; retry `max_tok=1024` | desk.agent.max_tokens / max_tokens_retry |
| scripts/weekly_audit.py | 30 | import | `from agent.research_agent import MODEL, ...` (inherits model/pricing) | desk.agent.model |
| scripts/weekly_audit.py | 118-156 | `_AUDIT_PROMPT_TEMPLATE` | "sports prediction-market trading system" framing tied to MLB/WNBA usage context | desk.agent.audit_prompt_template |
| scripts/weekly_audit.py | 206 | `run_audit_llm` | `max_tokens=1024` | desk.agent.max_tokens |
| scripts/reprocess_skipped_trades.py | 36-48 | module level | `CHRONIC_KEYWORDS`/`NEW_SCRATCH_KEYWORDS` lists — MLB injury vocabulary incl. named players ("acuna","strider","seager"), "tommy john", "il" | desk.agent.chronic_keywords / new_scratch_keywords |
| data/clients/odds_client.py | 17 | module level | `CREDIT_STOP_THRESHOLD = 100` | desk.odds_api.credit_stop_threshold |
| scripts/gap_curve_tracker.py | 59-60 | comment | "Odds API limit is 6,667 calls/day" | desk.odds_api.daily_quota |

---

## Notable Findings Beyond the Table Categories

The three `KALSHI_ALIAS` dictionaries in `live_gap_detector.py` (lines 41-57), `scripts/backtest_gap.py` (lines 37-45), and `scripts/snapshot_gaps.py` (lines 49-64) are near-duplicates of each other (MLB + NBA aliases repeated independently with slight drift) — a desk refactor should unify these into one per-desk alias table rather than three copies that can silently diverge.

`dashboard/app.py` hardcodes exactly two sport tabs (`tab_mlb`, `tab_wnba`, lines ~435-632) with fully duplicated block logic differing only by variable-name suffix (`_w`) — a desk-driven dashboard should generate tabs dynamically from an enabled-desk registry.

`config/config.py`'s `DB_PATH` (`data/trade_log.db`) and the various hardcoded paths like `TRADES_FILE`, `SKIPPED_FILE` across `agent/edge_discovery_agent.py` and `scripts/update_outcomes.py` are currently global/shared rather than per-desk, which will need namespacing (e.g. `data/{desk}/paper_trades.json`) once multiple desks run concurrently.

`agent/thresholds.json` is a single global file with no sport parameter — `research_agent.py`'s `THRESHOLDS_FILE` constant loads exactly one path, so it needs to become per-desk (e.g. `agent/thresholds_mlb.json`).

Most fundamentally, the entire codebase assumes binary, two-sided, home/away markets: `execution/position_manager.py`'s game-label parsing via `game.split(" @ ")` (also relied on in `edge_discovery_agent.py`, `update_outcomes.py`, `test_agent.py`), the `remove_vig` two-outcome vig-removal in `core/utils.py`, and the Kelly position sizing in `execution/position_sizer.py` are all structurally built for two-way sports moneylines — a politics desk (N-way or non-home/away contracts) will require these to be generalized, not just parameterized via config.
