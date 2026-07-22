# Mission Control — Live Agent Visualization

Read-only observability layer for a demo video — shows the agent pipeline
firing in real time, by name, with real data. Never writes to, modifies, or
interferes with any live trading file, signal gate, Kelly sizing, or
systemd-managed process. Runs as its own standalone process, fully separate
from `dashboard/app.py` (Streamlit's rerun model isn't built for pushed
real-time events) and from every existing systemd unit.

## Scope (demo, 2026-07-22)

Two stations built and verified end-to-end: **Scout** (`edge_discovery_agent.py`
— finds candidates, computes gaps) and **Analyst** (`research_agent.run()` —
the AI barrier, verdicts each candidate). **Auditor** (`weekly_audit.py`) and
**Ledger** (`feedback_loop_agent.py`) are wired in the watcher/backend already
(same poll-and-diff pattern) but shown dimmed in the UI until there's a real
event to demo them with — Auditor only fires Sunday nights, Ledger only on a
KILL/DOWNGRADE verdict.

## How it works

**Two independent event sources**, because the pipeline doesn't record a
distinct "Scout found this" moment anywhere in its JSON data files (checked,
not assumed):

1. **Scout** — tails `data/snapshots/edge_discovery_out.log` (the shared
   stdout log every edge-discovery cycle appends to) for the line
   `  → {game} — {team} (...)`, which fires the instant a candidate is
   handed to the research agent, before any verdict exists. This line only
   ever fires during a real research-enabled run — there's no way to see a
   Scout event without a real (paid) research call happening, since Scout
   and Analyst are inseparable in the current pipeline. This is expected
   given the demo's small scope; see "Migration note" below for how a future
   schema could decouple them.
2. **Analyst** — polls each active desk's `paper_trades.json` /
   `skipped_trades.json` / `shadow_trades.json` every 2 seconds. All three
   are full-array rewrites on every write (confirmed by reading
   `agent/edge_discovery_agent.py::_log_edge_trades()` — not append-only,
   not JSONL), so "new" is detected by diffing against a set of
   already-seen unique keys (`trade_id` for trades, `(event_ticker,
   skipped_at)` for skips, `(event_ticker, shadowed_at)` for shadow) rather
   than tailing bytes.

Every file is opened in default (`"r"`) read-only mode. The watcher never
calls `open(..., "w")` on anything under `data/`.

## Running it

```bash
# from the project root
python -m mission_control.server            # http://localhost:8765
python -m mission_control.server --port 9000 --host 0.0.0.0
```

Then open the URL in a browser. No new dependencies — built entirely on the
stdlib (`http.server`), no `fastapi`/`uvicorn` install needed.

Standalone watcher test (prints events to stdout, no server):
```bash
python -m mission_control.watcher
```

### Running during the demo

The real Scout+Analyst sequence only appears during an actual edge-discovery
cycle (every 30 min on the VPS, `prediction-fund-edge-discovery.timer`). For
a live demo:

- **On the VPS** (recommended — this is where the real schedule runs):
  `python -m mission_control.server --port 8765` in a separate terminal/tmux
  session (deliberately **not** added as a systemd unit per the brief — keep
  it a fully separate, easily-killable process while it's demo-only). Then
  either wait for the next real 30-min cycle, or manually fire one:
  `sudo systemctl start prediction-fund-edge-discovery.service` (this is a
  real, already-budgeted trading cycle either way — running Mission Control
  alongside it costs nothing extra since the research-agent spend happens
  regardless of whether anyone's watching).
- **Locally**: same command, but you'd need to trigger a real (paid) local
  edge-discovery run to see genuine events — not recommended purely to test
  the demo. The watcher/server logic was verified with a synthetic
  real-format line and an isolated test file instead (see verification
  notes below), so the plumbing itself is proven without spending anything
  extra.

## Verification performed

- Regex (`_SCOUT_LINE_RE`) tested against real captured production log
  lines — matches correctly.
- Baseline-priming tested against real local desk data (38 real MLB trades,
  131 real skips) — correctly primes with zero false "new" events on first
  poll.
- Full pipeline (log append → regex → event queue → SSE → JSON payload)
  verified end-to-end with one synthetic-but-real-format log line — event
  appeared correctly formatted on the `/events` stream.
- Analyst poll-and-diff logic verified against an isolated scratch file
  (not any real desk path) — correctly primes on existing records, correctly
  detects exactly one new event after a real append, using the exact same
  full-array-rewrite pattern the real pipeline uses.

## Migration note

Once the future unified-schema migration lands (mentioned as an intentional
"repoint later" in the original brief), this watcher's `_poll_paper_trades`/
`_poll_skipped_trades`/`_poll_shadow_trades`/`_poll_auditor`/`_poll_ledger`
methods would each change from polling a JSON file to querying whatever
replaces it (e.g. a `candidates` table). The event *shape* emitted onto the
queue, the SSE transport, and the entire frontend stay unchanged — only the
five `_poll_*` methods' internals need updating. The Scout side (log-tailing)
is unaffected by any data-schema migration, since it reads from process
stdout, not a data file.
