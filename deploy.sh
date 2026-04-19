#!/bin/bash
set -e

DEPLOY_DIR="/opt/tify-commandcenter"
VENV_PYTHON="/opt/telegram-claude-bot/venv/bin/python"
VENV_PIP="/opt/telegram-claude-bot/venv/bin/pip"
SERVICE_NAME="commandcenter"

echo "============================================"
echo "  Tify Agent Command Center - Deploy Script"
echo "============================================"

# 1. Create deploy directory
echo "[1/5] Creating deploy directory at $DEPLOY_DIR..."
mkdir -p "$DEPLOY_DIR/static"

# 2. Copy files
echo "[2/5] Copying files..."
cp main.py "$DEPLOY_DIR/main.py"
cp requirements.txt "$DEPLOY_DIR/requirements.txt"
cp static/index.html "$DEPLOY_DIR/static/index.html"

# Copy .env if it doesn't exist yet
if [ ! -f "$DEPLOY_DIR/.env" ]; then
    if [ -f ".env" ]; then
        cp .env "$DEPLOY_DIR/.env"
        echo "       .env copied from current directory."
    else
        cp .env.example "$DEPLOY_DIR/.env"
        echo "       WARNING: .env not found, copied .env.example. Please edit $DEPLOY_DIR/.env and set COMMAND_CENTER_KEY!"
    fi
else
    echo "       .env already exists, skipping."
fi

# 3. Install requirements into existing venv
echo "[3/5] Installing Python requirements into venv..."
$VENV_PIP install -q -r "$DEPLOY_DIR/requirements.txt"

# 4. Install/reload systemd service
echo "[4/5] Installing systemd service..."
cp commandcenter.service /etc/systemd/system/commandcenter.service
systemctl daemon-reload
systemctl enable commandcenter.service

# 5. Start / restart the service
echo "[5/5] Starting service..."
systemctl restart commandcenter.service

sleep 2
STATUS=$(systemctl is-active commandcenter.service)

echo ""
echo "============================================"
if [ "$STATUS" = "active" ]; then
    echo "  Deployment SUCCESSFUL!"
    echo "  Service status: $STATUS"
    # Detect public IP
    PUBLIC_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || echo "<your-server-ip>")
    echo ""
    echo "  Access URL: http://$PUBLIC_IP:8080"
    echo ""
    echo "  Default API key is in $DEPLOY_DIR/.env"
    echo "  Change COMMAND_CENTER_KEY before exposing to internet!"
else
    echo "  Deployment FAILED - service is: $STATUS"
    echo "  Check logs: journalctl -u commandcenter -n 50 --no-pager"
fi
echo "============================================"
