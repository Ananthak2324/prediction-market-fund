#!/bin/bash
# scripts/sync_from_vps.sh
#
# Pulls trading-state data files down from the VPS to this Mac, so the local
# Streamlit dashboard and scripts/relay_notifications.py keep working
# unchanged after the trading pipeline itself moves to the VPS.
#
# Run every ~5 min via com.predictionfund.data_sync.plist. That LaunchAgent
# is NOT loaded yet — fill in VPS_HOST below and generate/copy an SSH key
# first (see deploy/DEPLOY.md), then `launchctl load` it.
set -euo pipefail

VPS_USER="predfund"
VPS_HOST="REPLACE_WITH_VPS_IP_OR_HOSTNAME"
VPS_DATA_DIR="/opt/prediction-fund/data"
SSH_KEY="$HOME/.ssh/id_ed25519_predfund"
LOCAL_DATA_DIR="/Users/ananthan/Desktop/Prediction Market Fund/data"

rsync -az --timeout=20 \
  -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" \
  --include="notification_queue.jsonl" \
  --include="paper_trades.json" \
  --include="paper_trades.db" \
  --include="performance_summary.json" \
  --include="skipped_trades.json" \
  --include="agent_cost_log.csv" \
  --include="audits/" \
  --include="audits/*.json" \
  --exclude="*" \
  "$VPS_USER@$VPS_HOST:$VPS_DATA_DIR/" "$LOCAL_DATA_DIR/"
