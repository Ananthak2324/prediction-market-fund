"""
scripts/migrate_desk_data.py

One-time data migration (Phase 2 Step 4 of the 2026-07-04 rebuild): moves the
single shared data/*.json files into desk-namespaced directories
(data/mlb/, data/wnba/, data/nfl/) as defined by each desk's data_paths in
desks/<id>.yaml.

Routes:
  paper_trades.json    -> by trade["sport"] (or trade["desk_id"] if present)
  skipped_trades.json  -> by event_ticker/kalshi_ticker prefix (KXMLBGAME etc.)
  agent_cost_log.csv   -> best-effort; rows with no inferable sport go to
                          data/_unrouted_cost_log.csv for manual review

Leaves untouched originals at data/*.bak. Creates fresh empty
shadow_trades.json / funnel_log.json / performance_summary.json per desk.

Safe to re-run: if a desk's target file already exists and is non-empty, it
is left alone rather than overwritten (idempotent — run once).

Usage:
    python scripts/migrate_desk_data.py
"""

import csv
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.desk_loader import get_all_desks

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sport_from_ticker(record: dict) -> str | None:
    et = record.get("event_ticker", "") or record.get("kalshi_ticker", "")
    if "MLBGAME" in et:
        return "MLB"
    if "WNBAGAME" in et:
        return "WNBA"
    if "NBAGAME" in et:
        return "NBA"
    if "NFLGAME" in et:
        return "NFL"
    return None


def _desk_id_for_record(record: dict) -> str | None:
    desk_id = record.get("desk_id")
    if desk_id:
        return desk_id.upper()
    sport = record.get("sport")
    if sport:
        return sport.upper()
    return _sport_from_ticker(record)


def _backup(path: str) -> None:
    if os.path.exists(path) and not os.path.exists(path + ".bak"):
        shutil.copy2(path, path + ".bak")
        print(f"  Backed up {path} -> {path}.bak")


def _route_json_list(source_path: str, dest_paths: dict[str, str], label: str) -> dict[str, int]:
    """
    dest_paths: {desk_id: absolute_path}
    Returns per-desk counts of routed records.
    """
    counts = {desk_id: 0 for desk_id in dest_paths}
    if not os.path.exists(source_path):
        print(f"  {label}: source not found, skipping ({source_path})")
        return counts

    with open(source_path) as f:
        records = json.load(f)

    _backup(source_path)

    buckets: dict[str, list] = {desk_id: [] for desk_id in dest_paths}
    unrouted = 0
    for r in records:
        desk_id = _desk_id_for_record(r)
        if desk_id and desk_id in buckets:
            buckets[desk_id].append(r)
            counts[desk_id] += 1
        else:
            unrouted += 1

    for desk_id, dest_path in dest_paths.items():
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        existing: list = []
        if os.path.exists(dest_path):
            try:
                with open(dest_path) as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, ValueError):
                pass
        if existing:
            print(f"  {label} [{desk_id}]: {dest_path} already has {len(existing)} record(s) — skipping merge")
            continue
        with open(dest_path, "w") as f:
            json.dump(buckets[desk_id], f, indent=2)

    if unrouted:
        print(f"  {label}: {unrouted} record(s) could not be routed to a known desk (left in original file)")

    return counts


def _route_cost_log(source_path: str, dest_paths: dict[str, str]) -> dict[str, int]:
    counts = {desk_id: 0 for desk_id in dest_paths}
    if not os.path.exists(source_path):
        print(f"  agent_cost_log.csv: source not found, skipping")
        return counts

    _backup(source_path)

    with open(source_path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return counts
    fieldnames = list(rows[0].keys())

    # Cost log rows don't carry a sport/desk tag today (confirmed during the
    # 2026-07-04 audit) — there's no reliable per-row signal to route on, so
    # every row goes to the unrouted file for manual review rather than being
    # guessed at or silently dropped.
    unrouted: list = list(rows)

    unrouted_path = os.path.join(BASE, "data", "_unrouted_cost_log.csv")
    if unrouted:
        with open(unrouted_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(unrouted)
        print(f"  agent_cost_log.csv: {len(unrouted)} row(s) have no reliable per-row sport "
              f"signal — written to {unrouted_path} for manual review")

    for desk_id, dest_path in dest_paths.items():
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        if not os.path.exists(dest_path):
            with open(dest_path, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=fieldnames).writeheader()

    return counts


def main() -> None:
    desks = get_all_desks()
    print(f"Desks found: {[d.desk_id for d in desks]}\n")

    # 1. Create directories
    for desk in desks:
        for path_attr in ("paper_trades_path", "skipped_trades_path", "shadow_trades_path"):
            path = os.path.join(BASE, getattr(desk, path_attr))
            os.makedirs(os.path.dirname(path), exist_ok=True)
        os.makedirs(os.path.join(BASE, desk.snapshots_dir), exist_ok=True)
    print("Created desk data directories.\n")

    # 2. Route paper_trades.json
    print("Routing paper_trades.json...")
    trades_dest = {d.desk_id: os.path.join(BASE, d.paper_trades_path) for d in desks}
    trades_counts = _route_json_list(
        os.path.join(BASE, "data", "paper_trades.json"), trades_dest, "paper_trades"
    )

    # 3. Route skipped_trades.json
    print("\nRouting skipped_trades.json...")
    skipped_dest = {d.desk_id: os.path.join(BASE, d.skipped_trades_path) for d in desks}
    skipped_counts = _route_json_list(
        os.path.join(BASE, "data", "skipped_trades.json"), skipped_dest, "skipped_trades"
    )

    # 4. monitor_cache.json — desk-agnostic keys (event_ticker), safe to copy as-is
    print("\nCopying monitor_cache.json to each desk (event_ticker keys are globally unique)...")
    mc_source = os.path.join(BASE, "data", "monitor_cache.json")
    if os.path.exists(mc_source):
        _backup(mc_source)
        with open(mc_source) as f:
            mc_data = json.load(f)
        for desk in desks:
            dest = os.path.join(BASE, desk.monitor_cache_path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if not os.path.exists(dest):
                with open(dest, "w") as f:
                    json.dump(mc_data, f, indent=2)

    # 5. agent_cost_log.csv — best-effort routing
    print("\nRouting agent_cost_log.csv (best-effort)...")
    cost_dest = {d.desk_id: os.path.join(BASE, d.agent_cost_log_path) for d in desks}
    _route_cost_log(os.path.join(BASE, "data", "agent_cost_log.csv"), cost_dest)

    # 6. Fresh empty files per desk
    print("\nCreating fresh empty shadow_trades.json / funnel_log.json / performance_summary.json...")
    for desk in desks:
        for path_attr, empty_val in (
            ("shadow_trades_path", []),
            ("funnel_log_path", []),
            ("performance_summary_path", {}),
        ):
            dest = os.path.join(BASE, getattr(desk, path_attr))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if not os.path.exists(dest):
                with open(dest, "w") as f:
                    json.dump(empty_val, f, indent=2)

    # Summary
    print("\n" + "=" * 60)
    for desk in desks:
        n_trades  = trades_counts.get(desk.desk_id, 0)
        n_skipped = skipped_counts.get(desk.desk_id, 0)
        n_paused  = 0
        trades_file = os.path.join(BASE, desk.paper_trades_path)
        if os.path.exists(trades_file):
            with open(trades_file) as f:
                n_paused = sum(1 for t in json.load(f) if t.get("status") == "PAUSED")
        print(f"Migrated {desk.desk_id}: {n_trades} trades, {n_skipped} skips, {n_paused} PAUSED")
    print(f"\nBackups at: data/*.bak")


if __name__ == "__main__":
    main()
