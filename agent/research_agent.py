"""
agent/research_agent.py

Research agent — evaluates flagged prediction market gaps before logging.

Checks:
  1. Current Pinnacle line stability vs snapshot price (TheOddsAPI)
  2. Breaking news: injuries, lineup changes, weather (Claude web_search)
  3. Synthesizes a structured verdict: TRADE / SKIP / MONITOR

Cost optimisations:
  - System prompt is cache_control=ephemeral — 90% cost reduction after first call
  - Single combined web search per trade (was 4 separate queries)
  - Search results truncated to 1500 chars each before re-entering context

Returns a verdict dict. Never raises — defaults to MONITOR on any error
so agent failures never block trade logging.
"""

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.utils import remove_vig

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ODDS_KEY      = os.getenv("ODDS_API_KEY", "")
ODDS_BASE     = os.getenv("ODDS_API_BASE", "https://api.theoddsapi.com")
MODEL         = "claude-sonnet-4-6"

SPORT_KEYS = {"MLB": "baseball_mlb", "NBA": "basketball_nba"}

PINNACLE_MOVEMENT_THRESHOLD = 0.03
LARGE_GAP_WARN              = 0.20

COST_LOG = "data/agent_cost_log.csv"

# Pricing per million tokens (Sonnet 4.6)
PRICE_INPUT         = 3.00
PRICE_CACHE_READ    = 0.30
PRICE_OUTPUT        = 15.00
PRICE_PER_SEARCH    = 0.01


SYSTEM_PROMPT = """You are a sports prediction market research analyst trained by a professional sports bettor with expertise across MLB, NFL, NBA, and soccer.

Your job is to analyze pricing gaps between Vegas sportsbook consensus (Pinnacle) and Kalshi prediction market contracts. When a gap exists, you determine whether it is driven by INFORMATION (a real reason Pinnacle might be stale or wrong) or BEHAVIORAL BIAS (retail sentiment, favorite-longshot bias, name recognition, fan loyalty).

SIGNALS THAT MEAN THE GAP IS INFORMATION-DRIVEN — SKIP:
- Starting pitcher scratched or changed within 6 hours
- Key position player (top 3 hitter or cleanup) ruled out
- Pinnacle line has moved more than 3 percentage points since market open — sharp money is moving
- Significant weather event (wind 15+ mph blowing in at a baseball stadium)
- Bullpen heavily used in previous 2 days for the favored team
- Any credible injury report affecting a starter

SIGNALS THAT MEAN THE GAP IS BEHAVIORAL — TRADE:
- No lineup changes from expected starting roster
- Starting pitcher confirmed and healthy
- Pinnacle line has been stable for 3+ hours
- Gap is concentrated on a heavily favored team (Vegas 65%+) — favorite-longshot bias
- No injury news in the last 6 hours for either team
- Public narrative favors underdog (recent winning streak, nationally televised game, popular team)
- Home team overpriced by Kalshi retail crowd

Always weight information signals above behavioral signals. One confirmed injury to a starter overrides any number of behavioral signals pointing to TRADE.

Respond ONLY with a valid JSON object. No preamble, no explanation outside the JSON, no markdown backticks."""

# Cached system prompt format — cuts input cost 90% after the first call
_CACHED_SYSTEM = [
    {
        "type":          "text",
        "text":          SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }
]


# ── Pinnacle stability check ───────────────────────────────────────────────────

def _last_word(name: str) -> str:
    return name.lower().strip().split()[-1] if name.strip() else ""


def _teams_match(h1: str, a1: str, h2: str, a2: str) -> bool:
    return _last_word(h1) == _last_word(h2) and _last_word(a1) == _last_word(a2)


def _fetch_current_pinnacle(game_dict: dict) -> tuple[float | None, float | None]:
    """
    Fetch current Pinnacle vig-free probability for the traded team/side.
    Returns (current_prob, abs_movement_vs_snapshot) or (None, None) on failure.
    """
    sport     = game_dict.get("sport", "MLB")
    home      = game_dict.get("home_team", "")
    away      = game_dict.get("away_team", "")
    side      = game_dict.get("side", "HOME")
    snap_prob = game_dict.get("pinnacle_prob") or game_dict.get("v_prob")
    sport_key = SPORT_KEYS.get(sport.upper(), "baseball_mlb")

    try:
        resp = requests.get(
            f"{ODDS_BASE}/odds/",
            params={
                "sport_key":  sport_key,
                "markets":    "h2h",
                "bookmakers": "pinnacle",
                "oddsFormat": "american",
            },
            headers={"x-api-key": ODDS_KEY},
            timeout=30,
        )
        resp.raise_for_status()
        games = resp.json().get("data", [])
    except Exception:
        return None, None

    for g in games:
        g_home = g.get("home_team", "")
        g_away = g.get("away_team", "")
        if not _teams_match(home, away, g_home, g_away):
            continue

        outcomes: dict[str, float] = {}
        for book in g.get("books", []):
            if book.get("book") == "pinnacle":
                for o in book.get("outcomes", []):
                    outcomes[o["name"]] = o["price"]

        if len(outcomes) < 2:
            return None, None

        names = list(outcomes.keys())
        home_p, away_p = remove_vig(outcomes[names[0]], outcomes[names[1]])
        current_prob   = home_p if side.upper() == "HOME" else away_p

        if snap_prob is not None:
            return round(current_prob, 4), round(abs(current_prob - snap_prob), 4)
        return round(current_prob, 4), None

    return None, None


# ── Search result truncation (Fix 3) ──────────────────────────────────────────

def truncate_search_result(result: str, max_chars: int = 1500) -> str:
    """
    Truncate search result to max_chars characters.
    1500 chars ≈ 375 tokens — enough to catch any injury or lineup news
    in the headline and lede. Appends ellipsis so Claude knows it was cut.
    """
    if len(result) > max_chars:
        return result[:max_chars] + "...[truncated]"
    return result


def _truncate_content_blocks(content: list) -> list:
    """
    Walk through a list of content blocks and truncate text in any
    web_search_tool_result blocks before they re-enter the context window.
    """
    out = []
    for block in content:
        block_type = getattr(block, "type", "")
        if block_type == "web_search_tool_result":
            raw_items = getattr(block, "content", []) or []
            truncated_items = []
            for item in raw_items:
                if isinstance(item, str):
                    truncated_items.append(truncate_search_result(item))
                elif hasattr(item, "text"):
                    truncated_items.append(
                        {"type": "text", "text": truncate_search_result(item.text)}
                    )
                elif isinstance(item, dict) and "text" in item:
                    truncated_items.append(
                        {**item, "text": truncate_search_result(item["text"])}
                    )
                else:
                    truncated_items.append(item)
            block_dict: dict = {"type": block_type, "content": truncated_items}
            if hasattr(block, "tool_use_id"):
                block_dict["tool_use_id"] = block.tool_use_id
            out.append(block_dict)
        else:
            out.append(block)
    return out


# ── Message builder ────────────────────────────────────────────────────────────

def _build_user_message(
    game_dict: dict,
    current_pinnacle: float | None,
    pinnacle_movement: float | None,
    pinnacle_stable: bool,
) -> str:
    home         = game_dict.get("home_team", "")
    away         = game_dict.get("away_team", "")
    date         = game_dict.get("date", "")
    game_time    = game_dict.get("game_time", "")
    sport        = game_dict.get("sport", "MLB")
    k_prob       = game_dict.get("kalshi_prob") or game_dict.get("k_prob", 0)
    v_prob       = game_dict.get("pinnacle_prob") or game_dict.get("v_prob", 0)
    gap          = game_dict.get("gap", 0)
    abs_gap      = abs(gap)
    tier         = game_dict.get("tier", 2)
    signal       = game_dict.get("signal") or ("BUY_YES" if gap < 0 else "BUY_NO")
    hours_before = game_dict.get("hours_before_game")

    timing_note = ""
    if isinstance(hours_before, (int, float)) and hours_before > 3.0:
        timing_note = (
            f"\n\nWARNING — EARLY SNAPSHOT: This gap was captured {hours_before:.1f}h before "
            f"game time, well outside the clean 2h window. Gaps frequently shrink or flip sign "
            f"by game time on early captures. Treat gap size as provisional and be more "
            f"conservative with TRADE calls."
        )

    large_gap_note = ""
    if abs_gap >= LARGE_GAP_WARN:
        large_gap_note = (
            f"\n\nWARNING — UNUSUALLY LARGE GAP ({abs_gap:.1%}): Behavioral bias rarely "
            f"produces gaps this wide. More likely causes: information event not yet reflected "
            f"in Kalshi, early pricing noise, or a line that hasn't settled. Apply extra "
            f"scrutiny before calling TRADE."
        )

    if pinnacle_movement is None:
        pin_note = "Pinnacle stability: CHECK FAILED — assumed stable, pinnacle_movement = null"
    elif not pinnacle_stable:
        pin_note = (
            f"Pinnacle stability: UNSTABLE — moved {pinnacle_movement:.1%} since snapshot "
            f"(now {current_pinnacle:.1%} vs snap {v_prob:.1%}). "
            f"Sharp money is moving. This alone triggers SKIP per decision rules."
        )
    else:
        pin_note = (
            f"Pinnacle stability: STABLE — moved only {pinnacle_movement:.1%} since snapshot "
            f"(now {current_pinnacle:.1%})."
        )

    pin_movement_json = pinnacle_movement if pinnacle_movement is not None else "null"

    # Single combined search query (Fix 2 — was 4 separate queries)
    search_query = (
        f"{home} {away} starting pitcher lineup injury scratch {date}"
    )

    return (
        f"Analyze this {sport} prediction market gap and return a research verdict.\n\n"
        f"Game:   {away} @ {home}\n"
        f"Date:   {date}  ({game_time})\n"
        f"Signal: {signal}\n"
        f"Kalshi prob:            {k_prob:.1%}\n"
        f"Pinnacle prob (snap):   {v_prob:.1%}\n"
        f"Gap:                    {gap:+.1%}  (abs {abs_gap:.1%})\n"
        f"Tier:                   {tier}\n"
        f"Hours before game:      {hours_before}\n"
        f"{pin_note}"
        f"{timing_note}"
        f"{large_gap_note}\n\n"
        f"Run exactly ONE web search using this query:\n"
        f'  "{search_query}"\n\n'
        f"If the search returns no relevant results, set news_found=false and note "
        f"\"No news found\" in reasoning. Do not run additional searches.\n\n"
        f"After searching, apply the decision rules from your system prompt and return ONLY "
        f"this JSON object (no preamble, no markdown fences):\n"
        f"{{\n"
        f'  "news_found":          true or false,\n'
        f'  "news_detail":         "specific finding or null",\n'
        f'  "news_source":         "url or null",\n'
        f'  "pitcher_confirmed":   true or false or null,\n'
        f'  "weather_issue":       true or false,\n'
        f'  "pinnacle_stable":     {str(pinnacle_stable).lower()},\n'
        f'  "pinnacle_movement":   {pin_movement_json},\n'
        f'  "gap_type":            "BEHAVIORAL" or "INFORMATIONAL",\n'
        f'  "confidence":          "HIGH" or "MEDIUM" or "LOW",\n'
        f'  "recommendation":      "TRADE" or "SKIP" or "MONITOR",\n'
        f'  "reasoning":           "2-3 sentence plain English explanation",\n'
        f'  "gap_explanation":     "one sentence explaining why the gap exists"\n'
        f"}}\n\n"
        f"DECISION RULES (follow exactly):\n"
        f"  SKIP   — any of: news_found=true, pinnacle_stable=false, weather_issue=true\n"
        f"  HIGH confidence TRADE — all of: news_found=false, pinnacle_stable=true, "
        f"weather_issue=false, abs_gap >= 0.10, tier == 1\n"
        f"  MEDIUM confidence TRADE — all of: news_found=false, pinnacle_stable=true, "
        f"weather_issue=false, abs_gap >= 0.05, tier == 2\n"
        f"  MONITOR — everything else that is not SKIP"
    )


# ── JSON parsing ───────────────────────────────────────────────────────────────

def _parse_verdict(text: str) -> dict | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


def _default_verdict(reason: str, is_error: bool = True) -> dict:
    return {
        "news_found":        False,
        "news_detail":       None,
        "news_source":       None,
        "pitcher_confirmed": None,
        "weather_issue":     False,
        "pinnacle_stable":   True,
        "pinnacle_movement": None,
        "gap_type":          "BEHAVIORAL",
        "confidence":        "LOW",
        "recommendation":    "MONITOR",
        "reasoning":         reason,
        "gap_explanation":   "Unable to analyze — defaulted to MONITOR",
        "_agent_error":      is_error,
    }


# ── Cost tracking ──────────────────────────────────────────────────────────────

def _print_and_log_cost(
    game_label: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    search_calls: int,
) -> float:
    """Print cost breakdown and append a row to agent_cost_log.csv."""
    input_cost  = (input_tokens       / 1_000_000) * PRICE_INPUT
    cache_cost  = (cache_read_tokens  / 1_000_000) * PRICE_CACHE_READ
    output_cost = (output_tokens      / 1_000_000) * PRICE_OUTPUT
    search_cost = search_calls * PRICE_PER_SEARCH
    total       = input_cost + cache_cost + output_cost + search_cost

    print(f"\n  [AGENT COST] {game_label}")
    print(f"  Input tokens:      {input_tokens:>8,}  (${input_cost:.4f})")
    print(f"  Cache read tokens: {cache_read_tokens:>8,}  (${cache_cost:.4f})")
    print(f"  Output tokens:     {output_tokens:>8,}  (${output_cost:.4f})")
    print(f"  Web searches:      {search_calls:>8}  (${search_cost:.4f})")
    print(f"  {'─'*40}")
    print(f"  Total this trade:           ${total:.4f}")

    os.makedirs(os.path.dirname(COST_LOG), exist_ok=True)
    write_header = not os.path.exists(COST_LOG)
    with open(COST_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow([
                "timestamp", "game", "input_tokens", "output_tokens",
                "cache_read_tokens", "search_calls", "estimated_cost_usd",
            ])
        w.writerow([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            game_label,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            search_calls,
            round(total, 6),
        ])

    return total


# ── Main entry point ───────────────────────────────────────────────────────────

def run(game_dict: dict) -> dict:
    """
    Evaluate a flagged trade. Returns a verdict dict.
    Never raises — defaults to MONITOR on any error.
    """
    try:
        import anthropic as _anthropic
    except ImportError:
        return _default_verdict("anthropic package not installed")

    if not ANTHROPIC_KEY:
        return _default_verdict("ANTHROPIC_API_KEY not set in .env")

    # Step 1: Pinnacle stability (fast check before burning API tokens)
    current_pinnacle, pinnacle_movement = _fetch_current_pinnacle(game_dict)
    pinnacle_stable = (
        True if pinnacle_movement is None
        else pinnacle_movement < PINNACLE_MOVEMENT_THRESHOLD
    )

    # Step 2: Build research prompt
    user_msg = _build_user_message(
        game_dict, current_pinnacle, pinnacle_movement, pinnacle_stable
    )

    # Step 3: Call Claude with web_search + cached system prompt
    client   = _anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    messages = [{"role": "user", "content": user_msg}]

    # Accumulators for cost tracking across all turns
    total_input   = 0
    total_output  = 0
    total_cache   = 0
    total_searches = 0

    game_label = (
        f"{game_dict.get('away_team', '?')} @ {game_dict.get('home_team', '?')}"
    )

    def _make_api_call(msgs: list, max_tok: int = 2048):
        return client.beta.messages.create(
            model=MODEL,
            max_tokens=max_tok,
            system=_CACHED_SYSTEM,          # Fix 1 — cached system prompt
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=msgs,
            betas=["web-search-2025-03-05"],
        )

    try:
        response = _make_api_call(messages)

        # Agentic loop — truncate search results (Fix 3) before they re-enter context
        for _ in range(8):
            if response.stop_reason != "tool_use":
                break

            # Accumulate usage and search call count for this turn
            u = response.usage
            total_input  += getattr(u, "input_tokens",  0)
            total_output += getattr(u, "output_tokens", 0)
            total_cache  += getattr(u, "cache_read_input_tokens", 0)
            total_searches += sum(
                1 for b in response.content
                if getattr(b, "type", "") == "tool_use"
                and getattr(b, "name", "") == "web_search"
            )

            # Truncate search result blocks before they go back into context
            truncated = _truncate_content_blocks(response.content)
            messages.append({"role": "assistant", "content": truncated})

            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": []}
                for b in response.content
                if getattr(b, "type", "") == "tool_use"
            ]
            messages.append({"role": "user", "content": tool_results})
            response = _make_api_call(messages)

        # Final turn usage
        u = response.usage
        total_input  += getattr(u, "input_tokens",  0)
        total_output += getattr(u, "output_tokens", 0)
        total_cache  += getattr(u, "cache_read_input_tokens", 0)
        total_searches += sum(
            1 for b in response.content
            if getattr(b, "type", "") == "tool_use"
            and getattr(b, "name", "") == "web_search"
        )

        raw_text = "".join(getattr(b, "text", "") for b in response.content)
        verdict  = _parse_verdict(raw_text)

        # Retry on JSON parse failure
        if verdict is None:
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role":    "user",
                "content": "Your response was not valid JSON. Return ONLY the JSON object, no other text.",
            })
            retry = _make_api_call(messages, max_tok=1024)
            u = retry.usage
            total_input  += getattr(u, "input_tokens",  0)
            total_output += getattr(u, "output_tokens", 0)
            total_cache  += getattr(u, "cache_read_input_tokens", 0)
            retry_text = "".join(getattr(b, "text", "") for b in retry.content)
            verdict = _parse_verdict(retry_text)

        if verdict is None:
            return _default_verdict("JSON parse failed after retry")

        # Direct Pinnacle check is authoritative
        verdict["pinnacle_stable"]   = pinnacle_stable
        verdict["pinnacle_movement"] = (
            round(pinnacle_movement, 4) if pinnacle_movement is not None else None
        )
        verdict["_raw_response"] = raw_text

        # Log and print cost
        _print_and_log_cost(
            game_label, total_input, total_output, total_cache, total_searches
        )

        return verdict

    except Exception as e:
        return _default_verdict(f"API error: {str(e)[:300]}")
