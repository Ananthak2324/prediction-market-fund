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
from core.desk_loader import get_active_desks
from agent.research_agent import MODEL, ANTHROPIC_KEY
from scripts.update_outcomes import build_summary

# THRESHOLDS/THRESHOLDS_FILE (agent/thresholds.json) were removed in the
# 2026-07-04 desk-config rebuild — thresholds now live per-desk in
# desks/<id>.yaml. This script pre-dates that rebuild and was never migrated
# (same class of bug as the other legacy scripts fixed during the rebuild) —
# fixed 2026-07-13 to read desk-namespaced data and desk.get("thresholds").

BASE          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR      = os.path.join(BASE, "data")
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


def gather_tier_signal_performance(desks) -> dict:
    """
    Per-desk tier_performance/signal_performance from build_summary() — fed
    to the audit LLM so its new tier_signal_verdicts (2026-07-16 addition)
    are grounded in real current status/EV/sample-size numbers, not just the
    taken/shadow aggregates the rest of this script already computes.

    Calls build_summary() once per desk with that desk's own trades AND its
    own shadow_trades.json passed explicitly (mirrors dashboard/app.py's
    load_summary() pattern) rather than relying on build_summary()'s
    SHADOW_FILE module-global default, which this script never sets.
    """
    out: dict = {}
    for desk in desks:
        tp = os.path.join(BASE, desk.paper_trades_path)
        trades = json.load(open(tp)) if os.path.exists(tp) else []
        sp = os.path.join(BASE, desk.shadow_trades_path)
        shadow = json.load(open(sp)) if os.path.exists(sp) else []
        summary = build_summary(trades, shadow_entries=shadow)
        out[desk.desk_id] = {
            "tier_performance":   summary.get("tier_performance", {}),
            "signal_performance": summary.get("signal_performance", {}),
        }
    return out


def aggregate_cost(cost_log_path: str, cutoff: datetime) -> float:
    if not os.path.exists(cost_log_path):
        return 0.0
    total = 0.0
    with open(cost_log_path) as f:
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

CURRENT THRESHOLDS (desks/base.yaml's thresholds block — these gate the TRADE/SKIP decision):
{thresholds_json}

CURRENT TIER/SIGNAL STATUS AND PERFORMANCE, PER DESK (from build_summary(), current as of this audit):
{tier_signal_json}

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
4. For EVERY tier and signal shown in the per-desk status above, give an explicit verdict: reuse its \
current status string (ACTIVE_FULL, ACTIVE_REDUCED, or SHADOW_ONLY) if you see no reason to change it, \
or propose DOWNGRADE (move to reduced sizing or shadow-only) or KILL (stop shadow-tracking entirely, \
no further value in continuing to watch it) if the evidence supports it. Apply the same sample-size \
discipline as above — do not propose DOWNGRADE/KILL on a thin sample; use the existing status as the \
verdict and say so in the reason.

Return ONLY this JSON object, no markdown fences, no preamble:
{{
  "assessment": "2-4 sentence plain-English verdict on SKIP/TRADE calibration this week",
  "sample_size_note": "one sentence on whether this week's volume is sufficient to act on",
  "proposals": [
    {{"threshold": "tier_b_min_gap", "current_value": 0.10, "proposed_value": 0.08, "rationale": "..."}}
  ],
  "tier_signal_verdicts": [
    {{"scope": "tier", "id": "C", "current_status": "SHADOW_ONLY", "verdict": "KILL",
      "reason": "...", "sample_size": 0, "confidence": "low"}},
    {{"scope": "signal", "id": "BUY_NO", "current_status": "SHADOW_ONLY", "verdict": "DOWNGRADE",
      "reason": "...", "sample_size": 8, "confidence": "low"}}
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


def run_audit_llm(taken: dict, skipped: dict, cost: float, thresholds: dict, tier_signal_performance: dict) -> dict:
    if not ANTHROPIC_KEY:
        return {
            "assessment":       "LLM audit skipped — ANTHROPIC_API_KEY not set.",
            "sample_size_note": "n/a",
            "proposals":        [],
            "tier_signal_verdicts": [],
            "_error":           "no_api_key",
        }
    try:
        import anthropic as _anthropic
    except ImportError:
        return {
            "assessment":       "LLM audit skipped — anthropic package not installed.",
            "sample_size_note": "n/a",
            "proposals":        [],
            "tier_signal_verdicts": [],
            "_error":           "no_anthropic_package",
        }

    prompt = _AUDIT_PROMPT_TEMPLATE.format(
        window_days=WINDOW_DAYS,
        taken_json=json.dumps(taken, indent=2),
        skipped_json=json.dumps(skipped, indent=2),
        cost=cost,
        thresholds_json=json.dumps(thresholds, indent=2),
        tier_signal_json=json.dumps(tier_signal_performance, indent=2),
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
                "tier_signal_verdicts": [],
                "_error":           "parse_failed",
                "_raw_response":    raw_text,
            }
        parsed.setdefault("proposals", [])
        parsed.setdefault("tier_signal_verdicts", [])
        return parsed
    except Exception as e:
        return {
            "assessment":       f"LLM audit failed — {e}",
            "sample_size_note": "n/a",
            "proposals":        [],
            "tier_signal_verdicts": [],
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

    desks = get_active_desks()

    trades: list[dict]  = []
    skipped: list[dict] = []
    cost = 0.0
    for desk in desks:
        tp = os.path.join(BASE, desk.paper_trades_path)
        if os.path.exists(tp):
            trades.extend(json.load(open(tp)))
        sp = os.path.join(BASE, desk.skipped_trades_path)
        if os.path.exists(sp):
            skipped.extend(json.load(open(sp)))
        cost += aggregate_cost(os.path.join(BASE, desk.agent_cost_log_path), cutoff)
    cost = round(cost, 4)

    # Thresholds live in desks/base.yaml, shared identically across all active
    # desks today — use the first desk's view as representative.
    thresholds = desks[0].get("thresholds", {}) if desks else {}
    tier_signal_performance = gather_tier_signal_performance(desks)

    taken_stats   = aggregate_taken(trades, cutoff)
    skipped_stats = aggregate_skipped(skipped, cutoff)

    print(f"Taken:   {taken_stats}")
    print(f"Skipped: {skipped_stats}")
    print(f"Weekly agent cost: ${cost:.2f}")

    audit = run_audit_llm(taken_stats, skipped_stats, cost, thresholds, tier_signal_performance)
    print(f"\nAssessment: {audit.get('assessment')}")
    print(f"Sample size note: {audit.get('sample_size_note')}")
    print(f"Proposals: {json.dumps(audit.get('proposals'), indent=2)}")
    print(f"Tier/signal verdicts: {json.dumps(audit.get('tier_signal_verdicts'), indent=2)}")

    period_start = cutoff.date().isoformat()
    period_end   = now.date().isoformat()

    report = {
        "period_start":     period_start,
        "period_end":       period_end,
        "generated_at":     now.isoformat(),
        "taken":            taken_stats,
        "skipped":          skipped_stats,
        "weekly_cost_usd":  cost,
        "thresholds_at_audit_time": thresholds,
        "thresholds_source": "desks/base.yaml",
        "tier_signal_performance_at_audit_time": tier_signal_performance,
        "assessment":       audit.get("assessment"),
        "sample_size_note": audit.get("sample_size_note"),
        "proposals":        audit.get("proposals", []),
        "tier_signal_verdicts": audit.get("tier_signal_verdicts", []),
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
