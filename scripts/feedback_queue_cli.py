"""
scripts/feedback_queue_cli.py

Human approve/reject interface for data/audits/feedback_queue.json (the
queue agent/feedback_loop_agent.py drafts KILL/DOWNGRADE follow-up notes
into). Only ever flips an entry's status/reviewed_at/reviewed_by/review_note
fields — never edits desks/*.yaml, never restarts a service, never applies
any change itself. Any actual desk-config change based on an APPROVED entry
remains a fully separate manual step, by design.

Usage:
    python scripts/feedback_queue_cli.py list [--status PENDING_REVIEW]
    python scripts/feedback_queue_cli.py show <id>
    python scripts/feedback_queue_cli.py approve <id> [--by "name"] [--note "..."]
    python scripts/feedback_queue_cli.py reject <id> [--by "name"] [--note "..."]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

BASE       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE_FILE = os.path.join(BASE, "data", "audits", "feedback_queue.json")


def load_queue() -> list[dict]:
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE) as f:
        return json.load(f)


def save_queue(entries: list[dict]) -> None:
    with open(QUEUE_FILE, "w") as f:
        json.dump(entries, f, indent=2)


def _find(entries: list[dict], entry_id: str) -> dict | None:
    for e in entries:
        if e.get("id") == entry_id:
            return e
    return None


def cmd_list(args) -> None:
    entries = load_queue()
    if args.status:
        entries = [e for e in entries if e.get("status") == args.status]
    if not entries:
        print("(no entries)")
        return
    print(f"{'ID':<30} {'SCOPE':<8} {'TARGET':<10} {'VERDICT':<10} {'STATUS'}")
    for e in entries:
        print(f"{e.get('id',''):<30} {e.get('scope',''):<8} {e.get('target_id',''):<10} "
              f"{e.get('verdict',''):<10} {e.get('status','')}")


def cmd_show(args) -> None:
    entries = load_queue()
    entry = _find(entries, args.id)
    if entry is None:
        print(f"No entry with id={args.id!r}")
        sys.exit(1)
    print(json.dumps(entry, indent=2))


def _review(args, new_status: str) -> None:
    entries = load_queue()
    entry = _find(entries, args.id)
    if entry is None:
        print(f"No entry with id={args.id!r}")
        sys.exit(1)
    entry["status"]      = new_status
    entry["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    entry["reviewed_by"] = args.by or "unspecified"
    entry["review_note"] = args.note
    save_queue(entries)
    print(f"{args.id} -> {new_status} (reviewed_by={entry['reviewed_by']})")


def cmd_approve(args) -> None:
    _review(args, "APPROVED")


def cmd_reject(args) -> None:
    _review(args, "REJECTED")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", default=None)
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show")
    p_show.add_argument("id")
    p_show.set_defaults(func=cmd_show)

    p_approve = sub.add_parser("approve")
    p_approve.add_argument("id")
    p_approve.add_argument("--by", default=None)
    p_approve.add_argument("--note", default=None)
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject")
    p_reject.add_argument("id")
    p_reject.add_argument("--by", default=None)
    p_reject.add_argument("--note", default=None)
    p_reject.set_defaults(func=cmd_reject)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
