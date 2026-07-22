"""
mission_control/watcher.py

Read-only event-source watcher for the "Mission Control" live agent
visualization. Polls the existing trading-pipeline data files and the
edge-discovery stdout log for new entries, and emits structured events —
never writes to, modifies, or interferes with any live trading file, signal
gate, Kelly sizing, or systemd-managed process. Opens every file read-only.

Two independent sources, because the pipeline doesn't record a distinct
"Scout found this" event anywhere in its JSON data files (checked, not
assumed — see mission_control/README.md for the full writeup):

1. Scout events — tailed from data/snapshots/edge_discovery_out.log, the
   shared stdout log every edge-discovery cycle appends to (both desks,
   interleaved). The line `  → {game} — {team} ({signal}, gap=...) [...]`
   fires in agent/edge_discovery_agent.py's _run_sport() loop the instant a
   candidate is handed to the research agent, before any verdict exists.

2. Analyst events — polled from each active desk's paper_trades.json /
   skipped_trades.json / shadow_trades.json. All three are full-array
   rewrites on every write (not append-only, not JSONL), so "new" is
   detected by diffing against a set of already-seen unique keys
   (trade_id for paper_trades, event_ticker+skipped_at for skipped,
   event_ticker+shadowed_at for shadow) rather than tailing bytes.

Auditor (weekly_audit_*.json) and Ledger (feedback_queue.json) events are
included too, behind the same poll-and-diff pattern, for when demo scope
extends past Scout+Analyst.

Usage (standalone, for testing without the server):
    python -m mission_control.watcher
"""

import glob
import json
import os
import re
import sys
import time
from queue import Queue
from threading import Thread

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.desk_loader import get_active_desks

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDGE_DISCOVERY_LOG = os.path.join(BASE, "data", "snapshots", "edge_discovery_out.log")
AUDITS_DIR = os.path.join(BASE, "data", "audits")

POLL_SECONDS = 2.0

_SCOUT_LINE_RE = re.compile(
    r"^\s*→\s*(?P<game>.+?)\s*—\s*(?P<team>.+?)\s*"
    r"\((?P<signal>BUY_YES|BUY_NO),\s*gap=(?P<gap>-?[\d.]+)%\s*vs\s*(?P<book>\w+)\)\s*"
    r"\[(?P<edge_type>\w+),\s*lean=(?P<lean>\w+)\]"
)
_DESK_START_RE = re.compile(r"desk=(?P<desk>\w+)")


class Watcher:
    """
    Polls data sources on a background thread and pushes structured event
    dicts onto `self.events` (a stdlib Queue) for the SSE server to consume.
    Read-only throughout — every open() call is default ("r") mode.
    """

    def __init__(self) -> None:
        self.events: "Queue[dict]" = Queue()
        self._seen_paper_ids: dict[str, set] = {}
        self._seen_skip_ids: dict[str, set] = {}
        self._seen_shadow_ids: dict[str, set] = {}
        self._seen_audit_files: set[str] = set()
        self._seen_feedback_ids: set[str] = set()
        self._log_offset = 0
        self._current_desk = "?"
        self._running = False

    # ── Scout: tail the edge-discovery stdout log ──────────────────────────

    def _poll_scout_log(self) -> None:
        if not os.path.exists(EDGE_DISCOVERY_LOG):
            return
        with open(EDGE_DISCOVERY_LOG) as f:  # read-only
            f.seek(self._log_offset)
            new_lines = f.readlines()
            self._log_offset = f.tell()

        for line in new_lines:
            desk_match = _DESK_START_RE.search(line)
            if desk_match:
                self._current_desk = desk_match.group("desk")
                continue
            m = _SCOUT_LINE_RE.search(line)
            if not m:
                continue
            self.events.put({
                "agent": "Scout",
                "action": "FOUND",
                "desk": self._current_desk,
                "game": m.group("game"),
                "team": m.group("team"),
                "signal": m.group("signal"),
                "gap_pct": float(m.group("gap")),
                "book": m.group("book"),
                "edge_type": m.group("edge_type"),
                "lean": m.group("lean"),
                "ts": time.time(),
            })

    # ── Analyst: poll each desk's trade/skip/shadow files ──────────────────

    def _poll_analyst_files(self) -> None:
        for desk in get_active_desks():
            self._poll_paper_trades(desk)
            self._poll_skipped_trades(desk)
            self._poll_shadow_trades(desk)

    def _load_json(self, path: str, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path) as f:  # read-only
                return json.load(f)
        except (json.JSONDecodeError, ValueError, OSError):
            return default

    def _poll_paper_trades(self, desk) -> None:
        path = os.path.join(BASE, desk.paper_trades_path)
        trades = self._load_json(path, [])
        seen = self._seen_paper_ids.setdefault(desk.desk_id, set())
        for t in trades:
            tid = t.get("trade_id")
            if tid is None or tid in seen:
                continue
            seen.add(tid)
            self.events.put({
                "agent": "Analyst",
                "action": "TRADE",
                "desk": desk.desk_id,
                "game": t.get("game"),
                "team": t.get("team"),
                "signal": t.get("signal"),
                "gap_pct": round((t.get("abs_gap") or 0) * 100, 2),
                "tier": t.get("tier"),
                "verdict_confidence": t.get("agent_confidence"),
                "ts": time.time(),
            })

    def _poll_skipped_trades(self, desk) -> None:
        path = os.path.join(BASE, desk.skipped_trades_path)
        skipped = self._load_json(path, [])
        seen = self._seen_skip_ids.setdefault(desk.desk_id, set())
        for s in skipped:
            key = (s.get("event_ticker"), s.get("skipped_at"))
            if key[1] is None or key in seen:
                continue
            seen.add(key)
            self.events.put({
                "agent": "Analyst",
                "action": "SKIP",
                "desk": desk.desk_id,
                "game": s.get("game"),
                "team": s.get("team"),
                "gap_pct": round((s.get("abs_gap") or 0) * 100, 2),
                "reason": (s.get("skip_reason") or s.get("agent_reasoning") or "")[:140],
                "ts": time.time(),
            })

    def _poll_shadow_trades(self, desk) -> None:
        path = os.path.join(BASE, desk.shadow_trades_path)
        shadow = self._load_json(path, [])
        seen = self._seen_shadow_ids.setdefault(desk.desk_id, set())
        for s in shadow:
            key = (s.get("event_ticker"), s.get("shadowed_at"))
            if key[1] is None or key in seen:
                continue
            seen.add(key)
            self.events.put({
                "agent": "Analyst",
                "action": "SHADOW",
                "desk": desk.desk_id,
                "game": s.get("game"),
                "team": s.get("team"),
                "gap_pct": round((s.get("abs_gap") or 0) * 100, 2),
                "tier": s.get("tier"),
                "reason": (s.get("shadow_reason") or "")[:140],
                "ts": time.time(),
            })

    # ── Auditor + Ledger (stretch goal, same poll-and-diff pattern) ────────

    def _poll_auditor(self) -> None:
        for path in sorted(glob.glob(os.path.join(AUDITS_DIR, "weekly_audit_*.json"))):
            if path in self._seen_audit_files:
                continue
            self._seen_audit_files.add(path)
            report = self._load_json(path, {})
            if not report:
                continue
            self.events.put({
                "agent": "Auditor",
                "action": "AUDIT",
                "period_end": report.get("period_end"),
                "assessment": (report.get("assessment") or "")[:200],
                "n_verdicts": len(report.get("tier_signal_verdicts", [])),
                "ts": time.time(),
            })

    def _poll_ledger(self) -> None:
        path = os.path.join(BASE, "data", "audits", "feedback_queue.json")
        queue = self._load_json(path, [])
        for entry in queue:
            eid = entry.get("id")
            if eid is None or eid in self._seen_feedback_ids:
                continue
            self._seen_feedback_ids.add(eid)
            self.events.put({
                "agent": "Ledger",
                "action": entry.get("verdict", "NOTE"),
                "target": f"{entry.get('scope')} {entry.get('target_id')}",
                "note": (entry.get("proposed_note") or "")[:200],
                "status": entry.get("status"),
                "ts": time.time(),
            })

    # ── main loop ────────────────────────────────────────────────────────

    def _prime_baselines(self) -> None:
        """
        On startup, mark every currently-existing record as already-seen
        (and seek the log to EOF) so the first poll doesn't replay the
        entire trade history as a burst of "new" events.
        """
        if os.path.exists(EDGE_DISCOVERY_LOG):
            with open(EDGE_DISCOVERY_LOG) as f:
                f.seek(0, os.SEEK_END)
                self._log_offset = f.tell()
        for desk in get_active_desks():
            trades = self._load_json(os.path.join(BASE, desk.paper_trades_path), [])
            self._seen_paper_ids[desk.desk_id] = {t.get("trade_id") for t in trades}
            skipped = self._load_json(os.path.join(BASE, desk.skipped_trades_path), [])
            self._seen_skip_ids[desk.desk_id] = {(s.get("event_ticker"), s.get("skipped_at")) for s in skipped}
            shadow = self._load_json(os.path.join(BASE, desk.shadow_trades_path), [])
            self._seen_shadow_ids[desk.desk_id] = {(s.get("event_ticker"), s.get("shadowed_at")) for s in shadow}
        self._seen_audit_files = set(glob.glob(os.path.join(AUDITS_DIR, "weekly_audit_*.json")))
        feedback = self._load_json(os.path.join(BASE, "data", "audits", "feedback_queue.json"), [])
        self._seen_feedback_ids = {e.get("id") for e in feedback}

    def run_forever(self) -> None:
        self._prime_baselines()
        self._running = True
        while self._running:
            try:
                self._poll_scout_log()
                self._poll_analyst_files()
                self._poll_auditor()
                self._poll_ledger()
            except Exception as e:
                self.events.put({"agent": "System", "action": "WATCHER_ERROR", "error": str(e), "ts": time.time()})
            time.sleep(POLL_SECONDS)

    def start_background(self) -> Thread:
        t = Thread(target=self.run_forever, daemon=True)
        t.start()
        return t


if __name__ == "__main__":
    w = Watcher()
    w._prime_baselines()
    print(f"Watching {EDGE_DISCOVERY_LOG} and desk data files (Ctrl+C to stop)...")
    w._running = True
    try:
        while True:
            w._poll_scout_log()
            w._poll_analyst_files()
            w._poll_auditor()
            w._poll_ledger()
            while not w.events.empty():
                print(json.dumps(w.events.get(), indent=2, default=str))
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        pass
