"""
weekly_audit.py

Runs weekly (Sunday 11 PM via LaunchAgent/systemd timer). Reviews the
trailing 7 days of taken trades and shadow (skipped) trades, asks Claude
to judge SKIP/TRADE calibration using resolved shadow outcomes, and
proposes 0-2 changes to agent/thresholds.json for manual review.

Never auto-applies changes — output is a report + proposals only.

Usage:
    python scripts/weekly_audit.py
    python scripts/weekly_audit.py --dry-run   # don't write report or send iMessage
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from core.notifications import send_imessage
from agent.research_agent import MODEL, ANTHROPIC_KEY, THRESHOLDS, THRESHOLDS_FILE

DATA_DIR      = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TRADES_FILE   = os.path.join(DATA_DIR, "paper_trades.json")
SKIPPED_FILE  = os.path.join(DATA_DIR, "skipped_trades.json")
COST_LOG      = os.path.join(DATA_DIR, "agent_cost_log.csv")
AUDITS_DIR    = os.path.join(DATA_DIR, "audits")

WINDOW_DAYS         = 7
MIN_SAMPLE_FOR_TUNE = 30   # below this resolved count, the LLM is told to lean toward no changes


# ── time parsing ──────────────────────────────────────────────────────────────

def _parse_snapshot_dt(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_iso_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ── aggregation ────────────────────────────────────────────────────────────────

def aggregate_taken(trades: list[dict], cutoff: datetime) -> dict:
    in_window = [t for t in trades if (_parse_snapshot_dt(t.get("snapshot_time", "")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
    resolved  = [t for t in in_window if t.get("outcome") is not None]
    wins      = sum(1 for t in resolved if t["outcome"] == "WIN")
    losses    = sum(1 for t in resolved if t["outcome"] == "LOSS")
    return {
        "total":    len(in_window),
        "resolved": len(resolved),
        "wins":     wins,
        "losses":   losses,
        "open":     len(in_window) - len(resolved),
        "win_rate": round(wins / len(resolved), 4) if resolved else None,
    }


def aggregate_skipped(skipped: list[dict], cutoff: datetime) -> dict:
    in_window = [s for s in skipped if (_parse_iso_dt(s.get("skipped_at", "")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
    resolved  = [s for s in in_window if s.get("shadow_resolved_at")]
    wins      = sum(1 for s in resolved if s.get("shadow_correct") is True)
    losses    = sum(1 for s in resolved if s.get("shadow_correct") is False)

    by_trigger = {"news_found": 0, "pinnacle_unstable": 0, "weather_issue": 0}
    for s in in_window:
        if s.get("news_found"):
            by_trigger["news_found"] += 1
        if s.get("pinnacle_stable") is False:
            by_trigger["pinnacle_unstable"] += 1
        if s.get("weather_issue"):
            by_trigger["weather_issue"] += 1

    return {
        "total":      len(in_window),
        "resolved":   len(resolved),
        "wins":       wins,
        "losses":     losses,
        "pending":    len(in_window) - len(resolved),
        "win_rate":   round(wins / len(resolved), 4) if resolved else None,
        "by_trigger": by_trigger,
    }


def aggregate_cost(cutoff: datetime) -> float:
    if not os.path.exists(COST_LOG):
        return 0.0
    total = 0.0
    with open(COST_LOG) as f:
        for row in csv.DictReader(f):
            try:
                ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if ts >= cutoff:
                total += float(row.get("estimated_cost_usd", 0) or 0)
    return round(total, 4)


# ── LLM audit ────────────────────────────────────────────────────────────────

_AUDIT_PROMPT_TEMPLATE = """You are auditing a fully automated sports prediction-market trading system's \
weekly decision calibration. The system flags pricing gaps between Kalshi and Pinnacle, then a research \
agent decides TRADE / SKIP / MONITOR per game. TRADE positions become real (sandboxed paper) trades. \
SKIPped games are tracked as a "shadow portfolio" — what would have happened if the agent had traded \
them anyway — purely to measure whether the SKIP filter is adding value.

TRAILING {window_days}-DAY STATS:

Taken trades (agent said TRADE/MONITOR and a position was opened):
{taken_json}

Shadow trades (agent said SKIP, outcome tracked but not traded):
{skipped_json}

Weekly research-agent API cost: ${cost:.2f}

CURRENT THRESHOLDS (agent/thresholds.json — these gate the TRADE/SKIP decision):
{thresholds_json}

TASK:
1. Assess whether SKIP decisions were net-correct this week by comparing the shadow win rate to the \
taken win rate. A SKIP filter is working if shadow win rate is meaningfully lower than taken win rate.
2. Propose 0-2 highest-leverage changes to the thresholds above, each with a specific new value and a \
one-sentence rationale grounded in the stats above. Do not propose a change you can't justify from this \
week's numbers.
3. Sample size discipline: each bucket has fewer than {min_sample} resolved outcomes if the resolved count \
above is below that line. With small samples, lean strongly toward proposing NO changes — explicitly say \
in sample_size_note that the data is too thin to act on, rather than overfitting to noise. It is correct \
and expected for proposals to be empty most weeks at this trade volume.

Return ONLY this JSON object, no markdown fences, no preamble:
{{
  "assessment": "2-4 sentence plain-English verdict on SKIP/TRADE calibration this week",
  "sample_size_note": "one sentence on whether this week's volume is sufficient to act on",
  "proposals": [
    {{"threshold": "tier_b_min_gap", "current_value": 0.10, "proposed_value": 0.08, "rationale": "..."}}
  ]
}}
"""


def _parse_json_response(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
        return None


def run_audit_llm(taken: dict, skipped: dict, cost: float) -> dict:
    if not ANTHROPIC_KEY:
        return {
            "assessment":       "LLM audit skipped — ANTHROPIC_API_KEY not set.",
            "sample_size_note": "n/a",
            "proposals":        [],
            "_error":           "no_api_key",
        }
    try:
        import anthropic as _anthropic
    except ImportError:
        return {
            "assessment":       "LLM audit skipped — anthropic package not installed.",
            "sample_size_note": "n/a",
            "proposals":        [],
            "_error":           "no_anthropic_package",
        }

    prompt = _AUDIT_PROMPT_TEMPLATE.format(
        window_days=WINDOW_DAYS,
        taken_json=json.dumps(taken, indent=2),
        skipped_json=json.dumps(skipped, indent=2),
        cost=cost,
        thresholds_json=json.dumps(THRESHOLDS, indent=2),
        min_sample=MIN_SAMPLE_FOR_TUNE,
    )

    try:
        client   = _anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = "".join(getattr(b, "text", "") for b in response.content)
        parsed   = _parse_json_response(raw_text)
        if parsed is None:
            return {
                "assessment":       "LLM audit failed — could not parse response as JSON.",
                "sample_size_note": "n/a",
                "proposals":        [],
                "_error":           "parse_failed",
                "_raw_response":    raw_text,
            }
        parsed.setdefault("proposals", [])
        return parsed
    except Exception as e:
        return {
            "assessment":       f"LLM audit failed — {e}",
            "sample_size_note": "n/a",
            "proposals":        [],
            "_error":           "api_call_failed",
        }


# ── output formatting ───────────────────────────────────────────────────────────

def format_digest(period_start: str, period_end: str, taken: dict, skipped: dict, audit: dict) -> str:
    taken_wr   = f"{taken['win_rate']:.1%}" if taken["win_rate"] is not None else "n/a"
    skipped_wr = f"{skipped['win_rate']:.1%}" if skipped["win_rate"] is not None else "n/a"

    lines = [
        f"\U0001F4CB WEEKLY AUDIT — {period_start} to {period_end}",
        f"Taken: {taken['resolved']} resolved ({taken['wins']}W/{taken['losses']}L = {taken_wr})",
        f"Shadow: {skipped['resolved']} resolved ({skipped['wins']}W/{skipped['losses']}L = {skipped_wr})",
    ]

    proposals = audit.get("proposals") or []
    if proposals:
        top = proposals[0]
        lines.append(
            f"Top proposal: {top.get('threshold')} {top.get('current_value')} → {top.get('proposed_value')}"
        )
        lines.append(f"  {top.get('rationale', '')}")
    else:
        lines.append("No threshold changes proposed this week.")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=WINDOW_DAYS)

    trades  = json.load(open(TRADES_FILE)) if os.path.exists(TRADES_FILE) else []
    skipped = json.load(open(SKIPPED_FILE)) if os.path.exists(SKIPPED_FILE) else []

    taken_stats   = aggregate_taken(trades, cutoff)
    skipped_stats = aggregate_skipped(skipped, cutoff)
    cost          = aggregate_cost(cutoff)

    print(f"Taken:   {taken_stats}")
    print(f"Skipped: {skipped_stats}")
    print(f"Weekly agent cost: ${cost:.2f}")

    audit = run_audit_llm(taken_stats, skipped_stats, cost)
    print(f"\nAssessment: {audit.get('assessment')}")
    print(f"Sample size note: {audit.get('sample_size_note')}")
    print(f"Proposals: {json.dumps(audit.get('proposals'), indent=2)}")

    period_start = cutoff.date().isoformat()
    period_end   = now.date().isoformat()

    report = {
        "period_start":     period_start,
        "period_end":       period_end,
        "generated_at":     now.isoformat(),
        "taken":            taken_stats,
        "skipped":          skipped_stats,
        "weekly_cost_usd":  cost,
        "thresholds_at_audit_time": THRESHOLDS,
        "thresholds_file":  THRESHOLDS_FILE,
        "assessment":       audit.get("assessment"),
        "sample_size_note": audit.get("sample_size_note"),
        "proposals":        audit.get("proposals", []),
    }

    message = format_digest(period_start, period_end, taken_stats, skipped_stats, audit)
    print(f"\n{message}")

    if args.dry_run:
        print("\n[dry-run] Report not written, iMessage not sent.")
        return

    os.makedirs(AUDITS_DIR, exist_ok=True)
    report_path = os.path.join(AUDITS_DIR, f"weekly_audit_{period_end}.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved → {report_path}")

    sent = send_imessage(message)
    print(f"  [NOTIFY] sent={sent}")


if __name__ == "__main__":
    main()
