"""
agent/edge_discovery_agent.py

Automated multi-book edge scanner. Fetches Kalshi markets + Pinnacle, DraftKings,
and FanDuel; computes a gap matrix for each game; ranks edge candidates; and
auto-triggers the research agent for any candidate above the minimum threshold.

Two modes:
  --upcoming   scan all open Kalshi markets for today's games (scheduled/default)
  --date YYYY-MM-DD   scan games on a specific date

Usage:
    python agent/edge_discovery_agent.py --sport mlb --upcoming
    python agent/edge_discovery_agent.py --sport mlb --date 2026-07-01
    python agent/edge_discovery_agent.py --sport nba --upcoming --no-research
    python agent/edge_discovery_agent.py --sport mlb --upcoming --save
"""
import argparse
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
from scripts.snapshot_gaps import match_team, kalshi_mid

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KALSHI_BASE = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")
ODDS_BASE   = os.getenv("ODDS_API_BASE",   "https://api.theoddsapi.com")
ODDS_KEY    = os.getenv("ODDS_API_KEY",    "")

SERIES = {
    "mlb": "KXMLBGAME",
    "nba": "KXNBAGAME",
    "nfl": "KXNFLGAME",
}
SPORT_KEYS = {
    "mlb": "baseball_mlb",
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
}
ALL_BOOKS   = ["pinnacle", "draftkings", "fanduel"]
MIN_GAP     = 0.05   # tier 2 threshold — anything below this isn't a candidate
TIER1_GAP   = 0.10

LOG_FILE    = os.path.join(BASE, "data", "snapshots", "edge_discovery_log.txt")
ET          = ZoneInfo("America/New_York")


# ── fetch helpers ─────────────────────────────────────────────────────────────

def fetch_kalshi_markets(series: str) -> list[dict]:
    resp = requests.get(
        f"{KALSHI_BASE}/markets",
        params={"series_ticker": series, "status": "open", "limit": 200},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("markets", [])


def fetch_all_books(sport_key: str) -> dict[str, dict]:
    """
    Returns {(home_team, away_team): {book: {home_team: vf_prob, away_team: vf_prob, vig}}}
    fetched in a single API call for all three books.
    """
    resp = requests.get(
        f"{ODDS_BASE}/odds/",
        params={
            "sport_key":  sport_key,
            "markets":    "h2h",
            "bookmakers": ",".join(ALL_BOOKS),
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
        key  = (home, away)
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
            index[key] = {"home": home, "away": away, "books": book_data}
    return index


# ── team matching ─────────────────────────────────────────────────────────────

def _get_vegas_teams(book_index: dict) -> list[str]:
    teams = set()
    for v in book_index.values():
        teams.add(v["home"])
        teams.add(v["away"])
    return list(teams)


def _match_game(k_names: list[str], book_index: dict) -> tuple | None:
    """Find the (home, away) key in book_index that matches both Kalshi sub-titles."""
    vegas_teams = _get_vegas_teams(book_index)
    mapping: dict[str, str] = {}
    for ks in k_names:
        m = match_team(ks, vegas_teams)
        if m:
            mapping[ks] = m
    if len(mapping) != 2 or len(set(mapping.values())) != 2:
        return None
    # Find the matching game key
    matched_teams = set(mapping.values())
    for (home, away) in book_index:
        if {home, away} == matched_teams:
            return (home, away)
    return None


# ── gap computation ───────────────────────────────────────────────────────────

def compute_gap_matrix(
    sport: str,
    filter_date: date | None = None,
) -> list[dict]:
    """
    Returns a list of candidate dicts, one per Kalshi game side that has at
    least one book gap >= MIN_GAP. Sorted by best (max abs_gap across books).
    """
    series     = SERIES[sport]
    sport_key  = SPORT_KEYS[sport]
    now        = datetime.now(timezone.utc)
    et_today   = now.astimezone(ET).date()

    raw_markets = fetch_kalshi_markets(series)
    book_index  = fetch_all_books(sport_key)

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

        start_utc = ticker_to_utc(et)
        if not start_utc:
            continue

        game_date_et = start_utc.astimezone(ET).date()

        # Date filtering
        if filter_date is not None:
            if game_date_et != filter_date:
                continue
        else:
            # --upcoming: only today's games in ET
            if game_date_et != et_today:
                continue

        hours_until = (start_utc - now).total_seconds() / 3600

        k_names   = [s["yes_sub_title"] for s in sides]
        game_key  = _match_game(k_names, book_index)
        if not game_key:
            continue

        home, away   = game_key
        game_data    = book_index[game_key]
        book_probs   = game_data["books"]

        for s in sides:
            sub       = s["yes_sub_title"]
            matched   = match_team(sub, [home, away])
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
                "sport":         sport.upper(),
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
                "tier":          1 if best_abs_gap >= TIER1_GAP else 2,
                # Flattened fields for research_agent compatibility
                "date":          start_utc.astimezone(ET).strftime("%b %-d, %Y"),
                "game_time":     start_utc.astimezone(ET).strftime("%-I:%M %p ET"),
                "gap":           gaps_by_book[best_book]["gap"],
                "signal":        gaps_by_book[best_book]["signal"],
                "hours_before_game": round(hours_until, 2),
                "pinnacle_prob": round(pin_vf, 4) if pin_vf else None,
            }
            candidate["edge_context"] = classify_edge(candidate)
            candidates.append(candidate)

    # Sort: tradeable candidates first (best_abs_gap >= MIN_GAP), then by gap size
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

def print_gap_matrix(candidates: list[dict], sport: str) -> None:
    above   = [c for c in candidates if c["best_abs_gap"] >= MIN_GAP]
    below   = [c for c in candidates if c["best_abs_gap"] < MIN_GAP]
    n_games = len({c["event_ticker"] for c in candidates})

    print(f"\n{'═'*90}")
    print(f"  EDGE DISCOVERY REPORT — {sport.upper()}")
    print(f"  {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")
    print(f"  {n_games} games scanned  |  {len(above)} side(s) above {MIN_GAP:.0%} threshold")
    print(f"{'═'*90}")

    if above:
        print(f"\n  ★ CANDIDATES (gap ≥ {MIN_GAP:.0%})")
        hdr = f"  {'TEAM':<24} {'GAME':<38} {'K%':>5}  {'PIN':>5}  {'DK':>5}  {'FD':>5}  {'BEST':>7}  {'CONS':>5}  SIGNAL"
        print(hdr)
        print(f"  {'─'*115}")
        for c in above:
            pin_str = f"{c['gaps']['pinnacle']['book_vf']:.1%}" if "pinnacle" in c["gaps"] else "  — "
            dk_str  = f"{c['gaps']['draftkings']['book_vf']:.1%}" if "draftkings" in c["gaps"] else "  — "
            fd_str  = f"{c['gaps']['fanduel']['book_vf']:.1%}" if "fanduel" in c["gaps"] else "  — "
            cons    = f"{c['consensus']}/{c['books_checked']}"
            tier    = f"T{c['tier']}"
            print(
                f"  {c['team']:<24} {c['game']:<38}"
                f"  {c['k_prob']:.1%}  {pin_str}  {dk_str}  {fd_str}"
                f"  {c['best_abs_gap']:>+7.1%}  {cons:>5}  {c['signal']} [{c['best_book'].upper()} {tier}]"
            )
    else:
        print(f"\n  No candidates above {MIN_GAP:.0%} threshold today.")

    if below:
        print(f"\n  — Remaining games (gap < {MIN_GAP:.0%})")
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

def classify_edge(candidate: dict) -> dict:
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

    # Rule 1: Market anomaly — supersedes all others
    if pin_gap >= 0.20:
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
    if consensus >= 3 and best >= 0.05:
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
    if pin_gap >= 0.07 and dk_gap < 0.03 and fd_gap < 0.03:
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
    if max(dk_gap, fd_gap) >= 0.07 and pin_gap < 0.03:
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
    if pin_gap >= 0.05 and consensus >= 2:
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

def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-book Kalshi edge scanner")
    parser.add_argument("--sport",       default="mlb", choices=list(SERIES.keys()))
    parser.add_argument("--date",        default=None,
                        help="Scan games on this date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--upcoming",    action="store_true",
                        help="Scan all of today's open games (same as omitting --date).")
    parser.add_argument("--no-research", action="store_true",
                        help="Print gap matrix only; do not call research agent.")
    parser.add_argument("--save",        action="store_true",
                        help="Save results to outputs/edge_discovery_YYYY-MM-DD.json")
    args = parser.parse_args()

    filter_date = None
    if args.date:
        filter_date = date.fromisoformat(args.date)

    sport = args.sport.lower()
    _log(f"Edge discovery started — sport={sport.upper()} "
         f"date={filter_date or 'today'} research={'off' if args.no_research else 'on'}")

    # Fetch and compute
    print(f"\nFetching Kalshi markets and book lines for {sport.upper()}...", flush=True)
    try:
        candidates = compute_gap_matrix(sport, filter_date)
    except Exception as e:
        _log(f"ERROR fetching data: {e}")
        raise

    print_gap_matrix(candidates, sport)

    # Research any candidates above threshold
    above = [c for c in candidates if c["best_abs_gap"] >= MIN_GAP]
    verdicts: list[dict] = []

    if above and not args.no_research:
        print(f"\n  Running research agent on {len(above)} candidate(s)...\n")
        try:
            from agent import research_agent
        except ImportError:
            import research_agent  # type: ignore

        for c in above:
            ec = c.get("edge_context", {})
            etype = ec.get("edge_type", "UNKNOWN")
            lean  = ec.get("initial_lean", "?")
            print(f"  → {c['game']} — {c['team']} ({c['signal']}, gap={c['best_abs_gap']:.1%} vs {c['best_book']}) [{etype}, lean={lean}]")
            verdict = research_agent.run(c, edge_context=ec or None)
            rec     = verdict.get("recommendation", "MONITOR")
            conf    = verdict.get("confidence", "?")
            reason  = verdict.get("reasoning", "")[:120]
            print(f"     {rec} ({conf}) — {reason}")

            verdicts.append({**c, "research": verdict})
            _log(f"  {c['game']} | {c['team']} | gap={c['best_abs_gap']:.1%} | "
                 f"{rec} ({conf}) via {c['best_book'].upper()}")

        # Summarize tradeable
        trades = [v for v in verdicts if v["research"].get("recommendation") == "TRADE"]
        if trades:
            print(f"\n  ★ TRADE SIGNALS ({len(trades)}):")
            for t in trades:
                print(f"    {t['game']} — {t['team']} | {t['signal']} "
                      f"| gap={t['best_abs_gap']:.1%} ({t['best_book'].upper()}) "
                      f"| Tier {t['tier']} | {t['research']['confidence']}")
        else:
            print(f"\n  No TRADE signals after research.")

    elif above:
        print(f"\n  [--no-research] Skipping research agent.")
        verdicts = candidates

    # Save
    if args.save:
        out_dir = os.path.join(BASE, "outputs")
        os.makedirs(out_dir, exist_ok=True)
        fname = os.path.join(out_dir, f"edge_discovery_{filter_date or date.today()}.json")
        with open(fname, "w") as f:
            json.dump({
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sport":        sport.upper(),
                "candidates":   candidates,
                "verdicts":     verdicts,
            }, f, indent=2, default=str)
        print(f"\n  Saved to {fname}")

    _log(f"Edge discovery complete — {len(above)} candidate(s), "
         f"{len([v for v in verdicts if isinstance(v, dict) and v.get('research', {}).get('recommendation') == 'TRADE'])} TRADE signal(s)")


if __name__ == "__main__":
    main()
