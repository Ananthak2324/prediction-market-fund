"""
agent/edge_discovery_agent.py

Automated multi-book edge scanner. Fetches Kalshi markets + Pinnacle, DraftKings,
and FanDuel; computes a gap matrix for each game; ranks edge candidates; and
auto-triggers the research agent for any candidate above the minimum threshold.

Desk-driven (2026-07-04 rebuild) — every sport-specific constant (series
ticker, odds-api sport key, gap thresholds, book list, alias map, cooldowns)
comes from desks/<desk_id>.yaml via core.desk_loader.DeskConfig.

Two modes:
  --upcoming   scan every open Kalshi market regardless of date (scheduled/default) —
               Kalshi opens markets 1-3 days before game time, and the biggest
               behavioral mispricings tend to appear when retail first encounters
               a new market, so early-window games are evaluated too, not just
               today's (see 2026-07-06 fix: this used to silently skip everything
               but today, undercovering ~85% of open markets at any given time).
  --date YYYY-MM-DD   scan games on one specific date only

Usage:
    python agent/edge_discovery_agent.py --desk MLB --upcoming
    python agent/edge_discovery_agent.py --desk MLB --date 2026-07-01
    python agent/edge_discovery_agent.py --desk WNBA --upcoming --no-research
    python agent/edge_discovery_agent.py --desk MLB --upcoming --save
    python agent/edge_discovery_agent.py --all-desks --upcoming --save
"""
import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils import american_to_prob, remove_vig, ticker_to_utc
from core.desk_loader import DeskConfig, get_desk, get_active_desks
from scripts.snapshot_gaps import match_team, kalshi_mid

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KALSHI_BASE = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
ODDS_BASE   = os.getenv("ODDS_API_BASE",   "https://api.theoddsapi.com")
ODDS_KEY    = os.getenv("ODDS_API_KEY",    "")


def apply_signal_gates(desk: DeskConfig, candidate: dict, verdict: dict) -> dict:
    """
    Apply empirical signal gates (2026-07-04 rebuild, per strategy_analysis_report.md):
      Tier A (5-10%):  confirmed positive EV — full sizing (0.25x Kelly)
      Tier B (10-15%): n=8 too small to conclude a negative signal, but not yet
                        proven either — trade at reduced sizing (0.10x Kelly)
                        while validation data accumulates
      Tier C (15%+):   EV -1.00 confirmed negative — shadow only, never trades
      BUY_NO:          37.5% WR vs 62.5% for BUY_YES — suspended pending
                        investigation of the chronic-injury override interaction;
                        shadow only

    Only rewrites verdicts the agent already recommended as TRADE — a genuine
    SKIP or MONITOR from the agent means real information was found and is
    left untouched.
    """
    if verdict.get("recommendation") != "TRADE":
        return verdict

    gap    = candidate.get("best_abs_gap") or abs(candidate.get("gap") or 0)
    signal = candidate.get("signal", "")
    gap_min      = desk.gap_min
    tier_b_min   = desk.tier_b[0]
    tier_c_min   = desk.tier_c[0]
    tier_kelly   = desk.tier_kelly

    if gap >= tier_c_min:
        return {
            **verdict,
            "recommendation": "SHADOW",
            "tier": "C",
            "shadow_reason": (
                f"Tier C ({gap:.1%} gap) suspended. EV -1.00 on all resolved "
                f"Tier C trades. Logging as shadow trade only."
            ),
        }

    if signal == "BUY_NO":
        return {
            **verdict,
            "recommendation": "SHADOW",
            "tier": "A" if gap < tier_b_min else "B",
            "shadow_reason": (
                "BUY_NO suspended — 37.5% WR vs 62.5% for BUY_YES. Investigating "
                "chronic injury override interaction. Logging as shadow trade only."
            ),
        }

    if tier_b_min <= gap < tier_c_min:
        return {
            **verdict,
            "recommendation": "TRADE",
            "tier": "B",
            "kelly_multiplier_override": tier_kelly["B"],
            "sizing_note": (
                "Tier B reduced sizing — validation phase. n=8 observed sample "
                "insufficient to conclude signal direction. Full sizing resumes "
                "after 20 resolved Tier B clean trades."
            ),
        }

    if gap_min <= gap < tier_b_min:
        return {
            **verdict,
            "recommendation": "TRADE",
            "tier": "A",
            "kelly_multiplier_override": tier_kelly["A"],
        }

    return {
        **verdict,
        "recommendation": "SHADOW",
        "shadow_reason": f"Gap {gap:.1%} below {gap_min:.0%} minimum threshold.",
    }


LOG_FILE    = os.path.join(BASE, "data", "snapshots", "edge_discovery_log.txt")
ET          = ZoneInfo("America/New_York")

FUNNEL_LOG_MAX = 200  # keep ~4 days of 30-min runs per desk


# ── desk-scoped file paths ──────────────────────────────────────────────────

def _trades_file(desk: DeskConfig) -> str:
    return os.path.join(BASE, desk.paper_trades_path)


def _skipped_file(desk: DeskConfig) -> str:
    return os.path.join(BASE, desk.skipped_trades_path)


def _shadow_file(desk: DeskConfig) -> str:
    return os.path.join(BASE, desk.shadow_trades_path)


def _monitor_cache_file(desk: DeskConfig) -> str:
    return os.path.join(BASE, desk.monitor_cache_path)


def _funnel_log_file(desk: DeskConfig) -> str:
    return os.path.join(BASE, desk.funnel_log_path)


def _resolve_cooldown_hours(desk: DeskConfig, event_ticker: str, now: datetime, default_hours: float) -> float:
    """
    Tiered cooldown (2026-07-17): a game far from start time is re-checked
    rarely (little chance anything's changed); a game close to start time
    keeps the original fast cadence, since that's when real news (late
    scratches, lineup changes) actually breaks. Replaces a flat cooldown
    that was re-researching the same far-out candidate dozens of times
    before it ever played.

    Falls back to `default_hours` when hours-before-game can't be
    determined — event tickers with no embedded time and no odds-api
    start_time fallback available at this point in the pipeline (this
    function runs before compute_gap_matrix() builds book_index, so only
    ticker_to_utc()'s own parsing is available here).
    """
    tiers = desk.get("schedule.cooldown_tiers")
    if not tiers:
        return default_hours
    start_utc = ticker_to_utc(event_ticker)
    if not start_utc:
        return default_hours
    hours_before = (start_utc - now).total_seconds() / 3600
    for tier in tiers:
        if hours_before >= tier["min_hours_before_game"]:
            return tier["cooldown_hours"]
    return default_hours


def _load_evaluated_tickers(desk: DeskConfig) -> tuple[set[str], set[str]]:
    """
    Returns (traded_tickers, cooldown_tickers).
    - traded_tickers: already logged to paper_trades — never re-research
    - cooldown_tickers: recently SKIPped or MONITORed — skip until cooldown expires
    """
    traded: set[str] = set()
    cooldown: set[str] = set()
    now = datetime.now(timezone.utc)
    skip_cooldown_hours    = desk.cooldown_hours
    monitor_cooldown_hours = desk.get("schedule.monitor_cooldown_hours", 1.0)

    trades_file = _trades_file(desk)
    if os.path.exists(trades_file):
        try:
            with open(trades_file) as f:
                for t in json.load(f):
                    # Only count tickers claimed by this pipeline itself — legacy
                    # trades (no pipeline_source, or a different one, or PAUSED)
                    # must not block edge_discovery_agent from re-evaluating a game.
                    if (t.get("pipeline_source") == "edge_discovery_agent"
                            and t.get("status") in ("OPEN", "CLOSED")):
                        traded.add(t.get("event_ticker", ""))
        except (json.JSONDecodeError, ValueError):
            pass

    skipped_file = _skipped_file(desk)
    if os.path.exists(skipped_file):
        try:
            with open(skipped_file) as f:
                for t in json.load(f):
                    et = t.get("event_ticker", "")
                    skipped_at = t.get("skipped_at", "")
                    try:
                        age_h = (now - datetime.fromisoformat(skipped_at)).total_seconds() / 3600
                        cooldown_h = _resolve_cooldown_hours(desk, et, now, skip_cooldown_hours)
                        if age_h < cooldown_h:
                            cooldown.add(et)
                    except Exception:
                        cooldown.add(et)
        except (json.JSONDecodeError, ValueError):
            pass

    monitor_cache = _monitor_cache_file(desk)
    if os.path.exists(monitor_cache):
        try:
            with open(monitor_cache) as f:
                for et, ts in json.load(f).items():
                    try:
                        age_h = (now - datetime.fromisoformat(ts)).total_seconds() / 3600
                        cooldown_h = _resolve_cooldown_hours(desk, et, now, monitor_cooldown_hours)
                        if age_h < cooldown_h:
                            cooldown.add(et)
                    except Exception:
                        cooldown.add(et)
        except (json.JSONDecodeError, ValueError):
            pass

    return traded, cooldown


def _update_monitor_cache(desk: DeskConfig, event_tickers: list[str]) -> None:
    monitor_cache = _monitor_cache_file(desk)
    cache: dict[str, str] = {}
    if os.path.exists(monitor_cache):
        try:
            with open(monitor_cache) as f:
                cache = json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    now_iso = datetime.now(timezone.utc).isoformat()
    for et in event_tickers:
        cache[et] = now_iso
    os.makedirs(os.path.dirname(monitor_cache), exist_ok=True)
    with open(monitor_cache, "w") as f:
        json.dump(cache, f, indent=2)


def _append_funnel_entry(desk: DeskConfig, entry: dict) -> None:
    funnel_log_file = _funnel_log_file(desk)
    log: list = []
    try:
        with open(funnel_log_file) as f:
            log = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    log.append(entry)
    log = log[-FUNNEL_LOG_MAX:]
    os.makedirs(os.path.dirname(os.path.abspath(funnel_log_file)), exist_ok=True)
    with open(funnel_log_file, "w") as f:
        json.dump(log, f, indent=2)


def _sum_last_n_costs(desk: DeskConfig, n: int) -> float:
    """Sum the last n rows in agent_cost_log.csv — used to capture per-run API cost."""
    if n <= 0:
        return 0.0
    cost_log = os.path.join(BASE, desk.agent_cost_log_path)
    try:
        with open(cost_log, newline="") as f:
            rows = list(csv.DictReader(f))
        return round(sum(float(r.get("estimated_cost_usd", 0)) for r in rows[-n:]), 6)
    except Exception:
        return 0.0


def _make_trade_record(desk: DeskConfig, c: dict, snap_time: str) -> dict:
    """Convert an edge discovery candidate + research verdict into paper_trades.json format."""
    abs_gap = c.get("best_abs_gap") or abs(c.get("gap") or 0)
    hours   = c.get("hours_before_game") or c.get("hours_until") or 0
    verdict = c.get("research", {})
    return {
        "trade_id":           f"{snap_time}|{c.get('event_ticker', '')}",
        "snapshot_time":      snap_time,
        "snapshot_file":      f"edge_discovery_{c.get('sport','').lower()}_{snap_time[:10]}.json",
        "sport":              c.get("sport", ""),
        "game":               c.get("game", ""),
        "team":               c.get("team", ""),
        "side":               c.get("side", ""),
        "start_utc":          c.get("start_utc", ""),
        "kalshi_ticker":      c.get("kalshi_ticker", ""),
        "event_ticker":       c.get("event_ticker", ""),
        "k_prob":             c.get("k_prob"),
        "k_bid":              None,
        "k_ask":              None,
        "spread":             None,
        "v_prob":             c.get("v_prob") or c.get("pinnacle_prob"),
        "gap":                c.get("gap"),
        "abs_gap":            round(abs_gap, 4),
        "signal":             c.get("signal"),
        "book":               c.get("best_book", "pinnacle"),
        "hours_before_game":  round(hours, 2),
        "timing_suspect":     hours > 3.0,
        "valid_for_analysis": True,
        "replacement_flags":  [],
        "agent_verdict":      verdict.get("recommendation"),
        "agent_confidence":   verdict.get("confidence"),
        "agent_reasoning":    verdict.get("reasoning"),
        "gap_explanation":    verdict.get("gap_explanation"),
        "gap_type":           verdict.get("gap_type"),
        "news_found":         verdict.get("news_found"),
        "news_detail":        verdict.get("news_detail"),
        "news_source":        verdict.get("news_source"),
        "pitcher_confirmed":  verdict.get("pitcher_confirmed"),
        "weather_issue":      verdict.get("weather_issue"),
        "pinnacle_stable":    verdict.get("pinnacle_stable"),
        "pinnacle_movement":  verdict.get("pinnacle_movement"),
        "outcome":            None,
        "correct":            None,
        "resolution_price":   None,
        "resolved_at":        None,
        # ── 2026-07-04 rebuild: pipeline consolidation + signal-gate fields ──
        "pipeline_source":    "edge_discovery_agent",
        "desk_id":            desk.desk_id,
        "status":             "OPEN",
        "tier":               c.get("tier") or verdict.get("tier"),
        "kelly_multiplier_used": verdict.get("kelly_multiplier_override", desk.tier_kelly.get("A", 0.25)),
    }


def _log_edge_trades(
    desk: DeskConfig,
    trade_signals: list[dict],
    skip_signals: list[dict],
    snap_time: str,
    shadow_signals: list[dict] | None = None,
) -> None:
    """
    Persist TRADE, SKIP, and SHADOW verdicts from edge discovery.

    TRADE  → appended to paper_trades.json, sandbox position opened, iMessage sent.
    SKIP   → appended to skipped_trades.json only.
    SHADOW → appended to shadow_trades.json only. Tracked with outcomes for future
             analysis (Tier C, BUY_NO) but never touches paper_trades.json or opens
             a sandbox position — see apply_signal_gates().
    Deduplication is by event_ticker — same game won't be logged twice.
    """
    shadow_signals = shadow_signals or []
    trades_file  = _trades_file(desk)
    skipped_file = _skipped_file(desk)
    shadow_file  = _shadow_file(desk)

    # ── Load existing trades and build dedup index ─────────────────────────
    existing: list[dict] = []
    if os.path.exists(trades_file):
        try:
            with open(trades_file) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    event_index = {t["event_ticker"]: i for i, t in enumerate(existing)}

    # ── SHADOW verdicts → shadow_trades.json ───────────────────────────────
    if shadow_signals:
        shadow_list: list[dict] = []
        if os.path.exists(shadow_file):
            try:
                with open(shadow_file) as f:
                    shadow_list = json.load(f)
            except (json.JSONDecodeError, ValueError):
                pass
        for c in shadow_signals:
            record  = _make_trade_record(desk, c, snap_time)
            verdict = c.get("research", {})
            shadow_list.append({
                k: record.get(k)
                for k in ("trade_id", "event_ticker", "kalshi_ticker", "game", "team",
                          "signal", "gap", "abs_gap", "start_utc", "snapshot_time",
                          "hours_before_game", "timing_suspect")
            } | {
                "tier":              verdict.get("tier", record.get("tier")),
                "shadowed_at":       datetime.now(timezone.utc).isoformat(),
                "shadow_reason":     verdict.get("shadow_reason", ""),
                "agent_reasoning":   verdict.get("reasoning"),
                "agent_confidence":  verdict.get("confidence"),
                "news_found":        verdict.get("news_found"),
                "pinnacle_stable":   verdict.get("pinnacle_stable"),
                "shadow_outcome":    None,
                "shadow_correct":    None,
                "shadow_resolved_at": None,
            })
            _log(f"  [SHADOW LOGGED] {c['game']} — {c['team']} ({verdict.get('shadow_reason','')[:60]})")
        os.makedirs(os.path.dirname(shadow_file), exist_ok=True)
        with open(shadow_file, "w") as f:
            json.dump(shadow_list, f, indent=2)

    # ── SKIP verdicts → skipped_trades.json ───────────────────────────────
    if skip_signals:
        skipped: list[dict] = []
        if os.path.exists(skipped_file):
            try:
                with open(skipped_file) as f:
                    skipped = json.load(f)
            except (json.JSONDecodeError, ValueError):
                pass
        for c in skip_signals:
            record  = _make_trade_record(desk, c, snap_time)
            verdict = c.get("research", {})
            skipped.append({
                k: record.get(k)
                for k in ("trade_id", "event_ticker", "kalshi_ticker", "game", "team",
                          "signal", "gap", "abs_gap", "start_utc", "snapshot_time",
                          "hours_before_game", "timing_suspect")
            } | {
                "skipped_at":        datetime.now(timezone.utc).isoformat(),
                "pre_filter_skip":   False,
                "skip_reason":       verdict.get("skip_reason"),
                "agent_reasoning":   verdict.get("reasoning"),
                "news_found":        verdict.get("news_found"),
                "news_detail":       verdict.get("news_detail"),
                "pinnacle_stable":   verdict.get("pinnacle_stable"),
                "pinnacle_movement": verdict.get("pinnacle_movement"),
                "weather_issue":     verdict.get("weather_issue"),
            })
            _log(f"  [SKIP LOGGED] {c['game']} — {c['team']}")
        os.makedirs(os.path.dirname(skipped_file), exist_ok=True)
        with open(skipped_file, "w") as f:
            json.dump(skipped, f, indent=2)

    # ── TRADE verdicts → paper_trades.json + sandbox + iMessage ──────────
    for c in trade_signals:
        et = c.get("event_ticker", "")
        if et in event_index:
            _log(f"  [TRADE] Already logged, skipping duplicate: {c['game']} — {c['team']}")
            continue

        record = _make_trade_record(desk, c, snap_time)
        existing.append(record)
        event_index[et] = len(existing) - 1

        os.makedirs(os.path.dirname(trades_file), exist_ok=True)
        with open(trades_file, "w") as f:
            json.dump(existing, f, indent=2)

        _log(f"  [TRADE LOGGED] {c['game']} — {c['team']} | {c['signal']} "
             f"| gap={c.get('best_abs_gap', 0):.1%} | {c.get('best_book','').upper()}")

        try:
            from execution.position_manager import open_sandbox_position
            open_sandbox_position(record, desk)
        except Exception as _sb_err:
            _log(f"  [SANDBOX] {c['game']}: {_sb_err}")

        try:
            from core.notifications import send_imessage
            abs_gap = c.get("best_abs_gap") or abs(c.get("gap") or 0)
            tier    = record.get("tier") or desk.gap_tier(abs_gap)
            gap     = c.get("gap") or 0
            verdict = c.get("research", {})
            msg = (
                f"\U0001F7E2 TRADE LOGGED (Edge Discovery)\n"
                f"{c.get('game', '')}\n"
                f"{c.get('signal', '')} {c.get('team', '')}  |  Tier {tier}\n"
                f"Kalshi {c.get('k_prob', 0):.1%}  vs  Pinnacle {(c.get('v_prob') or 0):.1%}"
                f"   (gap {gap:+.1%})\n"
                f"Agent: TRADE ({verdict.get('confidence', '?')})"
            )
            send_imessage(msg)
        except Exception as _notify_err:
            _log(f"  [NOTIFY] {c['game']}: {_notify_err}")


# ── fetch helpers ─────────────────────────────────────────────────────────────

def fetch_kalshi_markets(series: str) -> list[dict]:
    resp = requests.get(
        f"{KALSHI_BASE}/markets",
        params={"series_ticker": series, "status": "open", "limit": 200},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("markets", [])


def fetch_all_books(sport_key: str, books: list[str]) -> dict[str, dict]:
    """
    Returns {(home_team, away_team, date_et): {book: {home_team: vf_prob, away_team: vf_prob, vig}}}
    fetched in a single API call for all three books.

    Keyed by date as well as team names (2026-07-21 fix) — the same two
    teams commonly play multiple games in the current window (any MLB
    series), and keying by team names alone silently collapsed every later
    game in a series onto the first one encountered, causing later games to
    be matched against a different day's stale prices. `date_et` is derived
    from the odds-api's own `start_time` (ET calendar date) — falls back to
    `None` if `start_time` is missing/unparseable, which restores the old
    team-names-only behavior for just that one entry rather than dropping it.
    """
    resp = requests.get(
        f"{ODDS_BASE}/odds/",
        params={
            "sport_key":  sport_key,
            "markets":    "h2h",
            "bookmakers": ",".join(books),
            "oddsFormat": "american",
        },
        headers={"x-api-key": ODDS_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    games = resp.json().get("data", [])

    index: dict[tuple, dict] = {}
    for g in games:
        home = g["home_team"]
        away = g["away_team"]
        date_et = None
        raw_start = g.get("start_time")
        if raw_start:
            try:
                date_et = datetime.fromisoformat(raw_start.replace("Z", "+00:00")).astimezone(ET).date()
            except (ValueError, AttributeError):
                date_et = None
        key = (home, away, date_et)
        if key in index:
            continue
        book_data: dict[str, dict] = {}
        for bk in g.get("books", []):
            bname    = bk.get("book", "")
            outcomes = {o["name"]: o["price"] for o in bk.get("outcomes", [])}
            if home not in outcomes or away not in outcomes:
                continue
            h_odds, a_odds = outcomes[home], outcomes[away]
            h_vf, a_vf     = remove_vig(h_odds, a_odds)
            vig             = american_to_prob(h_odds) + american_to_prob(a_odds) - 1.0
            book_data[bname] = {
                home: h_vf,
                away: a_vf,
                "_vig":       round(vig, 4),
                "_home_odds": h_odds,
                "_away_odds": a_odds,
            }
        if book_data:
            # start_time is the odds-api's own verified game start —
            # needed as a fallback in compute_gap_matrix() for desks whose
            # Kalshi ticker doesn't encode a time component (see
            # ticker_to_utc()'s docstring; confirmed 2026-07-16 that WNBA
            # tickers have no HHMM segment at all, unlike MLB's). Do NOT use
            # Kalshi's own occurrence_datetime/expiration fields for this —
            # cross-validated against this same field on a real game and
            # found them ~3h off (closer to game settlement than tip-off).
            index[key] = {"home": home, "away": away, "books": book_data, "start_time": g.get("start_time")}
    return index


# ── team matching ─────────────────────────────────────────────────────────────

def _get_vegas_teams(book_index: dict) -> list[str]:
    teams = set()
    for v in book_index.values():
        teams.add(v["home"])
        teams.add(v["away"])
    return list(teams)


def _match_game(k_names: list[str], book_index: dict, alias_map: dict, target_date=None) -> tuple | None:
    """
    Find the (home, away, date) key in book_index that matches both Kalshi
    sub-titles. When `target_date` is known (the Kalshi ticker's own game
    date — always available for MLB), only considers entries for that exact
    date, since the same two teams commonly play multiple games in the
    current window (any MLB series) and matching on team names alone would
    silently pick whichever game happened to be fetched first, regardless
    of date (2026-07-21 fix — this was matching later-series games against
    an earlier game's stale prices).

    When `target_date` is None (a desk whose ticker has no embedded time,
    e.g. WNBA — the date isn't known yet at match time), falls back to
    team-names-only matching, same as before this fix; picks the earliest
    date among ties as a best-effort tiebreak.
    """
    vegas_teams = _get_vegas_teams(book_index)
    mapping: dict[str, str] = {}
    for ks in k_names:
        m = match_team(ks, vegas_teams, alias_map=alias_map)
        if m:
            mapping[ks] = m
    if len(mapping) != 2 or len(set(mapping.values())) != 2:
        return None
    matched_teams = set(mapping.values())

    candidates = [
        (home, away, d) for (home, away, d) in book_index
        if {home, away} == matched_teams
    ]
    if not candidates:
        return None
    if target_date is not None:
        exact = [c for c in candidates if c[2] == target_date]
        if exact:
            return exact[0]
        return None  # this desk's game date is known but no odds entry matches it — don't guess
    # No target date available (ticker has no embedded time) — best-effort:
    # prefer the entry with the earliest known date, falling back to any
    # entry with an unknown (None) date.
    candidates.sort(key=lambda c: (c[2] is None, c[2]))
    return candidates[0]


# ── gap computation ───────────────────────────────────────────────────────────

def compute_gap_matrix(
    desk: DeskConfig,
    filter_date: date | None = None,
) -> list[dict]:
    """
    Returns a list of candidate dicts, one per Kalshi game side that has at
    least one book gap >= desk.gap_min. Sorted by best (max abs_gap across books).
    """
    series     = desk.series_ticker
    sport_key  = desk.sport_key
    alias_map  = desk.alias_map
    now        = datetime.now(timezone.utc)

    raw_markets = fetch_kalshi_markets(series)
    book_index  = fetch_all_books(sport_key, desk.books)

    # Group Kalshi by event
    events: dict[str, list[dict]] = {}
    for m in raw_markets:
        events.setdefault(m["event_ticker"], []).append(m)

    candidates: list[dict] = []

    for et, sides in events.items():
        if len(sides) < 2:
            continue

        # Compute Kalshi mids
        for s in sides:
            s["_mid"] = kalshi_mid(s)
        sides = [s for s in sides if s["_mid"] is not None]
        if len(sides) < 2:
            continue

        # Resolve the ticker's own date first when available (MLB always has
        # an embedded time) so team-matching can disambiguate which of
        # potentially several games between the same two teams (any MLB
        # series) this candidate actually is — matching on team names alone
        # silently collapsed every later game in a series onto the first one
        # fetched, regardless of date (2026-07-21 fix).
        k_names        = [s["yes_sub_title"] for s in sides]
        ticker_start   = ticker_to_utc(et)
        target_date    = ticker_start.astimezone(ET).date() if ticker_start else None

        game_key = _match_game(k_names, book_index, alias_map, target_date=target_date)
        if not game_key:
            continue

        home, away, _matched_date = game_key
        game_data    = book_index[game_key]
        book_probs   = game_data["books"]

        start_utc = ticker_start
        if not start_utc:
            # Some desks' Kalshi tickers don't encode a time component at all
            # (confirmed 2026-07-16: WNBA's ticker has no HHMM segment,
            # unlike MLB's) — fall back to the odds-api's own verified
            # start_time for this matched game. Do NOT fall back to Kalshi's
            # occurrence_datetime/expiration fields here — cross-validated
            # against this same odds-api field on a real game and found them
            # ~3h off (closer to game settlement than tip-off).
            raw_start = game_data.get("start_time")
            if raw_start:
                try:
                    start_utc = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    start_utc = None
        if not start_utc:
            continue

        game_date_et = start_utc.astimezone(ET).date()

        # Date filtering — only restrict when an explicit --date is given.
        # --upcoming (filter_date is None) evaluates every open market
        # regardless of date, since Kalshi opens markets 1-3 days ahead and
        # the biggest behavioral mispricings appear in that early window.
        if filter_date is not None and game_date_et != filter_date:
            continue

        hours_until = (start_utc - now).total_seconds() / 3600

        for s in sides:
            sub       = s["yes_sub_title"]
            matched   = match_team(sub, [home, away], alias_map=alias_map)
            if not matched:
                continue
            is_home   = matched == home
            k_prob    = s["_mid"]

            # Gap vs every available book
            gaps_by_book: dict[str, dict] = {}
            for bname, probs in book_probs.items():
                book_vf = probs.get(matched)
                if book_vf is None:
                    continue
                gap = k_prob - book_vf
                gaps_by_book[bname] = {
                    "book_vf":  round(book_vf, 4),
                    "gap":      round(gap, 4),
                    "abs_gap":  round(abs(gap), 4),
                    "book_vig": probs["_vig"],
                    "signal":   "BUY_YES" if gap < 0 else "BUY_NO",
                }

            if not gaps_by_book:
                continue

            # Best gap: largest abs_gap across all books
            best_book    = max(gaps_by_book, key=lambda b: gaps_by_book[b]["abs_gap"])
            best_abs_gap = gaps_by_book[best_book]["abs_gap"]

            # Pinnacle gap for research agent's stability check
            pin_data = gaps_by_book.get("pinnacle", {})
            pin_vf   = pin_data.get("book_vf") or next(
                (v["book_vf"] for v in gaps_by_book.values()), None
            )

            # Book consensus: how many books agree on direction
            if len(gaps_by_book) > 1:
                best_signal = gaps_by_book[best_book]["signal"]
                consensus   = sum(1 for v in gaps_by_book.values()
                                  if v["signal"] == best_signal)
            else:
                consensus = 1

            candidate = {
                "event_ticker":  et,
                "sport":         desk.sport_display_key,
                "game":          f"{away} @ {home}",
                "home_team":     home,
                "away_team":     away,
                "team":          matched,
                "side":          "HOME" if is_home else "AWAY",
                "kalshi_ticker": s["ticker"],
                "k_prob":        round(k_prob, 4),
                "v_prob":        round(pin_vf, 4) if pin_vf else None,
                "start_utc":     start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "game_date_et":  str(game_date_et),
                "hours_until":   round(hours_until, 2),
                "gaps":          gaps_by_book,
                "best_book":     best_book,
                "best_abs_gap":  best_abs_gap,
                "consensus":     consensus,
                "books_checked": len(gaps_by_book),
                "tier":          desk.gap_tier(best_abs_gap),
                # Flattened fields for research_agent compatibility
                "date":          start_utc.astimezone(ET).strftime("%b %-d, %Y"),
                "game_time":     start_utc.astimezone(ET).strftime("%-I:%M %p ET"),
                "gap":           gaps_by_book[best_book]["gap"],
                "signal":        gaps_by_book[best_book]["signal"],
                "hours_before_game": round(hours_until, 2),
                "pinnacle_prob": round(pin_vf, 4) if pin_vf else None,
            }
            candidate["edge_context"] = classify_edge(desk, candidate)
            candidates.append(candidate)

    # Sort: tradeable candidates first (best_abs_gap >= gap_min), then by gap size
    candidates.sort(key=lambda c: c["best_abs_gap"], reverse=True)
    return candidates


# ── logging ───────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line  = f"[{ts}] {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── printing ──────────────────────────────────────────────────────────────────

def print_gap_matrix(candidates: list[dict], sport: str, gap_min: float) -> None:
    above   = [c for c in candidates if c["best_abs_gap"] >= gap_min]
    below   = [c for c in candidates if c["best_abs_gap"] < gap_min]
    n_games = len({c["event_ticker"] for c in candidates})

    print(f"\n{'═'*90}")
    print(f"  EDGE DISCOVERY REPORT — {sport.upper()}")
    print(f"  {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")
    print(f"  {n_games} games scanned  |  {len(above)} side(s) above {gap_min:.0%} threshold")
    print(f"{'═'*90}")

    if above:
        print(f"\n  ★ CANDIDATES (gap ≥ {gap_min:.0%})")
        hdr = f"  {'TEAM':<24} {'GAME':<38} {'K%':>5}  {'PIN':>5}  {'DK':>5}  {'FD':>5}  {'BEST':>7}  {'CONS':>5}  SIGNAL"
        print(hdr)
        print(f"  {'─'*115}")
        for c in above:
            pin_str = f"{c['gaps']['pinnacle']['book_vf']:.1%}" if "pinnacle" in c["gaps"] else "  — "
            dk_str  = f"{c['gaps']['draftkings']['book_vf']:.1%}" if "draftkings" in c["gaps"] else "  — "
            fd_str  = f"{c['gaps']['fanduel']['book_vf']:.1%}" if "fanduel" in c["gaps"] else "  — "
            cons    = f"{c['consensus']}/{c['books_checked']}"
            tier    = f"Tier {c['tier']}"
            print(
                f"  {c['team']:<24} {c['game']:<38}"
                f"  {c['k_prob']:.1%}  {pin_str}  {dk_str}  {fd_str}"
                f"  {c['best_abs_gap']:>+7.1%}  {cons:>5}  {c['signal']} [{c['best_book'].upper()} {tier}]"
            )
    else:
        print(f"\n  No candidates above {gap_min:.0%} threshold today.")

    if below:
        print(f"\n  — Remaining games (gap < {gap_min:.0%})")
        print(f"  {'TEAM':<24} {'GAME':<38} {'K%':>5}  {'PIN':>5}  {'BEST GAP':>9}")
        print(f"  {'─'*85}")
        for c in below[:20]:  # cap at 20 to avoid wall of text
            pin_str = f"{c['gaps']['pinnacle']['book_vf']:.1%}" if "pinnacle" in c["gaps"] else "  — "
            print(
                f"  {c['team']:<24} {c['game']:<38}"
                f"  {c['k_prob']:.1%}  {pin_str}  {c['best_abs_gap']:>+9.1%}"
            )
        if len(below) > 20:
            print(f"  ... and {len(below)-20} more")


# ── edge classification ───────────────────────────────────────────────────────

def classify_edge(desk: DeskConfig, candidate: dict) -> dict:
    """
    Classify the edge type from objective book data only.
    Returns an edge_context dict with research priorities for the research agent.
    """
    gaps          = candidate["gaps"]
    pin_gap       = gaps.get("pinnacle",   {}).get("abs_gap", 0)
    dk_gap        = gaps.get("draftkings", {}).get("abs_gap", 0)
    fd_gap        = gaps.get("fanduel",    {}).get("abs_gap", 0)
    best          = candidate["best_abs_gap"]
    consensus     = candidate["consensus"]
    books_checked = candidate["books_checked"]

    large_gap_warn        = desk.get("thresholds.large_gap_warn", 0.20)
    consensus_min_books   = desk.get("thresholds.consensus_min_books", 3)
    behavioral_gap        = desk.get("thresholds.behavioral_gap", 0.05)
    sharp_signal_gap      = desk.get("thresholds.sharp_signal_gap", 0.07)
    retail_quiet_gap      = desk.get("thresholds.retail_quiet_gap", 0.03)
    retail_soft_gap       = desk.get("thresholds.retail_soft_gap", 0.07)
    consensus_min_partial = desk.get("thresholds.consensus_min_partial", 2)

    # Rule 1: Market anomaly — supersedes all others
    if pin_gap >= large_gap_warn:
        return {
            "edge_type":          "MARKET_ANOMALY",
            "edge_confidence":    "LOW",
            "supporting_evidence": [f"Pinnacle gap {pin_gap:.1%} — abnormally large"],
            "risk_factors":       ["Gap size suggests information asymmetry (injury, late lineup, weather)"],
            "research_priorities": [
                "Search for breaking news on both teams in last 6 hours",
                "Check starting pitcher status explicitly",
                "Look for weather delay or cancellation risk",
                "Verify Pinnacle line has moved (not stale)",
            ],
            "initial_lean": "SKIP_CANDIDATE",
        }

    # Rule 2: All books agree — strongest signal
    if consensus >= consensus_min_books and best >= behavioral_gap:
        return {
            "edge_type":          "MULTI_BOOK_CONSENSUS",
            "edge_confidence":    "HIGH",
            "supporting_evidence": [
                f"All {books_checked} books agree: best gap {best:.1%}",
                f"Pinnacle {pin_gap:.1%}, DK {dk_gap:.1%}, FanDuel {fd_gap:.1%}",
            ],
            "risk_factors":       ["Verify no late-breaking news that would justify Kalshi price"],
            "research_priorities": [
                "Confirm both starting pitchers healthy and confirmed",
                "Check for weather or scheduling changes",
                "Verify no significant news in last 24h explaining the Kalshi price",
            ],
            "initial_lean": "TRADE_CANDIDATE",
        }

    # Rule 3: Pinnacle alone diverges — sharp signal
    if pin_gap >= sharp_signal_gap and dk_gap < retail_quiet_gap and fd_gap < retail_quiet_gap:
        return {
            "edge_type":          "SHARP_SIGNAL",
            "edge_confidence":    "MEDIUM",
            "supporting_evidence": [
                f"Pinnacle gap {pin_gap:.1%} but DK {dk_gap:.1%} / FanDuel {fd_gap:.1%}",
                "Sharp money (Pinnacle) diverges from retail — may have new information",
            ],
            "risk_factors": [
                "Sharp books often move on injury/lineup info before public",
                "Trading against Pinnacle's direction has negative historical EV",
            ],
            "research_priorities": [
                "Why is Pinnacle alone moving? Search for injury, lineup, or trade news",
                "Check if Pinnacle line moved recently (vs yesterday's open)",
                "Look for any sharp betting reports or steam moves on this game",
            ],
            "initial_lean": "SKIP_CANDIDATE",
        }

    # Rule 4: DK/FanDuel diverge but Pinnacle agrees with Kalshi
    if max(dk_gap, fd_gap) >= retail_soft_gap and pin_gap < retail_quiet_gap:
        return {
            "edge_type":          "RETAIL_BOOK_SOFT",
            "edge_confidence":    "LOW",
            "supporting_evidence": [
                f"DK {dk_gap:.1%} / FanDuel {fd_gap:.1%} gap but Pinnacle only {pin_gap:.1%}",
                "Retail books likely stale — Pinnacle and Kalshi agree",
            ],
            "risk_factors":       ["This is a DK/FanDuel internal pricing delay, not a Kalshi edge"],
            "research_priorities": [
                "Verify Pinnacle line is current (not stale)",
                "Check if DK/FanDuel have updated since last refresh",
            ],
            "initial_lean": "MONITOR",
        }

    # Rule 5: Behavioral retail — Pinnacle gap with partial book consensus
    if pin_gap >= behavioral_gap and consensus >= consensus_min_partial:
        return {
            "edge_type":          "BEHAVIORAL_RETAIL",
            "edge_confidence":    "MEDIUM",
            "supporting_evidence": [
                f"Pinnacle gap {pin_gap:.1%}, confirmed by {consensus} books",
                "Kalshi price likely reflects retail crowd narrative bias",
            ],
            "risk_factors":       ["Smaller consensus than MULTI_BOOK — one book may be stale"],
            "research_priorities": [
                "Identify the narrative driving Kalshi price (hot team, star player hype, home crowd)",
                "Confirm starting pitcher / key player status for both sides",
                "Check for any real news that might justify Kalshi's implied probability",
            ],
            "initial_lean": "TRADE_CANDIDATE",
        }

    # Fallback
    return {
        "edge_type":          "BEHAVIORAL_RETAIL",
        "edge_confidence":    "LOW",
        "supporting_evidence": [f"Best gap {best:.1%} across {books_checked} books"],
        "risk_factors":       ["Pattern doesn't fit clean classification — lower confidence"],
        "research_priorities": [
            "Standard news/injury/weather check",
            "Verify book lines are current",
        ],
        "initial_lean": "MONITOR",
    }


# ── main ──────────────────────────────────────────────────────────────────────

def _run_sport(
    desk: DeskConfig,
    filter_date: date | None,
    no_research: bool,
    save: bool,
) -> None:
    """Run edge discovery for a single desk."""
    sport = desk.desk_id
    gap_min = desk.gap_min
    _log(f"Edge discovery started — desk={sport} "
         f"date={filter_date or 'today'} research={'off' if no_research else 'on'}")

    print(f"\nFetching Kalshi markets and book lines for {sport}...", flush=True)
    try:
        candidates = compute_gap_matrix(desk, filter_date)
    except Exception as e:
        _log(f"ERROR fetching {sport} data: {e}")
        return

    print_gap_matrix(candidates, sport, gap_min)

    above = [c for c in candidates if c["best_abs_gap"] >= gap_min]
    verdicts: list[dict] = []

    funnel: dict = {
        "run_at":             datetime.now(timezone.utc).isoformat(),
        "sport":              sport,
        "total_scanned":      len(candidates),
        "above_threshold":    len(above),
        "already_traded":     0,
        "on_cooldown":        0,
        "pre_filter_skipped": 0,
        "researched":         0,
        "trade_verdicts":     0,
        "skip_verdicts":      0,
        "monitor_verdicts":   0,
        "shadow_verdicts":    0,
        "api_cost_usd":       0.0,
    }

    if above and not no_research:
        traded_tickers, cooldown_tickers = _load_evaluated_tickers(desk)

        # Load trade list for pre_filter kalshi_ticker duplicate check
        existing_trades_list: list[dict] = []
        trades_file = _trades_file(desk)
        if os.path.exists(trades_file):
            try:
                with open(trades_file) as f:
                    existing_trades_list = json.load(f)
            except (json.JSONDecodeError, ValueError):
                pass

        try:
            from agent.pre_filter import pre_filter as _pre_filter
        except ImportError:
            from pre_filter import pre_filter as _pre_filter  # type: ignore

        snap_now = datetime.now(timezone.utc)
        pf_skipped_candidates: list[dict] = []
        filtered = []
        for c in above:
            et = c.get("event_ticker", "")
            if et in traded_tickers:
                print(f"  [SKIP] Already traded: {c['game']} — {c['team']}")
                funnel["already_traded"] += 1
            elif et in cooldown_tickers:
                print(f"  [COOLDOWN] Recently evaluated: {c['game']} — {c['team']}")
                funnel["on_cooldown"] += 1
            else:
                pf = _pre_filter(desk, c, existing_trades_list, snap_now)
                if pf["action"] == "SKIP":
                    print(f"  [PRE_FILTER] {c['game']} — {c['team']}: {pf['reason']}")
                    funnel["pre_filter_skipped"] += 1
                    pf_skipped_candidates.append({**c, "_pre_filter_reason": pf["reason"]})
                else:
                    filtered.append(c)
        funnel["researched"] = len(filtered)

        # Log pre-filter skips to skipped_trades.json
        if pf_skipped_candidates:
            skipped_file = _skipped_file(desk)
            pf_skipped_list: list[dict] = []
            if os.path.exists(skipped_file):
                try:
                    with open(skipped_file) as f:
                        pf_skipped_list = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    pass
            pf_snap_str = snap_now.strftime("%Y-%m-%d_%H%M")
            for c in pf_skipped_candidates:
                pf_skipped_list.append({
                    "trade_id":          f"{pf_snap_str}|{c.get('event_ticker', '')}",
                    "event_ticker":      c.get("event_ticker", ""),
                    "kalshi_ticker":     c.get("kalshi_ticker", ""),
                    "game":              c.get("game", ""),
                    "team":              c.get("team", ""),
                    "signal":            c.get("signal", ""),
                    "gap":               c.get("gap"),
                    "abs_gap":           c.get("best_abs_gap"),
                    "start_utc":         c.get("start_utc", ""),
                    "snapshot_time":     snap_now.isoformat(),
                    "hours_before_game": c.get("hours_before_game"),
                    "timing_suspect":    (c.get("hours_before_game") or 0) > 3.0,
                    "skipped_at":        snap_now.isoformat(),
                    "pre_filter_skip":   True,
                    "skip_reason":       c.get("_pre_filter_reason", ""),
                    "agent_reasoning":   None,
                    "news_found":        None,
                    "news_detail":       None,
                    "pinnacle_stable":   None,
                    "pinnacle_movement": None,
                    "weather_issue":     None,
                })
            os.makedirs(os.path.dirname(skipped_file), exist_ok=True)
            with open(skipped_file, "w") as f:
                json.dump(pf_skipped_list, f, indent=2)

        if not filtered:
            print(f"\n  All {len(above)} candidate(s) are in cooldown — no research calls needed.")
        else:
            print(f"\n  Running research agent on {len(filtered)} candidate(s) ({len(above) - len(filtered)} in cooldown)...\n")

        try:
            from agent import research_agent
        except ImportError:
            import research_agent  # type: ignore

        monitor_tickers: list[str] = []
        shadow_tickers: list[str] = []
        for c in filtered:
            ec = c.get("edge_context", {})
            etype = ec.get("edge_type", "UNKNOWN")
            lean  = ec.get("initial_lean", "?")
            print(f"  → {c['game']} — {c['team']} ({c['signal']}, gap={c['best_abs_gap']:.1%} vs {c['best_book']}) [{etype}, lean={lean}]")
            verdict = research_agent.run(desk, c, edge_context=ec or None)
            verdict = apply_signal_gates(desk, c, verdict)
            rec     = verdict.get("recommendation", "MONITOR")
            conf    = verdict.get("confidence", "?")
            reason  = verdict.get("reasoning", "")[:120]
            print(f"     {rec} ({conf}) — {reason}")

            verdicts.append({**c, "research": verdict})
            _log(f"  {c['game']} | {c['team']} | gap={c['best_abs_gap']:.1%} | "
                 f"{rec} ({conf}) via {c['best_book'].upper()}")

            if rec == "TRADE":
                funnel["trade_verdicts"] += 1
            elif rec == "SKIP":
                funnel["skip_verdicts"] += 1
            elif rec == "SHADOW":
                funnel["shadow_verdicts"] += 1
                shadow_tickers.append(c.get("event_ticker", ""))
            else:
                funnel["monitor_verdicts"] += 1
                monitor_tickers.append(c.get("event_ticker", ""))

        if monitor_tickers:
            _update_monitor_cache(desk, monitor_tickers)
        if shadow_tickers:
            _update_monitor_cache(desk, shadow_tickers)  # prevents re-researching an already-shadowed candidate

        trades  = [v for v in verdicts if v["research"].get("recommendation") == "TRADE"]
        skips   = [v for v in verdicts if v["research"].get("recommendation") == "SKIP"]
        shadows = [v for v in verdicts if v["research"].get("recommendation") == "SHADOW"]
        if trades:
            print(f"\n  ★ TRADE SIGNALS ({len(trades)}):")
            for t in trades:
                print(f"    {t['game']} — {t['team']} | {t['signal']} "
                      f"| gap={t['best_abs_gap']:.1%} ({t['best_book'].upper()}) "
                      f"| Tier {t['research'].get('tier', t.get('tier'))} | {t['research']['confidence']}")
        else:
            print(f"\n  No TRADE signals after research.")
        if shadows:
            print(f"\n  ○ SHADOW SIGNALS ({len(shadows)}) — logged to shadow_trades.json, not traded:")
            for s in shadows:
                print(f"    {s['game']} — {s['team']} | {s['signal']} "
                      f"| gap={s['best_abs_gap']:.1%} | {s['research'].get('shadow_reason','')}")

        snap_time = snap_now.strftime("%Y-%m-%d_%H%M")
        _log_edge_trades(desk, trades, skips, snap_time, shadow_signals=shadows)
        funnel["api_cost_usd"] = _sum_last_n_costs(desk, funnel["researched"])

    elif above:
        print(f"\n  [--no-research] Skipping research agent.")
        verdicts = candidates

    if save:
        out_dir = os.path.join(BASE, "outputs")
        os.makedirs(out_dir, exist_ok=True)
        fname = os.path.join(out_dir, f"edge_discovery_{sport.lower()}_{filter_date or date.today()}.json")
        with open(fname, "w") as f:
            json.dump({
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sport":        sport,
                "candidates":   candidates,
                "verdicts":     verdicts,
            }, f, indent=2, default=str)
        print(f"\n  Saved to {fname}")

    trade_ct = len([v for v in verdicts if isinstance(v, dict) and v.get("research", {}).get("recommendation") == "TRADE"])
    _log(f"Edge discovery complete [{sport}] — {len(above)} candidate(s), {trade_ct} TRADE signal(s)")
    _append_funnel_entry(desk, funnel)


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-book Kalshi edge scanner")
    parser.add_argument("--desk",        default="MLB",
                        help="Desk to scan (e.g. MLB, WNBA). See desks/*.yaml.")
    parser.add_argument("--all-desks",   action="store_true",
                        help="Scan all ACTIVE desks. Overrides --desk.")
    parser.add_argument("--date",        default=None,
                        help="Scan games on this date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--upcoming",    action="store_true",
                        help="Scan all of today's open games (same as omitting --date).")
    parser.add_argument("--no-research", action="store_true",
                        help="Print gap matrix only; do not call research agent.")
    parser.add_argument("--save",        action="store_true",
                        help="Save results to outputs/edge_discovery_<desk>_YYYY-MM-DD.json")
    args = parser.parse_args()

    filter_date = None
    if args.date:
        filter_date = date.fromisoformat(args.date)

    desks_to_scan = get_active_desks() if args.all_desks else [get_desk(args.desk)]

    for desk in desks_to_scan:
        if desk.is_pending:
            print(f"Desk {desk.desk_id} is PENDING — skipping")
            continue
        if not desk.is_active:
            print(f"Desk {desk.desk_id} is not ACTIVE — skipping")
            continue
        _run_sport(desk, filter_date, args.no_research, args.save)


if __name__ == "__main__":
    main()
