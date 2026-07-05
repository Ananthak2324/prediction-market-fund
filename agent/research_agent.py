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
from core.desk_loader import DeskConfig

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ODDS_KEY      = os.getenv("ODDS_API_KEY", "")
ODDS_BASE     = os.getenv("ODDS_API_BASE", "https://api.theoddsapi.com")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Retained for any external callers still expecting a module-level default —
# desk config (desk.get("agent.model")) is authoritative going forward.
MODEL = "claude-sonnet-4-6"

# Edge-type framing prepended to user message (not system prompt, so cache stays warm)
_EDGE_FRAMING = {
    "MARKET_ANOMALY":       "ANOMALY INVESTIGATION: Gap ≥20%. Find what information is driving this. Default SKIP unless the gap is clearly behavioral.",
    "SHARP_SIGNAL":         "SHARP SIGNAL: Pinnacle alone diverges. Sharp books move on information first. Default SKIP — only MONITOR if zero news found.",
    "MULTI_BOOK_CONSENSUS": "CONSENSUS EDGE: All books agree Kalshi is mispriced. Confirm there is NO news justifying Kalshi's price. Default TRADE if clean.",
    "RETAIL_BOOK_SOFT":     "RETAIL LAG CHECK: DK/FanDuel diverge but Pinnacle agrees with Kalshi. Verify if DK/FanDuel are stale. Default MONITOR.",
    "BEHAVIORAL_RETAIL":    "BEHAVIORAL EDGE: Kalshi likely reflects retail narrative bias. Identify the narrative and confirm it is not supported by facts. Default TRADE if clean.",
}

_DEFAULT_EDGE_CONTEXT: dict = {
    "edge_type":          "BEHAVIORAL_RETAIL",
    "edge_confidence":    "LOW",
    "supporting_evidence": [],
    "risk_factors":       [],
    "research_priorities": ["Standard news/injury/weather check"],
    "initial_lean":       "MONITOR",
}


def _build_verdict_prompt(desk: DeskConfig, edge_context: dict) -> str:
    """Build the second-turn JSON verdict prompt with edge-type-specific decision rules."""
    edge_type    = edge_context.get("edge_type", "BEHAVIORAL_RETAIL")
    initial_lean = edge_context.get("initial_lean", "MONITOR")
    tier_a_min   = desk.tier_a[0]
    tier_b_min   = desk.tier_b[0]

    rules = {
        "MARKET_ANOMALY":       "SKIP only if you found fresh news (<48h) with a genuine status change today. TRADE if gap is behavioral and Pinnacle is stable.",
        "SHARP_SIGNAL":         "SKIP if Pinnacle moved >3pp AND you found confirming news. MONITOR if zero news found. TRADE is appropriate if Pinnacle has since stabilised.",
        "MULTI_BOOK_CONSENSUS": f"TRADE if no fresh status-change news found. SKIP only for confirmed new scratches or ruled-out players today. Gap ≥ {tier_a_min:.0%} required.",
        "RETAIL_BOOK_SOFT":     "TRADE if no news found and Pinnacle is stable. MONITOR only if evidence is genuinely ambiguous.",
        "BEHAVIORAL_RETAIL":    f"TRADE if gap ≥ {tier_a_min:.0%} and no fresh status-change news. SKIP only for breaking news TODAY — not chronic conditions.",
    }
    rule = rules.get(edge_type, f"TRADE if gap ≥ {tier_a_min:.0%} with no fresh status-change news.")

    return (
        "Based on your research above, return ONLY this exact JSON object "
        "(no preamble, no markdown fences, no backticks):\n\n"
        "{\n"
        '  "recommendation": "TRADE" or "SKIP" or "MONITOR",\n'
        '  "confidence": "HIGH" or "MEDIUM" or "LOW",\n'
        '  "reasoning": "2-3 sentence plain English explanation",\n\n'
        '  "disqualifier_check": {\n'
        '    "news_found": true or false,\n'
        '    "news_is_recent": true or false  (is this news from TODAY or YESTERDAY specifically?),\n'
        '    "news_age_estimate": "today" or "this week" or "older"  (how old is the news?),\n'
        '    "news_detail": "specific finding, or null if nothing found",\n'
        '    "news_source": "url or null",\n'
        '    "is_status_change": true or false  (is this a NEW status change, not a chronic condition?),\n'
        '    "pitcher_confirmed": true or false or null,\n'
        '    "weather_issue": true or false,\n'
        '    "pinnacle_stable": true or false,\n'
        '    "pinnacle_movement": float or null\n'
        "  },\n\n"
        '  "behavioral_analysis": {\n'
        '    "gap_type": "BEHAVIORAL" or "INFORMATIONAL",\n'
        '    "primary_bias": one of "HOME_PREMIUM" / "NAME_RECOGNITION" / "FAVORITE_LONGSHOT" / "RECENCY" / "PUBLIC_NARRATIVE" / "UNKNOWN",\n'
        '    "explanation": "2-3 sentence explanation of why retail Kalshi pricing diverges from sharp Pinnacle consensus"\n'
        "  },\n\n"
        '  "skip_reason": "NEW_INJURY" or "PINNACLE_MOVED" or null\n'
        "}\n\n"
        f"EDGE TYPE: {edge_type}  |  Initial data lean: {initial_lean}\n"
        f"Edge-specific rule: {rule}\n\n"
        "CRITICAL REMINDERS:\n"
        "- Long-term IL players (out weeks/months) are NOT disqualifiers — Pinnacle already priced them\n"
        "- 'Returning from IL' means the pitcher IS STARTING — that is NOT a scratch\n"
        "- Only SKIP for genuine status changes announced TODAY\n"
        "- A stable Pinnacle line confirms all known information is already priced in\n"
        "- Fill out behavioral_analysis even when issuing SKIP\n"
        f"- HIGH confidence TRADE: no news, Pinnacle stable, abs_gap ≥ {tier_b_min:.0%}\n"
        f"- MEDIUM confidence TRADE: no disqualifying news, Pinnacle stable, abs_gap ≥ {tier_a_min:.0%}\n"
    )


# ── Pinnacle stability check ───────────────────────────────────────────────────

def _last_word(name: str) -> str:
    return name.lower().strip().split()[-1] if name.strip() else ""


def _teams_match(h1: str, a1: str, h2: str, a2: str) -> bool:
    return _last_word(h1) == _last_word(h2) and _last_word(a1) == _last_word(a2)


def _fetch_current_pinnacle(desk: DeskConfig, game_dict: dict) -> tuple[float | None, float | None]:
    """
    Fetch current Pinnacle vig-free probability for the traded team/side.
    Returns (current_prob, abs_movement_vs_snapshot) or (None, None) on failure.

    Uses desk.sport_key exclusively — this is what fixes the pre-2026-07-04
    bug where WNBA games silently used MLB's odds-api sport key (the old
    module-level SPORT_KEYS dict only covered MLB/NBA).
    """
    home      = game_dict.get("home_team", "")
    away      = game_dict.get("away_team", "")
    side      = game_dict.get("side", "HOME")
    snap_prob = game_dict.get("pinnacle_prob") or game_dict.get("v_prob")
    sport_key = desk.sport_key

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
    desk: DeskConfig,
    game_dict: dict,
    current_pinnacle: float | None,
    pinnacle_movement: float | None,
    pinnacle_stable: bool,
    edge_context: dict | None = None,
) -> str:
    ec           = edge_context or _DEFAULT_EDGE_CONTEXT
    home         = game_dict.get("home_team", "")
    away         = game_dict.get("away_team", "")
    date         = game_dict.get("date", "")
    game_time    = game_dict.get("game_time", "")
    sport        = game_dict.get("sport", desk.sport_display_key)
    k_prob       = game_dict.get("kalshi_prob") or game_dict.get("k_prob", 0)
    v_prob       = game_dict.get("pinnacle_prob") or game_dict.get("v_prob", 0)
    gap          = game_dict.get("gap", 0)
    abs_gap      = abs(gap)
    tier         = game_dict.get("tier") or desk.gap_tier(abs_gap)
    signal       = game_dict.get("signal") or ("BUY_YES" if gap < 0 else "BUY_NO")
    hours_before = game_dict.get("hours_before_game")

    # Edge hypothesis block (edge-type-specific framing + research checklist)
    framing     = _EDGE_FRAMING.get(ec["edge_type"], "")
    evidence    = "\n".join(f"  - {e}" for e in ec.get("supporting_evidence", []))
    risks       = "\n".join(f"  - {r}" for r in ec.get("risk_factors", []))
    priorities  = "\n".join(f"  - {p}" for p in ec.get("research_priorities", []))
    edge_block  = (
        f"EDGE TYPE: {ec['edge_type']} ({ec.get('edge_confidence','?')} confidence) "
        f"— initial data lean: {ec.get('initial_lean','MONITOR')}\n"
        f"{framing}\n\n"
        + (f"Supporting evidence:\n{evidence}\n\n" if evidence else "")
        + (f"Risk factors:\n{risks}\n\n" if risks else "")
        + (f"RESEARCH CHECKLIST (address each point):\n{priorities}\n\n" if priorities else "")
    )

    timing_note = ""
    if isinstance(hours_before, (int, float)) and hours_before > 3.0:
        timing_note = (
            f"\n\nWARNING — EARLY SNAPSHOT: This gap was captured {hours_before:.1f}h before "
            f"game time, well outside the clean 2h window. Gaps frequently shrink or flip sign "
            f"by game time on early captures. Treat gap size as provisional and be more "
            f"conservative with TRADE calls."
        )

    large_gap_note = ""
    if abs_gap >= desk.get("thresholds.large_gap_warn", 0.20):
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

    search_query = desk.search_query_template.format(
        home_team=home, away_team=away, date=date
    )
    search_context = desk.get(
        "agent.prompt.search_context",
        "Report what you find about injuries, lineup changes, or weather. "
        "If nothing relevant is found, note that explicitly.",
    )

    return (
        f"{edge_block}"
        f"Analyze this {sport} prediction market gap.\n\n"
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
        f"Search for current news using this query:\n"
        f'  "{search_query}"\n\n'
        f"{search_context}"
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
        "recommendation":    "MONITOR",
        "confidence":        "LOW",
        "reasoning":         reason,
        # flat fields (backward compat with _make_trade_record)
        "gap_type":          "BEHAVIORAL",
        "gap_explanation":   "Unable to analyze — defaulted to MONITOR",
        "news_found":        False,
        "news_detail":       None,
        "news_source":       None,
        "pitcher_confirmed": None,
        "weather_issue":     False,
        "pinnacle_stable":   True,
        "pinnacle_movement": None,
        # enriched fields
        "news_is_recent":    False,
        "news_age_estimate": "",
        "is_status_change":  False,
        "primary_bias":      "UNKNOWN",
        "behavioral_analysis": {},
        "skip_reason":       None,
        "_agent_error":      is_error,
    }


def _normalize_verdict(
    raw: dict,
    pinnacle_stable: bool,
    pinnacle_movement: float | None,
) -> dict:
    """
    Flatten the new nested verdict schema to a flat dict for backward compatibility
    with _make_trade_record and paper_trades.json field expectations.
    """
    dc = raw.get("disqualifier_check") or {}
    ba = raw.get("behavioral_analysis") or {}

    return {
        # Core verdict
        "recommendation":    raw.get("recommendation", "MONITOR"),
        "confidence":        raw.get("confidence", "LOW"),
        "reasoning":         raw.get("reasoning", ""),
        # Flat fields used by _make_trade_record
        "gap_type":          ba.get("gap_type",    raw.get("gap_type",    "BEHAVIORAL")),
        "gap_explanation":   ba.get("explanation", raw.get("gap_explanation", "")),
        "news_found":        dc.get("news_found",        raw.get("news_found",        False)),
        "news_detail":       dc.get("news_detail",       raw.get("news_detail")),
        "news_source":       dc.get("news_source",       raw.get("news_source")),
        "pitcher_confirmed": dc.get("pitcher_confirmed", raw.get("pitcher_confirmed")),
        "weather_issue":     dc.get("weather_issue",     raw.get("weather_issue",     False)),
        # Pinnacle — authoritative from direct live check, not agent guess
        "pinnacle_stable":   pinnacle_stable,
        "pinnacle_movement": round(pinnacle_movement, 4) if pinnacle_movement is not None else None,
        # Enriched new fields
        "news_is_recent":    dc.get("news_is_recent",    True),
        "news_age_estimate": dc.get("news_age_estimate", ""),
        "is_status_change":  dc.get("is_status_change",  False),
        "primary_bias":      ba.get("primary_bias",      "UNKNOWN"),
        "behavioral_analysis": ba,
        "skip_reason":       raw.get("skip_reason"),
        "_raw_response":     raw.get("_raw_response", ""),
    }


# ── Cost tracking ──────────────────────────────────────────────────────────────

_COST_LOG_HEADER = [
    "timestamp", "game", "input_tokens", "output_tokens",
    "cache_read_tokens", "search_calls", "estimated_cost_usd",
    "recommendation", "skip_reason", "news_age",
]


def _migrate_cost_log_header(cost_log_path: str) -> None:
    """One-time migration: add 3 new columns to existing 7-column CSV rows."""
    if not os.path.exists(cost_log_path):
        return
    try:
        with open(cost_log_path, newline="") as f:
            rows = list(csv.reader(f))
        if not rows or len(rows[0]) >= len(_COST_LOG_HEADER):
            return  # already migrated or empty
        rows[0] = _COST_LOG_HEADER
        for i in range(1, len(rows)):
            while len(rows[i]) < len(_COST_LOG_HEADER):
                rows[i].append("")
        with open(cost_log_path, "w", newline="") as f:
            csv.writer(f).writerows(rows)
    except Exception:
        pass


def _print_and_log_cost(
    desk: DeskConfig,
    game_label: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    search_calls: int,
    recommendation: str = "",
    skip_reason: str | None = None,
    news_age: str = "",
) -> float:
    """Print cost breakdown and append a row to this desk's agent_cost_log.csv."""
    cost_log = os.path.join(BASE, desk.agent_cost_log_path)
    _migrate_cost_log_header(cost_log)

    pricing      = desk.get("agent.pricing", {})
    price_input  = pricing.get("input_per_million", 3.00)
    price_cache  = pricing.get("cache_read_per_million", 0.30)
    price_output = pricing.get("output_per_million", 15.00)
    price_search = pricing.get("per_search", 0.01)

    input_cost  = (input_tokens       / 1_000_000) * price_input
    cache_cost  = (cache_read_tokens  / 1_000_000) * price_cache
    output_cost = (output_tokens      / 1_000_000) * price_output
    search_cost = search_calls * price_search
    total       = input_cost + cache_cost + output_cost + search_cost

    print(f"\n  [AGENT COST] {game_label}")
    print(f"  Input tokens:      {input_tokens:>8,}  (${input_cost:.4f})")
    print(f"  Cache read tokens: {cache_read_tokens:>8,}  (${cache_cost:.4f})")
    print(f"  Output tokens:     {output_tokens:>8,}  (${output_cost:.4f})")
    print(f"  Web searches:      {search_calls:>8}  (${search_cost:.4f})")
    print(f"  {'─'*40}")
    print(f"  Total this trade:           ${total:.4f}")

    os.makedirs(os.path.dirname(cost_log), exist_ok=True)
    write_header = not os.path.exists(cost_log)
    with open(cost_log, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(_COST_LOG_HEADER)
        w.writerow([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            game_label,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            search_calls,
            round(total, 6),
            recommendation,
            skip_reason or "",
            news_age,
        ])

    return total


# ── Main entry point ───────────────────────────────────────────────────────────

def run(desk: DeskConfig, game_dict: dict, edge_context: dict | None = None) -> dict:
    """
    Evaluate a flagged trade. Returns a verdict dict.
    Never raises — defaults to MONITOR on any error.

    edge_context: output of classify_edge() from edge_discovery_agent. If None,
    falls back to BEHAVIORAL_RETAIL defaults (backward compatible).
    """
    try:
        import anthropic as _anthropic
    except ImportError:
        return _default_verdict("anthropic package not installed")

    if not ANTHROPIC_KEY:
        return _default_verdict("ANTHROPIC_API_KEY not set in .env")

    ec = edge_context or _DEFAULT_EDGE_CONTEXT
    model           = desk.get("agent.model", MODEL)
    max_tokens      = desk.get("agent.max_tokens", 2048)
    max_tokens_retry = desk.get("agent.max_tokens_retry", 1024)
    pinnacle_movement_threshold = desk.get("thresholds.pinnacle_move_agent_prompt", 0.03)

    cached_system = [
        {
            "type":          "text",
            "text":          desk.agent_system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    # Step 1: Pinnacle stability (fast check before burning API tokens)
    current_pinnacle, pinnacle_movement = _fetch_current_pinnacle(desk, game_dict)
    pinnacle_stable = (
        True if pinnacle_movement is None
        else pinnacle_movement < pinnacle_movement_threshold
    )

    # Step 2: Build research prompt
    user_msg = _build_user_message(
        desk, game_dict, current_pinnacle, pinnacle_movement, pinnacle_stable, ec
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

    def _make_api_call(msgs: list, max_tok: int = max_tokens, force_tool: bool = False):
        kwargs: dict = dict(
            model=model,
            max_tokens=max_tok,
            system=cached_system,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=msgs,
            betas=["web-search-2025-03-05"],
        )
        if force_tool:
            kwargs["tool_choice"] = {"type": "any"}
        return client.beta.messages.create(**kwargs)

    def _accumulate(resp) -> None:
        nonlocal total_input, total_output, total_cache, total_searches
        u = resp.usage
        total_input   += getattr(u, "input_tokens",  0)
        total_output  += getattr(u, "output_tokens", 0)
        total_cache   += getattr(u, "cache_read_input_tokens", 0)
        total_searches += sum(
            1 for b in resp.content
            if getattr(b, "type", "") == "tool_use"
            and getattr(b, "name", "") == "web_search"
        )

    try:
        # Turn 1: force web_search, collect results
        response = _make_api_call(messages, force_tool=True)

        # Agentic loop — process search tool calls and truncate results
        for _ in range(8):
            if response.stop_reason != "tool_use":
                break
            _accumulate(response)
            truncated = _truncate_content_blocks(response.content)
            messages.append({"role": "assistant", "content": truncated})
            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": []}
                for b in response.content
                if getattr(b, "type", "") == "tool_use"
            ]
            messages.append({"role": "user", "content": tool_results})
            response = _make_api_call(messages)

        # Accumulate usage for whatever the loop exited on
        _accumulate(response)

        # Turn 2: search results are now in context — ask for JSON verdict
        messages.append({"role": "assistant", "content": _truncate_content_blocks(response.content)})
        messages.append({"role": "user", "content": _build_verdict_prompt(desk, ec)})
        response = _make_api_call(messages)
        _accumulate(response)

        raw_text = "".join(getattr(b, "text", "") for b in response.content)
        verdict  = _parse_verdict(raw_text)

        # Retry on JSON parse failure
        if verdict is None:
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role":    "user",
                "content": "Your response was not valid JSON. Return ONLY the JSON object, no other text.",
            })
            retry = _make_api_call(messages, max_tok=max_tokens_retry)
            _accumulate(retry)
            retry_text = "".join(getattr(b, "text", "") for b in retry.content)
            verdict = _parse_verdict(retry_text)

        if verdict is None:
            return _default_verdict("JSON parse failed after retry")

        verdict["_raw_response"] = raw_text

        # Flatten nested schema → backward-compat flat dict
        verdict = _normalize_verdict(verdict, pinnacle_stable, pinnacle_movement)

        # News-age override: SKIP → TRADE when agent admits news is old and Pinnacle stable
        if (
            verdict.get("recommendation") == "SKIP"
            and verdict.get("news_age_estimate", "") in ("this week", "older")
            and verdict.get("pinnacle_stable", True)
        ):
            verdict["recommendation"] = "TRADE"
            verdict["confidence"]     = "MEDIUM"
            verdict["skip_reason"]    = None
            verdict["reasoning"]      = (
                "[auto-override] News is old and Pinnacle is stable — "
                "chronic condition already priced in. " + verdict.get("reasoning", "")
            )

        # Log and print cost (with new verdict fields)
        _print_and_log_cost(
            desk, game_label, total_input, total_output, total_cache, total_searches,
            recommendation=verdict.get("recommendation", ""),
            skip_reason=verdict.get("skip_reason"),
            news_age=verdict.get("news_age_estimate", ""),
        )

        return verdict

    except Exception as e:
        return _default_verdict(f"API error: {str(e)[:300]}")
