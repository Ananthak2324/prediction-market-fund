"""
agent/memory_agent.py

EdgeFund Intelligence Agent — institutional memory chatbot.

Loads all system data files once at init, caches them alongside the system
prompt as system blocks (both ephemeral-cached), then answers multi-turn
questions grounded entirely in that loaded context.

No web search — knowledge comes from data files only.
"""

import csv
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL     = "claude-sonnet-4-6"
COST_LOG  = os.path.join(BASE, "data", "agent_cost_log.csv")

PRICE_INPUT      = 3.00
PRICE_CACHE_READ = 0.30
PRICE_OUTPUT     = 15.00


_SYSTEM_PROMPT = """\
You are the EdgeFund Intelligence Agent — the complete institutional memory \
and expert brain of the EdgeFund prediction market trading system.

You have full access to every trade logged, every agent decision made, every \
system component's behavior, and all performance statistics.

You answer questions as the system expert:
- For trade decisions: cite specific agent reasoning, gap size, Pinnacle \
probability, news found
- For statistics: give actual numbers, explain p-values in plain English, be \
honest about sample size limitations
- For system architecture: explain how each component works and why it was \
built that way
- For performance: separate clean from contaminated trades, separate MLB from \
WNBA, always note statistical confidence level

Never fabricate data. If the data does not contain the answer, say exactly \
that and explain what data would be needed. Cite specific dates, trade IDs, \
and numbers whenever possible.

You are honest about limitations. 21 trades is a small sample. Say so when \
relevant. The edge is promising but not yet proven at statistical significance. \
Say so when asked.\
"""


class MemoryAgent:
    """Stateful chat agent backed by loaded system data files.

    Instantiate once per session — context is loaded at init and frozen.
    Call query() for each turn, passing the accumulated history.
    """

    def __init__(self) -> None:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package not installed")

        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        self._client = anthropic.Anthropic(api_key=key)
        context_text = self._load_context()
        self._system = [
            {"type": "text", "text": _SYSTEM_PROMPT,  "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": context_text,     "cache_control": {"type": "ephemeral"}},
        ]
        self.context_stats = _compute_stats(context_text)

    def query(self, question: str, history: list[dict]) -> str:
        """Answer a question in the context of prior conversation history."""
        messages = list(history) + [{"role": "user", "content": question}]
        response = self._client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=self._system,
            messages=messages,
        )
        _log_cost(question, response.usage)
        return response.content[0].text

    # ── Context assembly ──────────────────────────────────────────────────────

    def _load_context(self) -> str:
        ctx: dict = {"loaded_at": datetime.now(timezone.utc).isoformat()}

        ctx["system_overview"] = _read_text(os.path.join(BASE, "system_overview.md"))

        trades = _read_json(os.path.join(BASE, "data", "paper_trades.json"), default=[])
        if isinstance(trades, list):
            trades = sorted(trades, key=lambda t: t.get("snapshot_time", ""))
            ctx["paper_trades"] = trades[-50:]
        else:
            ctx["paper_trades"] = None

        skipped = _read_json(os.path.join(BASE, "data", "skipped_trades.json"), default=[])
        if isinstance(skipped, list):
            ctx["skipped_trades"] = skipped[-30:]
        else:
            ctx["skipped_trades"] = None

        ctx["performance_summary"] = _read_json(
            os.path.join(BASE, "data", "performance_summary.json"), default=None
        )

        ctx["agent_cost_log"] = _read_csv_tail(
            os.path.join(BASE, "data", "agent_cost_log.csv"), n=50
        )

        ctx["edge_discovery_latest"] = _read_latest_edge_discovery()

        ctx["gap_curve_analysis"] = _read_json(
            os.path.join(BASE, "data", "gap_curve_analysis.json"), default=None
        )

        ctx["funnel_log"] = _read_json(
            os.path.join(BASE, "data", "funnel_log.json"), default=None
        )

        return json.dumps(ctx, indent=2, default=str)


# ── Helpers (module-level so they're testable independently) ──────────────────

def _compute_stats(context_text: str) -> dict:
    stats: dict = {"n_trades": 0, "n_skipped": 0, "days_active": 0}
    try:
        ctx     = json.loads(context_text)
        trades  = ctx.get("paper_trades") or []
        skipped = ctx.get("skipped_trades") or []
        stats["n_trades"]  = len(trades)
        stats["n_skipped"] = len(skipped)
        if trades:
            earliest = min(
                (t.get("snapshot_time", "") for t in trades if t.get("snapshot_time")),
                default="",
            )
            if earliest:
                first_date = datetime.fromisoformat(earliest[:10])
                stats["days_active"] = (datetime.now() - first_date).days + 1
    except Exception:
        pass
    return stats


def _log_cost(question: str, usage) -> None:
    input_t  = getattr(usage, "input_tokens",            0)
    output_t = getattr(usage, "output_tokens",           0)
    cache_t  = getattr(usage, "cache_read_input_tokens", 0)
    cost = (
        (input_t  / 1_000_000) * PRICE_INPUT  +
        (cache_t  / 1_000_000) * PRICE_CACHE_READ +
        (output_t / 1_000_000) * PRICE_OUTPUT
    )
    label = f"[Intelligence] {question[:50]}"
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
            label,
            input_t,
            output_t,
            cache_t,
            0,
            round(cost, 6),
        ])


def _read_text(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return None


def _read_json(path: str, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _read_csv_tail(path: str, n: int = 50) -> list[dict] | None:
    try:
        rows: list[dict] = []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(dict(row))
        return rows[-n:]
    except Exception:
        return None


def _read_latest_edge_discovery() -> dict | None:
    out_dir = os.path.join(BASE, "outputs")
    try:
        files = sorted(
            f for f in os.listdir(out_dir)
            if f.startswith("edge_discovery_") and f.endswith(".json")
        )
        if not files:
            return None
        with open(os.path.join(out_dir, files[-1])) as f:
            data = json.load(f)
        return {
            "generated_at": data.get("generated_at"),
            "sport":        data.get("sport"),
            "candidates":   data.get("candidates", []),
        }
    except Exception:
        return None
