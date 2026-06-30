import json
import os
import subprocess
import sys
import time
import uuid

NOTIFICATION_QUEUE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "notification_queue.jsonl"
)


def send_imessage(message: str) -> bool:
    """
    Send a notification for `message`.

    On macOS, sends an iMessage directly via Messages.app (osascript).
    On Linux (e.g. a VPS with no Messages.app), appends the message to
    data/notification_queue.jsonl instead — scripts/relay_notifications.py,
    running on the Mac, polls this queue (synced down via rsync) and sends
    each entry via the same osascript path.

    Recipient comes from IMESSAGE_RECIPIENT env var. Never raises —
    a notification failure must never break the trading pipeline.
    """
    recipient = os.getenv("IMESSAGE_RECIPIENT", "")
    if not recipient:
        print("  [NOTIFY] IMESSAGE_RECIPIENT not set, skipping")
        return False

    if sys.platform != "darwin":
        return _queue_notification(message)

    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'tell application "Messages" to send "{escaped}" '
        f'to buddy "{recipient}" of (service 1 whose service type is iMessage)'
    )

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            print(f"  [NOTIFY] send_imessage failed: {result.stderr.strip()}")
            return False
        return True
    except Exception as e:
        print(f"  [NOTIFY] send_imessage error: {e}")
        return False


def _queue_notification(message: str) -> bool:
    try:
        os.makedirs(os.path.dirname(NOTIFICATION_QUEUE), exist_ok=True)
        entry = {
            "id":         uuid.uuid4().hex,
            "message":    message,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with open(NOTIFICATION_QUEUE, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return True
    except Exception as e:
        print(f"  [NOTIFY] queue_notification error: {e}")
        return False
