import os
import subprocess


def send_imessage(message: str) -> bool:
    """
    Send an iMessage via Messages.app (macOS only).

    Recipient comes from IMESSAGE_RECIPIENT env var. Never raises —
    a notification failure must never break the trading pipeline.
    """
    recipient = os.getenv("IMESSAGE_RECIPIENT", "")
    if not recipient:
        print("  [NOTIFY] IMESSAGE_RECIPIENT not set, skipping")
        return False

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
