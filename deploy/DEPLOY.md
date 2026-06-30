# Cloud Migration — VPS Deployment Guide

Moves `position_manager.py`, `schedule_snapshots.py`, `update_outcomes.py`,
`send_digest.py`, and `weekly_audit.py` off the laptop's `launchd` onto an
always-on VPS's `systemd`, so the pipeline keeps running when the Mac is
asleep or closed. The local Streamlit dashboard and iMessage identity are
unaffected — see "Notification relay" below.

## 1. Provision the VPS

**Primary: Oracle Cloud "Always Free"**
1. Sign up at cloud.oracle.com (requires a card for identity verification, never charged on the free tier).
2. Create a compute instance: shape `VM.Standard.A1.Flex` (Ampere, Always Free), 1 OCPU / 6 GB RAM is plenty. Image: Ubuntu 22.04 or later.
3. If the Ampere shape capacity check fails in your home region, retry in a different Always-Free-eligible region, or fall back to step below.
4. Add your SSH public key during instance creation (or after, via the console).
5. In the VPS's network security list / security group: allow inbound TCP 22 (SSH) only. No other inbound ports are needed — outbound is unrestricted by default.

**Fallback: Google Cloud `e2-micro`**
- Create an `e2-micro` instance in `us-west1`, `us-central1`, or `us-east1` (the three Always Free regions). Same Ubuntu image, same steps below.

## 2. Generate a dedicated SSH key (Mac side)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_predfund -C "predfund-vps"
```

Add the public key to the VPS (via cloud console "Add SSH key" or `ssh-copy-id -i ~/.ssh/id_ed25519_predfund.pub ubuntu@<VPS_IP>`).

## 3. Server setup (run on the VPS)

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git rsync

sudo useradd -m -s /bin/bash predfund
sudo -u predfund -i

git clone https://github.com/Ananthak2324/prediction-market-fund.git /opt/prediction-fund   # adjust path/owner as needed
cd /opt/prediction-fund
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
mkdir -p data/snapshots data/audits
```

(`sudo chown -R predfund:predfund /opt/prediction-fund` if cloned as a different user.)

## 4. Copy `.env` (never via git)

From the Mac:

```bash
scp -i ~/.ssh/id_ed25519_predfund "/Users/ananthan/Desktop/Prediction Market Fund/.env" predfund@<VPS_IP>:/opt/prediction-fund/.env
ssh -i ~/.ssh/id_ed25519_predfund predfund@<VPS_IP> "chmod 600 /opt/prediction-fund/.env"
```

## 5. Install systemd units

From the Mac, copy the unit templates in `deploy/systemd/` to the VPS, then enable them:

```bash
scp -i ~/.ssh/id_ed25519_predfund deploy/systemd/*.service deploy/systemd/*.timer predfund@<VPS_IP>:/tmp/
ssh -i ~/.ssh/id_ed25519_predfund <VPS_IP>  # then on the VPS:

sudo mv /tmp/*.service /tmp/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Always-on daemon
sudo systemctl enable --now prediction-fund-positions.service

# Timer-driven jobs
sudo systemctl enable --now prediction-fund-snapshot.timer
sudo systemctl enable --now prediction-fund-outcomes.timer
sudo systemctl enable --now prediction-fund-digest.timer
sudo systemctl enable --now prediction-fund-weekly-audit.timer
```

Verify:

```bash
systemctl status prediction-fund-positions.service
systemctl list-timers | grep prediction-fund
journalctl -u prediction-fund-positions.service -f
```

Test the `Restart=always` guarantee:

```bash
sudo systemctl kill prediction-fund-positions.service
sleep 12
systemctl status prediction-fund-positions.service   # should show "active (running)" again
```

## 6. Notification relay (VPS has no Messages.app)

`core/notifications.py` already detects `sys.platform != "darwin"` and writes to
`data/notification_queue.jsonl` instead of calling `osascript`. To get those
messages onto your phone:

1. Edit `scripts/sync_from_vps.sh` on the Mac: set `VPS_HOST` to your VPS's IP/hostname.
2. Load the sync LaunchAgent:
   ```bash
   launchctl load ~/Library/LaunchAgents/com.predictionfund.data_sync.plist
   ```
   This pulls `notification_queue.jsonl` and the other state files (`paper_trades.json`,
   `paper_trades.db`, `performance_summary.json`, `skipped_trades.json`,
   `agent_cost_log.csv`, `data/audits/*.json`) down every 5 minutes.
3. `com.predictionfund.notify_relay` (already loaded, runs every 5 min) picks up
   unsent entries from the synced queue file and sends them via the existing
   `send_imessage()` / osascript path. Sent IDs are tracked in
   `data/.notify_sent_ids.json` so nothing double-sends.

## 7. Cut over and retire the Mac LaunchAgents

Once the VPS has run cleanly for 24h with zero `MISSED_SNAPSHOT` lines in its logs:

```bash
launchctl unload ~/Library/LaunchAgents/com.predictionfund.positions.plist
launchctl unload ~/Library/LaunchAgents/com.predictionfund.snapshot.plist
launchctl unload ~/Library/LaunchAgents/com.predictionfund.outcomes.plist
launchctl unload ~/Library/LaunchAgents/com.predictionfund.digest.plist
launchctl unload ~/Library/LaunchAgents/com.predictionfund.weekly_audit.plist
```

Keep `com.predictionfund.notify_relay` and `com.predictionfund.data_sync` loaded on
the Mac — those are the only two that should still run locally. The local Streamlit
dashboard keeps reading from the same `data/` files, now kept fresh by the sync.
