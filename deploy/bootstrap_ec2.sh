#!/usr/bin/env bash
# ==============================================================================
# AlphaChain - AWS EC2 Remote Bootstrap Script
# Target: Ubuntu 22.04 / 24.04 LTS (ap-southeast-2 Sydney)
# Execution: Run on the newly launched EC2 instance as ubuntu (with sudo)
# ==============================================================================

set -euo pipefail

APP_DIR="/opt/alphachain"
APP_USER="ubuntu"
APP_GROUP="ubuntu"
SERVICE_NAME="alphachain"

echo "=============================================================================="
echo "🚀 AlphaChain Production EC2 Bootstrapper (Sydney ap-southeast-2)"
echo "=============================================================================="

# 1. Update OS packages & install essential system dependencies
echo "📦 [1/8] Updating apt packages and upgrading base OS..."
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"

echo "🛠️ [2/8] Installing core dependencies (Python 3, venv, git, chrony, curl, jq, sqlite3)..."
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    git \
    curl \
    jq \
    htop \
    sqlite3 \
    chrony \
    tmux \
    ca-certificates

# 2. Configure Amazon Time Sync Service (Chrony)
echo "⏱️ [3/8] Configuring Amazon Time Sync Service (169.254.169.123)..."
CHRONY_CONF="/etc/chrony/chrony.conf"
if [ ! -f "${CHRONY_CONF}" ] && [ -f "/etc/chrony.conf" ]; then
    CHRONY_CONF="/etc/chrony.conf"
fi

if [ -f "${CHRONY_CONF}" ]; then
    if ! grep -q "169.254.169.123" "${CHRONY_CONF}"; then
        sudo sed -i '1s/^/server 169.254.169.123 prefer iburst minpoll 4 maxpoll 4\n/' "${CHRONY_CONF}"
        sudo systemctl restart chrony
        sudo systemctl enable chrony
    fi
    chronyc tracking || true
fi

# 3. Setup Application Directory Structure
echo "📁 [4/8] Setting up application directory structure at ${APP_DIR}..."
sudo mkdir -p "${APP_DIR}/data" "${APP_DIR}/logs"
sudo chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"
sudo chmod 750 "${APP_DIR}"

# 4. Setup Python Virtual Environment
echo "🐍 [5/8] Creating isolated Python virtual environment..."
if [ ! -d "${APP_DIR}/venv" ]; then
    sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/venv"
fi

echo "📥 [6/8] Upgrading pip and installing project dependencies..."
sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/pip" install --upgrade pip setuptools wheel
if [ -f "${APP_DIR}/requirements.txt" ]; then
    sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
else
    echo "⚠️ requirements.txt not found at ${APP_DIR}/requirements.txt; please deploy code before installing packages."
fi

# 5. Security check on production .env
echo "🔐 [7/8] Enforcing strict security permissions on secrets..."
if [ -f "${APP_DIR}/.env" ]; then
    sudo chown "${APP_USER}:${APP_GROUP}" "${APP_DIR}/.env"
    sudo chmod 600 "${APP_DIR}/.env"
    echo "✅ .env found and secured (chmod 600, owned by ${APP_USER})."
else
    echo "⚠️ Notice: ${APP_DIR}/.env is missing. Ensure production secrets are transferred securely via SCP."
    echo "   Command on local workstation: scp -i <key.pem> .env ${APP_USER}@<EC2_IP>:${APP_DIR}/.env"
fi

# 6. Install and enable Systemd Service
echo "⚙️ [8/8] Installing and enabling 24/7 systemd service unit..."
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [ -f "${APP_DIR}/deploy/alphachain.service" ]; then
    sudo cp "${APP_DIR}/deploy/alphachain.service" "${SERVICE_FILE}"
else
    sudo tee "${SERVICE_FILE}" > /dev/null << 'EOF'
[Unit]
Description=AlphaChain Autonomous 24/7 Quantitative Trading Daemon
Documentation=https://github.com/alphachain/trading-system
After=network.target network-online.target time-sync.target
Wants=network-online.target time-sync.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/alphachain

# Load secrets from isolated .env file
EnvironmentFile=/opt/alphachain/.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=/opt/alphachain

# Binary execution from dedicated virtual environment
ExecStart=/opt/alphachain/venv/bin/python3 scripts/run_bot.py

# 24/7 High-Availability & Auto-Restart Policies
Restart=always
RestartSec=5
StartLimitIntervalSec=300
StartLimitBurst=10
TimeoutStopSec=30
KillSignal=SIGTERM
SendSIGKILL=yes
KillMode=mixed

# Resource Governance & Security Hardening
LimitNOFILE=65536
MemoryHigh=1800M
MemoryMax=2048M
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/opt/alphachain/data /opt/alphachain/logs
PrivateTmp=true
NoNewPrivileges=true

# Centralized Journald Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=alphachain

[Install]
WantedBy=multi-user.target
EOF
fi

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.service"

echo "=============================================================================="
echo "🎉 Bootstrap Complete!"
echo "   Next Operational Steps:"
echo "   1. Ensure code is in:           ${APP_DIR}"
echo "   2. Ensure .env is present:      ${APP_DIR}/.env (chmod 600)"
echo "   3. Run test verification:       sudo -u ${APP_USER} ${APP_DIR}/venv/bin/pytest -v ${APP_DIR}/tests"
echo "   4. Start daemon:                sudo systemctl start ${SERVICE_NAME}"
echo "   5. Check status:                sudo systemctl status ${SERVICE_NAME}"
echo "   6. Monitor journal logs:        sudo journalctl -u ${SERVICE_NAME} -f -n 100"
echo "=============================================================================="
