"""
relay_notifications.py

Runs every ~5 min on the Mac (via LaunchAgent), after VPS data syncs down
via rsync. Reads data/notification_queue.jsonl (written by core/notifications.py
when running on Linux, where Messages.app doesn't exist), sends each unsent
entry via the same osascript path used locally, and tracks sent IDs in a
local-only file so nothing double-sends across cycles.

Usage:
    python scripts/relay_notifications.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from core.notifications import send_imessage

DATA_DIR      = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
QUEUE_FILE    = os.path.join(DATA_DIR, "notification_queue.jsonl")
SENT_IDS_FILE = os.path.join(DATA_DIR, ".notify_sent_ids.json")


def load_sent_ids() -> set[str]:
    if os.path.exists(SENT_IDS_FILE):
        with open(SENT_IDS_FILE) as f:
            return set(json.load(f))
    return set()


def save_sent_ids(ids: set[str]) -> None:
    with open(SENT_IDS_FILE, "w") as f:
        json.dump(sorted(ids), f, indent=2)


def main() -> None:
    if not os.path.exists(QUEUE_FILE):
        print("  [RELAY] no queue file, nothing to do")
        return

    sent_ids   = load_sent_ids()
    sent_count = 0

    with open(QUEUE_FILE) as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue

        entry_id = entry.get("id")
        if not entry_id or entry_id in sent_ids:
            continue

        ok = send_imessage(entry.get("message", ""))
        if ok:
            sent_ids.add(entry_id)
            sent_count += 1
            print(f"  [RELAY] sent {entry_id}")
        else:
            print(f"  [RELAY] failed to send {entry_id}, will retry next cycle")

    save_sent_ids(sent_ids)
    print(f"  [RELAY] {sent_count} notification(s) sent this cycle")


if __name__ == "__main__":
    main()
