"""
update_outcomes.py

Runs nightly (2 AM via LaunchAgent). Resolves all open paper trades by
checking settled Kalshi market results, then regenerates the performance
summary with win rates, gap analysis, and statistical significance.

Flow:
  1. Scan all snapshot files → extract fav_flag=True trades into paper_trades.json
  2. For each unresolved trade where game started >4 hours ago:
       GET /markets/{kalshi_ticker} → check result (yes/no)
  3. Update paper_trades.json with outcome, correct, resolution_price, resolved_at
  4. Regenerate data/performance_summary.json
  5. Print clean summary

Usage:
    python scripts/update_outcomes.py
    python scripts/update_outcomes.py --dry-run    # resolve but don't save
    python scripts/update_outcomes.py --force-all  # retry already-resolved trades
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from dotenv import load_dotenv
from scipy.stats import binomtest

load_dotenv()

from core.utils import ticker_to_utc
from core.notifications import send_imessage

try:
    from agent.research_agent import run as _agent_run, TIER_B_MIN_GAP, TIER_C_MIN_GAP
    _AGENT_AVAILABLE = True
except Exception:
    _AGENT_AVAILABLE = False
    TIER_B_MIN_GAP = 0.10
    TIER_C_MIN_GAP = 0.15


def _gap_tier(abs_gap: float) -> str:
    """A = 5-10%, B = 10-15%, C = 15%+."""
    if abs_gap >= TIER_C_MIN_GAP:
        return "C"
    if abs_gap >= TIER_B_MIN_GAP:
        return "B"
    return "A"

KALSHI_BASE    = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
SNAPSHOT_DIR   = "data/snapshots"
TRADES_FILE    = "data/paper_trades.json"
SUMMARY_FILE   = "data/performance_summary.json"
SKIPPED_FILE   = "data/skipped_trades.json"
MONITOR_CACHE  = "data/monitor_cache.json"
MONITOR_COOLDOWN_HOURS = 3.0
RESOLVE_AFTER  = 0.5   # hours after game start before we attempt resolution (Kalshi handles finalization)


# ── paper trades I/O ──────────────────────────────────────────────────────────

def load_trades() -> list[dict]:
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE) as f:
            return json.load(f)
    return []


def save_trades(trades: list[dict], dry_run: bool = False) -> None:
    if dry_run:
        return
    os.makedirs(os.path.dirname(TRADES_FILE), exist_ok=True)
    with open(TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2)


GAP_THRESHOLD    = 0.05
SPREAD_THRESHOLD = 0.06  # max acceptable Kalshi bid-ask spread; wider = illiquid, skip


# ── agent helpers ─────────────────────────────────────────────────────────────

def _build_agent_game_dict(trade: dict) -> dict:
    game  = trade.get("game", "")
    parts = game.split(" @ ")
    away  = parts[0].strip() if len(parts) == 2 else ""
    home  = parts[1].strip() if len(parts) == 2 else ""
    abs_gap = trade.get("abs_gap") or abs(trade.get("gap", 0))
    tier    = _gap_tier(abs_gap)
    start   = trade.get("start_utc", "")
    return {
        "home_team":         home,
        "away_team":         away,
        "team":              trade.get("team", ""),
        "side":              trade.get("side", "HOME"),
        "game_time":         start,
        "sport":             trade.get("sport", "MLB"),
        "kalshi_prob":       trade.get("k_prob", 0),
        "pinnacle_prob":     trade.get("v_prob", 0),
        "gap":               trade.get("gap", 0),
        "abs_gap":           abs_gap,
        "gap_direction":     "kalshi_lower" if (trade.get("gap", 0) < 0) else "kalshi_higher",
        "tier":              tier,
        "signal":            trade.get("signal", ""),
        "snapshot_time":     trade.get("snapshot_time", ""),
        "date":              start[:10] if start else "",
        "hours_before_game": trade.get("hours_before_game"),
        "timing_suspect":    trade.get("timing_suspect", False),
    }


def _agent_fields(verdict: dict) -> dict:
    return {
        "agent_verdict":     verdict.get("recommendation"),
        "agent_confidence":  verdict.get("confidence"),
        "agent_reasoning":   verdict.get("reasoning"),
        "gap_explanation":   verdict.get("gap_explanation"),
        "gap_type":          verdict.get("gap_type"),
        "news_found":        verdict.get("news_found"),
        "news_detail":       verdict.get("news_detail"),
        "news_source":       verdict.get("news_source"),
        "pitcher_confirmed": verdict.get("pitcher_confirmed"),
        "weather_issue":     verdict.get("weather_issue"),
        "pinnacle_stable":   verdict.get("pinnacle_stable"),
        "pinnacle_movement": verdict.get("pinnacle_movement"),
    }


def _format_trade_entry_message(trade: dict) -> str:
    abs_gap = trade.get("abs_gap") or abs(trade.get("gap", 0))
    tier    = _gap_tier(abs_gap)
    k_prob  = trade.get("k_prob") or 0
    v_prob  = trade.get("v_prob") or 0
    gap     = trade.get("gap") or 0
    verdict = trade.get("agent_verdict") or "—"
    conf    = trade.get("agent_confidence") or "—"
    return (
        f"\U0001F7E2 TRADE LOGGED\n"
        f"{trade.get('game', '')}\n"
        f"{trade.get('signal', '')} {trade.get('team', '')}  |  Tier {tier}\n"
        f"Kalshi {k_prob:.1%}  vs  Pinnacle {v_prob:.1%}   (gap {gap:+.1%})\n"
        f"Agent: {verdict} ({conf})"
    )


def _append_skipped(trade: dict, verdict: dict) -> None:
    """Log a SKIP decision to skipped_trades.json (never appears in paper_trades)."""
    record = {
        k: trade.get(k)
        for k in ("trade_id", "event_ticker", "kalshi_ticker", "game", "team", "signal", "gap", "abs_gap",
                  "start_utc", "snapshot_time", "hours_before_game", "timing_suspect")
    }
    record.update({
        "skipped_at":        datetime.now(timezone.utc).isoformat(),
        "agent_reasoning":   verdict.get("reasoning"),
        "news_found":        verdict.get("news_found"),
        "news_detail":       verdict.get("news_detail"),
        "pinnacle_stable":   verdict.get("pinnacle_stable"),
        "pinnacle_movement": verdict.get("pinnacle_movement"),
        "weather_issue":     verdict.get("weather_issue"),
    })
    existing: list[dict] = []
    if os.path.exists(SKIPPED_FILE):
        try:
            with open(SKIPPED_FILE) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    existing.append(record)
    os.makedirs(os.path.dirname(SKIPPED_FILE), exist_ok=True)
    with open(SKIPPED_FILE, "w") as f:
        json.dump(existing, f, indent=2)


def ingest_new_trades(existing: list[dict]) -> tuple[list[dict], int, int, int]:
    """
    Scan all snapshot files chronologically. For each game (event_ticker):
      - Log BUY_YES rows (Kalshi underprices) AND BUY_NO rows (Kalshi overprices)
      - Only if the snapshot was taken BEFORE game start (pre-game price)
      - If a cleaner snapshot (≤3h) arrives for an already-logged suspect trade,
        replace the prices/timing while preserving any resolved outcome
    Returns (updated_list, new_count, replaced_count).
    """
    # Index by event_ticker for O(1) replacement lookups
    event_index: dict[str, int] = {t["event_ticker"]: i for i, t in enumerate(existing)}
    new_count = 0
    replaced_count = 0
    skipped_count = 0

    # Load previously-skipped tickers so we don't re-evaluate the same game on every run
    previously_skipped: set[str] = set()
    try:
        with open(SKIPPED_FILE) as f:
            for entry in json.load(f):
                et = entry.get("event_ticker", "")
                if not et:
                    tid = entry.get("trade_id", "")
                    et = tid.split("|", 1)[1] if "|" in tid else ""
                if et:
                    previously_skipped.add(et)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Load MONITORed tickers within cooldown window — don't re-research for 3h
    monitored_cooldown: set[str] = set()
    try:
        from datetime import timezone as _tz
        _now = datetime.now(_tz.utc)
        with open(MONITOR_CACHE) as f:
            for et, ts in json.load(f).items():
                try:
                    age_h = (_now - datetime.fromisoformat(ts)).total_seconds() / 3600
                    if age_h < MONITOR_COOLDOWN_HOURS:
                        monitored_cooldown.add(et)
                except Exception:
                    monitored_cooldown.add(et)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    this_run_skipped:    set[str]  = set()
    this_run_monitored:  list[str] = []

    for snap_file in sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "*.json"))):
        if os.path.basename(snap_file) in ("master_log.json", "missed_snapshots.json"):
            continue
        try:
            with open(snap_file) as f:
                snap = json.load(f)
        except (json.JSONDecodeError, KeyError):
            continue

        snap_time_str = snap.get("snapshot_time", "")
        try:
            snap_dt = datetime.strptime(snap_time_str, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        for row in snap.get("rows", []):
            # Accept BUY_YES (Kalshi underprices) and BUY_NO (Kalshi overprices)
            gap = row.get("gap", 0)
            if abs(gap) < GAP_THRESHOLD:
                continue

            event_ticker = row.get("event_ticker", "")

            # Derive accurate game start from ticker (Kalshi occurrence_datetime
            # has a known 3-hour UTC/ET error; the ticker encodes ET time correctly)
            start_dt = ticker_to_utc(event_ticker)
            if start_dt is None:
                # Fallback: use stored start_utc if ticker can't be parsed
                start_str = row.get("start_utc", "")
                try:
                    start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue

            start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            if snap_dt >= start_dt:
                continue  # snapshot taken after game started — in-game price, skip

            hours_before   = (start_dt - snap_dt).total_seconds() / 3600
            timing_suspect = hours_before > 3.0

            new_signal = row.get("signal") or ("BUY_YES" if gap < 0 else "BUY_NO")

            k_bid  = row.get("k_bid")
            k_ask  = row.get("k_ask")
            spread = round(k_ask - k_bid, 3) if (k_bid is not None and k_ask is not None) else None

            # Skip wide-spread markets — mid-price is unreliable when spread > threshold
            if spread is not None and spread > SPREAD_THRESHOLD:
                continue

            trade = {
                "trade_id":           f"{snap_time_str}|{event_ticker}",
                "snapshot_time":      snap_time_str,
                "snapshot_file":      os.path.basename(snap_file),
                "sport":              row.get("sport", ""),
                "game":               row.get("game", ""),
                "team":               row.get("team", ""),
                "side":               row.get("side", ""),
                "start_utc":          start_str,
                "kalshi_ticker":      row.get("kalshi_ticker", ""),
                "event_ticker":       event_ticker,
                "k_prob":             row.get("k_prob"),
                "k_bid":              k_bid,
                "k_ask":              k_ask,
                "spread":             spread,
                "v_prob":             row.get("v_prob"),
                "gap":                row.get("gap"),
                "abs_gap":            row.get("abs_gap"),
                "signal":             new_signal,
                "book":               "pinnacle",
                "hours_before_game":  round(hours_before, 2),
                "timing_suspect":     timing_suspect,
                "valid_for_analysis": True,
                "replacement_flags":  [],
                "outcome":            None,
                "correct":            None,
                "resolution_price":   None,
                "resolved_at":        None,
            }

            if event_ticker not in event_index:
                # Skip agent call if already confidently rejected or in cooldown
                if event_ticker in previously_skipped or event_ticker in this_run_skipped:
                    skipped_count += 1
                    continue
                if event_ticker in monitored_cooldown:
                    print(f"  [MONITOR COOLDOWN] {trade['game']} — re-evaluation in <{MONITOR_COOLDOWN_HOURS:.0f}h")
                    continue

                # Reject impossible Pinnacle lines before hitting the agent — a v_prob
                # below 20% in a regular season MLB/WNBA game is a data-matching error,
                # not a real edge.
                v_prob = trade.get("v_prob") or 0
                if v_prob < 0.20:
                    print(f"  [DATA ERROR] {trade['game']} v_prob={v_prob:.1%} — implausible Pinnacle line, skipping")
                    continue

                # Run agent before logging — it is the barrier between detection and execution
                if _AGENT_AVAILABLE:
                    try:
                        verdict = _agent_run(_build_agent_game_dict(trade))
                    except Exception as _agent_err:
                        print(f"  [AGENT ERROR] {trade['game']}: {_agent_err} — not logging")
                        continue  # API failure → do not log, do not guess
                    rec = verdict.get("recommendation", "MONITOR")
                    trade.update(_agent_fields(verdict))
                    print(f"  [AGENT] {trade['game']} → {rec} ({verdict.get('confidence','?')})")
                    if rec == "SKIP":
                        _append_skipped(trade, verdict)
                        skipped_count += 1
                        this_run_skipped.add(event_ticker)
                        continue  # Do not log to paper_trades
                    elif rec != "TRADE":
                        # MONITOR or any unrecognised verdict — hold, cache to suppress reruns
                        print(f"  [MONITOR] {trade['game']} — held for re-evaluation, not logged")
                        this_run_monitored.append(event_ticker)
                        continue
                else:
                    trade["agent_verdict"] = None

                existing.append(trade)
                event_index[event_ticker] = len(existing) - 1
                new_count += 1

                # Open a sandbox position for June 25+ trades
                try:
                    from execution.position_manager import open_sandbox_position
                    open_sandbox_position(trade)
                except Exception as _sb_err:
                    print(f"  [SANDBOX] open_sandbox_position skipped: {_sb_err}")

                try:
                    send_imessage(_format_trade_entry_message(trade))
                except Exception as _notify_err:
                    print(f"  [NOTIFY] trade-entry notification skipped: {_notify_err}")

            elif not timing_suspect and existing[event_index[event_ticker]].get("timing_suspect"):
                # Cleaner snapshot arrived — validate before replacing
                old = existing[event_index[event_ticker]]

                flags: list[str] = []
                if old.get("signal") != new_signal:
                    flags.append("SIGNAL_FLIP")
                if (row.get("abs_gap") or 0) < GAP_THRESHOLD:
                    flags.append("BELOW_THRESHOLD")
                if old.get("book") != trade["book"]:
                    flags.append("BOOK_CHANGED")

                trade["replacement_flags"]  = flags
                trade["valid_for_analysis"] = not any(f in flags for f in ("SIGNAL_FLIP", "BELOW_THRESHOLD"))

                # Preserve any resolved outcome
                trade["outcome"]          = old.get("outcome")
                trade["correct"]          = old.get("correct")
                trade["resolution_price"] = old.get("resolution_price")
                trade["resolved_at"]      = old.get("resolved_at")

                # Re-run agent with clean snapshot data if trade is still open
                if _AGENT_AVAILABLE and old.get("outcome") is None:
                    try:
                        verdict = _agent_run(_build_agent_game_dict(trade))
                    except Exception as _agent_err:
                        print(f"  [AGENT re-eval ERROR] {trade['game']}: {_agent_err} — keeping old verdict")
                        verdict = None
                    if verdict:
                        trade.update(_agent_fields(verdict))
                        print(f"  [AGENT re-eval] {trade['game']} → {verdict.get('recommendation','?')}")

                existing[event_index[event_ticker]] = trade
                replaced_count += 1
            # else: already have a clean trade for this game — skip

    # Persist MONITOR cooldowns so the next run doesn't re-research the same games
    if this_run_monitored:
        cache: dict[str, str] = {}
        try:
            with open(MONITOR_CACHE) as f:
                cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        now_iso = datetime.now(timezone.utc).isoformat()
        for et in this_run_monitored:
            cache[et] = now_iso
        os.makedirs(os.path.dirname(os.path.abspath(MONITOR_CACHE)), exist_ok=True)
        with open(MONITOR_CACHE, "w") as f:
            json.dump(cache, f, indent=2)

    return existing, new_count, replaced_count, skipped_count


# ── timing backfill ──────────────────────────────────────────────────────────

def backfill_timing(trades: list[dict]) -> int:
    """Add hours_before_game / timing_suspect / valid_for_analysis to older trades."""
    updated = 0
    for t in trades:
        changed = False
        if "hours_before_game" not in t:
            try:
                snap_dt  = datetime.strptime(t["snapshot_time"], "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
                start_dt = datetime.fromisoformat(t["start_utc"].replace("Z", "+00:00"))
                hours    = (start_dt - snap_dt).total_seconds() / 3600
                t["hours_before_game"] = round(hours, 2)
                t["timing_suspect"]    = hours > 3.0
                changed = True
            except (ValueError, KeyError, AttributeError):
                pass
        # Default — trades that were never replaced are clean by definition
        if "valid_for_analysis" not in t:
            t["valid_for_analysis"] = True
            changed = True
        if "replacement_flags" not in t:
            t["replacement_flags"] = []
            changed = True
        if changed:
            updated += 1
    return updated


# ── Kalshi resolution ─────────────────────────────────────────────────────────

def fetch_kalshi_result(ticker: str) -> dict | None:
    """
    Returns {"status", "result", "settlement_ts"} or None on error.
    result = "yes"  → team won (BET_YES is a WIN)
    result = "no"   → team lost (BET_YES is a LOSS)
    """
    try:
        resp = requests.get(
            f"{KALSHI_BASE}/markets/{ticker}",
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        m = resp.json().get("market", {})
        return {
            "status":         m.get("status"),
            "result":         m.get("result"),
            "settlement_ts":  m.get("settlement_ts"),
        }
    except Exception:
        return None


def should_resolve(trade: dict) -> bool:
    """True if trade is open and game started more than RESOLVE_AFTER hours ago."""
    if trade.get("outcome") is not None:
        return False
    start = trade.get("start_utc", "")
    if not start:
        return False
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= start_dt + timedelta(hours=RESOLVE_AFTER)
    except ValueError:
        return False


# ── resolve loop ──────────────────────────────────────────────────────────────

def resolve_trades(trades: list[dict], dry_run: bool = False) -> tuple[int, int]:
    """
    Attempt resolution for all eligible open trades.
    Returns (resolved_count, still_open_count).
    """
    to_resolve = [t for t in trades if should_resolve(t)]
    resolved = 0
    still_open = 0

    for trade in to_resolve:
        kalshi = fetch_kalshi_result(trade["kalshi_ticker"])
        time.sleep(0.15)

        if kalshi is None or kalshi["status"] != "finalized":
            still_open += 1
            continue

        kalshi_yes = (kalshi["result"] == "yes")
        # BUY_YES wins when the team wins; BUY_NO wins when the team loses
        won = kalshi_yes if trade.get("signal") != "BUY_NO" else not kalshi_yes

        if not dry_run:
            trade["outcome"]           = "WIN" if won else "LOSS"
            trade["correct"]           = won
            trade["resolution_price"]  = 1.00 if won else 0.00
            trade["resolved_at"]       = kalshi["settlement_ts"]

        resolved += 1

    return resolved, still_open


def resolve_skipped_trades() -> int:
    """
    For each skipped trade that has kalshi_ticker and no shadow_outcome yet,
    fetch the Kalshi result and record what would have happened.
    Returns count of shadow outcomes written.
    """
    if not os.path.exists(SKIPPED_FILE):
        return 0
    try:
        with open(SKIPPED_FILE) as f:
            skipped = json.load(f)
    except (json.JSONDecodeError, ValueError):
        return 0

    resolved = 0
    for entry in skipped:
        if entry.get("shadow_outcome"):
            continue
        ticker = entry.get("kalshi_ticker", "")
        if not ticker:
            continue
        start = entry.get("start_utc", "")
        if not start:
            continue
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < start_dt + timedelta(hours=RESOLVE_AFTER):
                continue
        except ValueError:
            continue

        kalshi = fetch_kalshi_result(ticker)
        time.sleep(0.15)
        if kalshi is None or kalshi["status"] != "finalized":
            continue

        kalshi_yes = (kalshi["result"] == "yes")
        signal     = entry.get("signal", "BUY_YES")
        won        = kalshi_yes if signal != "BUY_NO" else not kalshi_yes

        entry["shadow_outcome"]     = "WIN" if won else "LOSS"
        entry["shadow_correct"]     = won
        entry["shadow_resolved_at"] = kalshi["settlement_ts"]
        resolved += 1

    if resolved:
        with open(SKIPPED_FILE, "w") as f:
            json.dump(skipped, f, indent=2)
        print(f"  [SHADOW] Resolved {resolved} skipped trade(s) — shadow outcomes written.")

    return resolved


# ── performance summary ───────────────────────────────────────────────────────

def _build_agent_stats(trades: list[dict]) -> dict:
    """Compute agent performance stats across logged + skipped trades."""
    skipped: list[dict] = []
    if os.path.exists(SKIPPED_FILE):
        try:
            with open(SKIPPED_FILE) as f:
                skipped = json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass

    evaluated    = [t for t in trades if t.get("agent_verdict") is not None]
    trade_rec    = [t for t in evaluated if t["agent_verdict"] == "TRADE"]
    monitor_rec  = [t for t in evaluated if t["agent_verdict"] == "MONITOR"]
    skip_count   = len(skipped)
    total_eval   = len(evaluated) + skip_count

    skip_rate    = round(skip_count / total_eval, 4) if total_eval else None

    # Win rate for agent-vetted trades only
    vetted_res   = [t for t in evaluated if t.get("outcome") is not None]
    vetted_wins  = [t for t in vetted_res if t["outcome"] == "WIN"]
    wr_after     = round(len(vetted_wins) / len(vetted_res), 4) if vetted_res else None

    # Win rate for trades without agent vetting (logged before agent existed)
    unvetted_res = [t for t in trades if t.get("agent_verdict") is None and t.get("outcome") is not None]
    unvetted_wins = [t for t in unvetted_res if t["outcome"] == "WIN"]
    wr_without   = round(len(unvetted_wins) / len(unvetted_res), 4) if unvetted_res else None

    # News found rate + Pinnacle unstable rate (across evaluated trades + skipped)
    all_evals    = evaluated + skipped
    news_found   = sum(1 for t in all_evals if t.get("news_found"))
    pin_unstable = sum(1 for t in all_evals if t.get("pinnacle_stable") is False)
    nf_rate      = round(news_found  / total_eval, 4) if total_eval else None
    pu_rate      = round(pin_unstable / total_eval, 4) if total_eval else None

    # Confidence-tier win rates
    high_res  = [t for t in vetted_res if t.get("agent_confidence") == "HIGH"]
    med_res   = [t for t in vetted_res if t.get("agent_confidence") == "MEDIUM"]
    wr_high   = round(sum(1 for t in high_res if t["outcome"] == "WIN") / len(high_res), 4) if high_res else None
    wr_med    = round(sum(1 for t in med_res  if t["outcome"] == "WIN") / len(med_res),  4) if med_res  else None

    # Shadow portfolio — what would have happened if we had taken the skipped trades
    shadow_resolved = [s for s in skipped if s.get("shadow_outcome")]
    shadow_wins     = [s for s in shadow_resolved if s["shadow_outcome"] == "WIN"]
    shadow_wr       = round(len(shadow_wins) / len(shadow_resolved), 4) if shadow_resolved else None

    return {
        "total_evaluated":          total_eval,
        "trade_recommendations":    len(trade_rec),
        "monitor_recommendations":  len(monitor_rec),
        "skip_recommendations":     skip_count,
        "skip_rate":                skip_rate,
        "win_rate_after_agent":     wr_after,
        "win_rate_without_agent":   wr_without,
        "news_found_rate":          nf_rate,
        "pinnacle_unstable_rate":   pu_rate,
        "high_confidence_win_rate": wr_high,
        "medium_confidence_win_rate": wr_med,
        "shadow_resolved":          len(shadow_resolved),
        "shadow_win_rate":          shadow_wr,
    }


def _build_portfolio_metrics(trades: list[dict]) -> dict:
    """Compute EV per trade (paper trades) and Sharpe/drawdown (sandbox DB)."""
    import sqlite3 as _sqlite3
    import statistics as _stats

    # ── Expected value from paper trades ─────────────────────────────────────
    valid_res = [
        t for t in trades
        if t.get("valid_for_analysis", True)
        and t.get("outcome") is not None
        and t.get("k_prob") is not None
    ]
    ev_values: list[float] = []
    for t in valid_res:
        k = t["k_prob"]
        win_rate = 1.0 if t["outcome"] == "WIN" else 0.0
        if k <= 0 or k >= 1:
            continue
        payout_ratio = (1.0 - k) / k
        ev = win_rate * payout_ratio - (1.0 - win_rate)
        ev_values.append(ev)

    avg_ev = round(sum(ev_values) / len(ev_values), 4) if ev_values else None

    # EV by gap bucket
    ev_by_bucket: dict[str, dict] = {}
    for t in valid_res:
        k = t.get("k_prob")
        if k is None or k <= 0 or k >= 1:
            continue
        b   = gap_bucket(t.get("abs_gap", 0))
        wr  = 1.0 if t["outcome"] == "WIN" else 0.0
        ev  = wr * (1 - k) / k - (1 - wr)
        ev_by_bucket.setdefault(b, []).append(ev)
    ev_by_bucket_avg = {b: round(sum(v) / len(v), 4) for b, v in ev_by_bucket.items()}

    # ── Sandbox metrics from SQLite ───────────────────────────────────────────
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "paper_trades.db")
    sharpe = max_drawdown = total_return_pct = None
    n_closed = 0

    try:
        conn = _sqlite3.connect(db_path)
        conn.row_factory = _sqlite3.Row

        closed = conn.execute(
            "SELECT pnl_pct, actual_cost FROM sandbox_trades WHERE status='CLOSED' ORDER BY exit_time"
        ).fetchall()
        n_closed = len(closed)

        if n_closed >= 2:
            returns = [r["pnl_pct"] for r in closed if r["pnl_pct"] is not None]
            if len(returns) >= 2:
                mean_r = sum(returns) / len(returns)
                std_r  = _stats.stdev(returns)
                sharpe = round(mean_r / std_r, 3) if std_r > 0 else None

        # Max drawdown from bankroll history
        history = conn.execute(
            "SELECT bankroll FROM sandbox_bankroll_history ORDER BY timestamp"
        ).fetchall()
        if history:
            bankrolls   = [r["bankroll"] for r in history]
            peak        = bankrolls[0]
            max_dd      = 0.0
            for b in bankrolls:
                peak = max(peak, b)
                dd   = (peak - b) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
            max_drawdown = round(max_dd, 4)

        # Total return
        cfg = conn.execute("SELECT bankroll_start FROM sandbox_config WHERE id=1").fetchone()
        if cfg:
            latest = conn.execute(
                "SELECT bankroll FROM sandbox_bankroll_history ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if latest:
                total_return_pct = round((latest["bankroll"] - cfg["bankroll_start"]) / cfg["bankroll_start"], 4)

        conn.close()
    except Exception:
        pass

    return {
        "avg_ev_per_trade":   avg_ev,
        "ev_by_gap_bucket":   ev_by_bucket_avg,
        "sandbox_sharpe":     sharpe,
        "sandbox_max_drawdown": max_drawdown,
        "sandbox_total_return_pct": total_return_pct,
        "sandbox_closed_trades": n_closed,
    }


def gap_bucket(abs_gap: float) -> str:
    if abs_gap < 0.07:  return "5_7"
    if abs_gap < 0.10:  return "7_10"
    if abs_gap < 0.15:  return "10_15"
    return "15_plus"


def build_summary(trades: list[dict]) -> dict:
    resolved     = [t for t in trades if t.get("outcome") is not None]
    open_        = [t for t in trades if t.get("outcome") is None]
    wins         = [t for t in resolved if t["outcome"] == "WIN"]

    # Primary: only trades where the clean 2h signal agreed with original signal
    valid        = [t for t in resolved if t.get("valid_for_analysis", True)]
    valid_wins   = [t for t in valid if t["outcome"] == "WIN"]
    excluded     = [t for t in resolved if not t.get("valid_for_analysis", True)]

    win_rate     = len(valid_wins) / len(valid) if valid else None

    # Binomial test vs 50% null hypothesis (primary / valid only)
    p_value = None
    if len(valid) >= 5:
        result  = binomtest(len(valid_wins), len(valid), 0.5, alternative="greater")
        p_value = round(result.pvalue, 4)

    # Tier split: A = 5-10%, B = 10-15%, C = 15%+ (higher conviction)
    tier_a = [t for t in resolved if 0.05 <= (t.get("abs_gap") or 0) < 0.10]
    tier_b = [t for t in resolved if 0.10 <= (t.get("abs_gap") or 0) < 0.15]
    tier_c = [t for t in resolved if (t.get("abs_gap") or 0) >= 0.15]
    wr_ta = sum(1 for t in tier_a if t["outcome"] == "WIN") / len(tier_a) if tier_a else None
    wr_tb = sum(1 for t in tier_b if t["outcome"] == "WIN") / len(tier_b) if tier_b else None
    wr_tc = sum(1 for t in tier_c if t["outcome"] == "WIN") / len(tier_c) if tier_c else None

    # Avg gap — winners vs losers
    avg_gap_w = sum(t.get("abs_gap", 0) for t in resolved if t["outcome"] == "WIN") / len(wins) if wins else None
    avg_gap_l = sum(t.get("abs_gap", 0) for t in resolved if t["outcome"] == "LOSS") / max(len(resolved) - len(wins), 1) if resolved else None

    # By gap bucket
    buckets: dict[str, dict] = {}
    for t in resolved:
        b = gap_bucket(t.get("abs_gap", 0))
        buckets.setdefault(b, {"trades": 0, "wins": 0})
        buckets[b]["trades"] += 1
        if t["outcome"] == "WIN":
            buckets[b]["wins"] += 1
    by_bucket = {
        b: {"trades": v["trades"], "win_rate": round(v["wins"] / v["trades"], 4)}
        for b, v in buckets.items()
    }

    # By book
    by_book: dict[str, dict] = {}
    for t in resolved:
        book = t.get("book", "unknown")
        by_book.setdefault(book, {"trades": 0, "wins": 0})
        by_book[book]["trades"] += 1
        if t["outcome"] == "WIN":
            by_book[book]["wins"] += 1
    by_book_out = {
        b: {"trades": v["trades"], "win_rate": round(v["wins"] / v["trades"], 4)}
        for b, v in by_book.items()
    }

    # Clean vs suspect buckets — split on hours_before_game
    def _bucket(subset: list[dict]) -> dict:
        res  = [t for t in subset if t.get("outcome") is not None]
        opn  = [t for t in subset if t.get("outcome") is None]
        w    = [t for t in res if t["outcome"] == "WIN"]
        wr   = round(len(w) / len(res), 4) if res else None
        pv   = None
        if len(res) >= 5:
            pv = round(binomtest(len(w), len(res), 0.5, alternative="greater").pvalue, 4)
        return {
            "resolved": len(res),
            "wins":     len(w),
            "losses":   len(res) - len(w),
            "win_rate": wr,
            "open":     len(opn),
            "p_value":  pv,
        }

    clean_trades   = [t for t in trades if not t.get("timing_suspect", False)
                      and t.get("valid_for_analysis", True)]
    suspect_trades = [t for t in trades if t.get("timing_suspect", False)]

    return {
        # ── top-level counts ──────────────────────────────────────────────
        "total_logged":        len(trades),
        "total_resolved":      len(resolved),
        "total_valid":         len(valid),
        "total_valid_wins":    len(valid_wins),
        "total_excluded":      len(excluded),
        "total_open":          len(open_),

        # ── primary metric: clean timing only ────────────────────────────
        "primary_metric":      "clean_trades",
        "clean_trades":        _bucket(clean_trades),
        "suspect_trades":      _bucket(suspect_trades),

        # ── breakdown (primary / valid trades only) ───────────────────────
        "win_rate_overall":    round(win_rate, 4) if win_rate is not None else None,
        "win_rate_tier_a":     round(wr_ta, 4) if wr_ta is not None else None,
        "win_rate_tier_b":     round(wr_tb, 4) if wr_tb is not None else None,
        "win_rate_tier_c":     round(wr_tc, 4) if wr_tc is not None else None,
        "avg_gap_winners":     round(avg_gap_w, 4) if avg_gap_w is not None else None,
        "avg_gap_losers":      round(avg_gap_l, 4) if avg_gap_l is not None else None,
        "p_value":             p_value,
        "by_gap_bucket":       by_bucket,
        "by_book":             by_book_out,

        # ── excluded (signal flipped or gap closed at 2h mark) ────────────
        "excluded_trades":     [
            {
                "team":              t["team"],
                "game":              t["game"],
                "signal":            t["signal"],
                "gap":               t["gap"],
                "outcome":           t["outcome"],
                "replacement_flags": t.get("replacement_flags", []),
            }
            for t in excluded
        ],
        "last_updated":        datetime.now(timezone.utc).isoformat(),
        "agent_stats":         _build_agent_stats(trades),
        "portfolio_metrics":   _build_portfolio_metrics(trades),
    }


def save_summary(summary: dict, dry_run: bool = False) -> None:
    if dry_run:
        return
    with open(SUMMARY_FILE, "w") as f:
        json.dump(summary, f, indent=2)


# ── print ─────────────────────────────────────────────────────────────────────

def print_summary(trades: list[dict], summary: dict) -> None:
    resolved    = [t for t in trades if t.get("outcome") is not None]
    n_valid     = summary["total_valid"]
    n_valid_wins = summary["total_valid_wins"]

    print(f"\n{'═'*65}")
    print(f"  PERFORMANCE SUMMARY  —  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'═'*65}\n")

    print(f"  Trades logged    : {summary['total_logged']}")
    print(f"  Resolved         : {summary['total_resolved']}")
    print(f"  Open (pending)   : {summary['total_open']}")
    print()

    if not resolved:
        print("  No resolved trades yet — check back after games complete.\n")
        return

    # ── Clean vs Suspect buckets ──────────────────────────────────────────────
    def _fmt_bucket(label: str, b: dict, primary: bool = False) -> None:
        tag = " ← PRIMARY" if primary else ""
        if b["resolved"] == 0:
            print(f"  {label:<22}: {b['open']} open, 0 resolved{tag}")
            return
        wr_str = f"{b['win_rate']:.1%}" if b["win_rate"] is not None else "—"
        pv_str = f"  p={b['p_value']:.4f}" if b["p_value"] is not None else ""
        sig_str = ""
        if b["p_value"] is not None:
            if b["p_value"] < 0.01:   sig_str = " ★★★"
            elif b["p_value"] < 0.05: sig_str = " ★★"
            elif b["p_value"] < 0.10: sig_str = " ★"
        print(
            f"  {label:<22}: {b['wins']}W / {b['losses']}L  "
            f"({wr_str}){pv_str}{sig_str}  open={b['open']}{tag}"
        )

    print(f"  {'─'*60}")
    _fmt_bucket("Clean  (≤3h)", summary["clean_trades"],   primary=True)
    _fmt_bucket("Suspect (>3h)", summary["suspect_trades"], primary=False)
    print(f"  {'─'*60}")
    print()

    n_valid    = summary["total_valid"]
    n_excluded = summary["total_excluded"]
    wr = summary["win_rate_overall"]
    pv = summary["p_value"]
    sig = ""
    if pv is not None:
        if pv < 0.01:   sig = "  ★★★ p<0.01 — HIGHLY SIGNIFICANT"
        elif pv < 0.05: sig = "  ★★  p<0.05 — SIGNIFICANT"
        elif pv < 0.10: sig = "  ★   p<0.10 — marginal"
        else:           sig = "  (not yet significant)"

    excl_note = f"  ({n_excluded} excluded — signal/gap invalid at 2h mark)" if n_excluded else ""
    print(f"  Win rate (valid) : {wr:.1%}  ({n_valid_wins}/{n_valid}){sig}{excl_note}")
    if pv is not None:
        print(f"  p-value vs 50%   : {pv:.4f}")

    pm = summary.get("portfolio_metrics", {})
    avg_ev = pm.get("avg_ev_per_trade")
    if avg_ev is not None:
        ev_sign = "+" if avg_ev >= 0 else ""
        print(f"  Avg EV per trade : {ev_sign}{avg_ev:.4f}  ({ev_sign}{avg_ev*100:.2f}¢ per $1 risked)")
    print()

    # Excluded trades (signal flipped or gap closed by 2h snapshot)
    if summary["excluded_trades"]:
        print(f"  EXCLUDED from analysis (replacement invalidated signal):")
        for ex in summary["excluded_trades"]:
            flags = ", ".join(ex["replacement_flags"])
            print(f"    {ex['team']:<28} {ex['outcome']:<5}  gap={ex['gap']:+.1%}  flags={flags}")
        print()

    # Tier breakdown
    ta_wr = summary.get("win_rate_tier_a")
    tb_wr = summary.get("win_rate_tier_b")
    tc_wr = summary.get("win_rate_tier_c")
    if ta_wr is not None:
        print(f"  Tier A (5–10%)   : {ta_wr:.1%}")
    if tb_wr is not None:
        print(f"  Tier B (10–15%)  : {tb_wr:.1%}")
    if tc_wr is not None:
        print(f"  Tier C (15%+)    : {tc_wr:.1%}")
    print()

    # Gap bucket gradient
    bucket_order = ["5_7", "7_10", "10_15", "15_plus"]
    bucket_labels = {"5_7": "5–7%", "7_10": "7–10%", "10_15": "10–15%", "15_plus": "15%+"}
    print(f"  Gap gradient:")
    print(f"  {'Range':<10}  {'Trades':>6}  {'Win rate':>9}  Visual")
    print(f"  {'─'*45}")
    for b in bucket_order:
        if b not in summary["by_gap_bucket"]:
            continue
        d   = summary["by_gap_bucket"][b]
        bar = "█" * round(d["win_rate"] * 10)
        print(f"  {bucket_labels[b]:<10}  {d['trades']:>6}  {d['win_rate']:>8.1%}  {bar}")
    print()

    # By book
    print(f"  By book:")
    print(f"  {'Book':<14}  {'Trades':>6}  {'Win rate':>9}")
    print(f"  {'─'*35}")
    for book in ["pinnacle", "draftkings", "fanduel"]:
        if book not in summary["by_book"]:
            continue
        d = summary["by_book"][book]
        print(f"  {book:<14}  {d['trades']:>6}  {d['win_rate']:>8.1%}")
    print()

    # Avg gap: winners vs losers
    if summary["avg_gap_winners"] and summary["avg_gap_losers"]:
        print(f"  Avg gap — winners : {summary['avg_gap_winners']:.1%}")
        print(f"  Avg gap — losers  : {summary['avg_gap_losers']:.1%}")
        if summary["avg_gap_winners"] > summary["avg_gap_losers"]:
            print(f"  ↑ Larger gaps correlate with wins — edge is real")
        print()

    # Recent resolved trades
    recent = sorted(
        [t for t in resolved],
        key=lambda x: x.get("resolved_at") or "",
        reverse=True
    )[:10]

    if recent:
        print(f"  Last {len(recent)} resolved trades:")
        print(f"  {'TEAM':<28} {'GAME':<38} {'GAP':>7}  {'OUTCOME':<6}  RESOLVED")
        print(f"  {'─'*95}")
        for t in recent:
            res_ts = (t.get("resolved_at") or "")[:10]
            icon   = "✓" if t["outcome"] == "WIN" else "✗"
            print(
                f"  {t['team']:<28} {t['game']:<38}"
                f"  {t['abs_gap']:>6.1%}  {icon} {t['outcome']:<4}  {res_ts}"
            )
    print()


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",   action="store_true", help="Resolve but don't write files")
    parser.add_argument("--force-all", action="store_true", help="Re-resolve all trades including already-resolved")
    args = parser.parse_args()

    if args.force_all:
        for t in (trades := load_trades()):
            t["outcome"] = t["correct"] = t["resolution_price"] = t["resolved_at"] = None
    else:
        trades = load_trades()

    # 1. Ingest new trades from snapshots (agent gates every new trade)
    trades, new_count, replaced_count, skipped_count = ingest_new_trades(trades)
    backfilled = backfill_timing(trades)
    if backfilled:
        print(f"Backfilled timing fields for {backfilled} existing trade(s).")
    if replaced_count:
        print(f"Replaced {replaced_count} timing-suspect trade(s) with cleaner snapshot(s).")
    print(f"Ingested {new_count} new trade(s) from snapshots.  Skipped by agent: {skipped_count}")
    print(f"Total trades: {len(trades)}  |  Open: {sum(1 for t in trades if t['outcome'] is None)}")

    # 2. Resolve eligible open trades + shadow-resolve skipped trades
    resolved, still_open = resolve_trades(trades, dry_run=args.dry_run)
    if not args.dry_run:
        shadow_count = resolve_skipped_trades()
        if shadow_count:
            print(f"Shadow-resolved {shadow_count} skipped trade(s).")
    print(f"Resolved: {resolved}  |  Still pending (game not finished): {still_open}")

    # 3. Save trades
    save_trades(trades, dry_run=args.dry_run)

    # 3b. Settle any sandbox positions whose paper trade just resolved
    if not args.dry_run:
        try:
            from execution.position_manager import settle_resolved_positions
            settle_resolved_positions(trades)
        except Exception as _sb_err:
            print(f"  [SANDBOX] settle_resolved_positions skipped: {_sb_err}")

    # 4. Build + save summary
    summary = build_summary(trades)
    save_summary(summary, dry_run=args.dry_run)

    # 5. Print
    print_summary(trades, summary)

    if args.dry_run:
        print("  [dry-run] No files written.\n")


if __name__ == "__main__":
    main()
