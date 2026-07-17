"""
agent/feedback_loop_agent.py

Closes the loop between scripts/weekly_audit.py's KILL/DOWNGRADE verdicts
and actual follow-up — confirmed via grep before this was built that
weekly_audit.py's report was write-only: generated every Sunday, saved to
data/audits/weekly_audit_*.json, and never read back by anything.

This agent is the first consumer. For each KILL/DOWNGRADE verdict in the
most recent audit report, it drafts one human-readable note (what was
killed/downgraded, the audit's stated reason, 1-2 concrete next-hypothesis
suggestions) and appends it to a PENDING_REVIEW queue. It never applies any
change itself — see scripts/feedback_queue_cli.py for the human
approve/reject step. This is explicitly NOT an autonomous learning loop:
no code path here edits desks/*.yaml, restarts a service, or otherwise acts
on a verdict without a human reading and approving it first.

Modeled on agent/memory_agent.py's MemoryAgent (simple client.messages.create()
call, ephemeral system-prompt caching, no web-search tools) but stateless/
single-shot rather than a multi-turn chat, since this runs unattended via
systemd once a week.

Usage:
    python agent/feedback_loop_agent.py
"""

import glob
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

BASE       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDITS_DIR = os.path.join(BASE, "data", "audits")
QUEUE_FILE = os.path.join(AUDITS_DIR, "feedback_queue.json")
MODEL      = "claude-sonnet-4-6"

_SYSTEM_PROMPT = """\
You are the EdgeFund feedback-loop drafting agent. Once a week, the audit \
agent reviews tier and signal performance and may issue a KILL verdict \
(stop tracking entirely — no further value in continuing) or a DOWNGRADE \
verdict (move to reduced sizing or shadow-only) for a specific tier or \
signal. Your job is to draft one clear, human-readable note per verdict: \
state what was killed/downgraded, the audit's stated reason in your own \
words, and 1-2 concrete, specific next hypotheses worth testing given that \
reason (e.g. a different gap threshold to try, a different signal to \
watch, a data quality issue to investigate first).

You are drafting a recommendation for a human to approve or reject — never \
claim a change has been applied, never invent numbers not present in the \
audit data you're given, and be explicit when a suggestion is speculative \
versus grounded in the stated numbers.

Return ONLY a JSON array, no markdown fences, no preamble:
[
  {"target": "tier C" or "signal BUY_NO" (matches the verdict's scope/id),
   "proposed_note": "2-4 sentence note: what was killed/downgraded, why per \
the audit, and 1-2 concrete next hypotheses to test"}
]
"""


def load_latest_audit() -> dict | None:
    """Globs data/audits/weekly_audit_*.json, returns the most recent by
    generated_at (falls back to filename sort if that's missing)."""
    paths = sorted(glob.glob(os.path.join(AUDITS_DIR, "weekly_audit_*.json")))
    if not paths:
        return None
    reports = []
    for p in paths:
        try:
            with open(p) as f:
                reports.append((p, json.load(f)))
        except (json.JSONDecodeError, ValueError, OSError):
            continue
    if not reports:
        return None
    reports.sort(key=lambda pr: pr[1].get("generated_at", pr[0]))
    return reports[-1][1]


class FeedbackLoopAgent:
    def __init__(self) -> None:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package not installed")

        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        self._client = anthropic.Anthropic(api_key=key, timeout=90.0, max_retries=1)
        self._system = [
            {"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        ]

    def draft_notes(self, audit_report: dict) -> list[dict]:
        """
        For each KILL/DOWNGRADE verdict in audit_report['tier_signal_verdicts'],
        drafts a queue-ready entry. Returns [] if there are none — no LLM
        call is made in that case.
        """
        verdicts = [
            v for v in audit_report.get("tier_signal_verdicts", [])
            if v.get("verdict") in ("KILL", "DOWNGRADE")
        ]
        if not verdicts:
            return []

        user_message = (
            f"Audit date: {audit_report.get('period_end', '?')}\n\n"
            f"KILL/DOWNGRADE verdicts this week:\n"
            f"{json.dumps(verdicts, indent=2)}\n\n"
            f"Full audit assessment for context:\n{audit_report.get('assessment', '')}"
        )
        response = self._client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=self._system,
            messages=[{"role": "user", "content": user_message}],
        )
        raw_text = "".join(getattr(b, "text", "") for b in response.content)
        drafted = _parse_json_array(raw_text)
        if drafted is None:
            # Never silently drop a real verdict — fall back to a minimal
            # note built from the verdict's own fields rather than losing it.
            drafted = [
                {"target": f"{v['scope']} {v['id']}", "proposed_note": v.get("reason", "(no reason given)")}
                for v in verdicts
            ]

        entries = []
        now = datetime.now(timezone.utc)
        for v, d in zip(verdicts, drafted):
            entries.append({
                "id": f"fb_{audit_report.get('period_end', now.date().isoformat())}_{v['scope']}-{v['id']}",
                "source_audit_date": audit_report.get("period_end"),
                "scope": v["scope"],
                "target_id": v["id"],
                "current_status": v.get("current_status"),
                "verdict": v["verdict"],
                "reason": v.get("reason"),
                "proposed_note": d.get("proposed_note", ""),
                "sample_size": v.get("sample_size"),
                "confidence": v.get("confidence"),
                "status": "PENDING_REVIEW",
                "created_at": now.isoformat(),
                "reviewed_at": None,
                "reviewed_by": None,
                "review_note": None,
            })
        return entries


def _parse_json_array(text: str) -> list[dict] | None:
    import re
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else None
    except Exception:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, list) else None
            except Exception:
                return None
        return None


def _existing_keys(queue: list[dict]) -> set[tuple]:
    return {(e.get("scope"), e.get("target_id"), e.get("source_audit_date")) for e in queue}


def append_to_queue(entries: list[dict]) -> int:
    """
    Appends new entries to QUEUE_FILE, skipping any whose
    (scope, target_id, source_audit_date) already exists — idempotent so
    rerunning against the same week's audit doesn't duplicate entries.
    Returns the number of entries actually appended.
    """
    os.makedirs(AUDITS_DIR, exist_ok=True)
    queue: list[dict] = []
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE) as f:
                queue = json.load(f)
        except (json.JSONDecodeError, ValueError):
            queue = []

    seen = _existing_keys(queue)
    added = 0
    for e in entries:
        key = (e["scope"], e["target_id"], e["source_audit_date"])
        if key in seen:
            continue
        queue.append(e)
        seen.add(key)
        added += 1

    if added:
        with open(QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2)
    return added


def main() -> None:
    audit = load_latest_audit()
    if audit is None:
        print("No audit report found in data/audits/ — nothing to process.")
        return

    verdicts = [
        v for v in audit.get("tier_signal_verdicts", [])
        if v.get("verdict") in ("KILL", "DOWNGRADE")
    ]
    if not verdicts:
        print(f"Latest audit ({audit.get('period_end')}) has no KILL/DOWNGRADE verdicts — queue unchanged.")
        return

    agent = FeedbackLoopAgent()
    drafted = agent.draft_notes(audit)
    added = append_to_queue(drafted)

    if added:
        print(f"Queued {added} new PENDING_REVIEW item(s) → {QUEUE_FILE}")
    else:
        print("All verdicts from this audit were already queued — no duplicates added.")


if __name__ == "__main__":
    main()
