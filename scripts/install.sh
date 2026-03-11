#!/usr/bin/env bash
# =============================================================================
# NicoCast – Installation script for Raspberry Pi Zero 2W
# (Raspberry Pi OS Bookworm / Bullseye, 32-bit or 64-bit)
# =============================================================================
# Run as root:   sudo bash scripts/install.sh
# =============================================================================
set -euo pipefail

INSTALL_DIR="/opt/nicocast"
CONFIG_DIR="/etc/nicocast"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_USER="nicocast"

# ─── Colour helpers ───────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[+]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
error()   { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
check_root() { [[ $EUID -eq 0 ]] || error "This script must be run as root (sudo)"; }

check_root

# ─── 1. System dependencies ───────────────────────────────────────────────────
info "Installing system dependencies…"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    wpasupplicant wireless-tools iproute2 \
    dnsmasq \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    gstreamer1.0-omx-rpi \
    libgstreamer1.0-0 \
    libgstreamer-plugins-base1.0-0

# ─── 2. Create service user ───────────────────────────────────────────────────
info "Creating service user '${SERVICE_USER}'…"
if ! id -u "${SERVICE_USER}" &>/dev/null; then
    adduser --system --group --no-create-home \
        --gecos "NicoCast service user" "${SERVICE_USER}"
fi
# Allow the user to run wpa_cli without sudo
usermod -aG netdev "${SERVICE_USER}" 2>/dev/null || true

# ─── 3. Copy application files ────────────────────────────────────────────────
info "Installing NicoCast to ${INSTALL_DIR}…"
mkdir -p "${INSTALL_DIR}"
cp -r "${REPO_DIR}/nicocast" "${INSTALL_DIR}/"
# Create __main__.py so the package is runnable with  python -m nicocast
cat > "${INSTALL_DIR}/nicocast/__main__.py" <<'EOF'
from nicocast.main import main
main()
EOF

# ─── 4. Python virtual environment ───────────────────────────────────────────
info "Creating Python virtual environment…"
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install --quiet \
    flask \
    configparser

# ─── 5. Configuration ─────────────────────────────────────────────────────────
info "Installing default configuration to ${CONFIG_DIR}…"
mkdir -p "${CONFIG_DIR}"
if [[ ! -f "${CONFIG_DIR}/nicocast.conf" ]]; then
    cp "${REPO_DIR}/config/nicocast.conf" "${CONFIG_DIR}/nicocast.conf"
    info "Config written to ${CONFIG_DIR}/nicocast.conf – edit as needed."
else
    warn "Config already exists at ${CONFIG_DIR}/nicocast.conf – not overwriting."
fi
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${CONFIG_DIR}"

# ─── 6. wpa_supplicant P2P configuration ─────────────────────────────────────
info "Configuring wpa_supplicant for Wi-Fi Direct P2P…"
bash "${REPO_DIR}/scripts/setup_wpa_supplicant.sh"

# ─── 7. dnsmasq: prevent it from overriding system DNS ───────────────────────
info "Disabling system-wide dnsmasq service (NicoCast runs its own instance)…"
systemctl stop dnsmasq 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true

# ─── 8. Systemd service ───────────────────────────────────────────────────────
info "Installing systemd service…"
cp "${REPO_DIR}/systemd/nicocast.service" /etc/systemd/system/nicocast.service
chown root:root /etc/systemd/system/nicocast.service
chmod 644 /etc/systemd/system/nicocast.service
systemctl daemon-reload
systemctl enable nicocast.service
systemctl start nicocast.service

# ─── 9. Summary ───────────────────────────────────────────────────────────────
echo ""
info "════════════════════════════════════════════"
info "  NicoCast installed successfully!"
info "════════════════════════════════════════════"
echo ""
echo "  • Service status:  sudo systemctl status nicocast"
echo "  • Live logs:       sudo journalctl -fu nicocast"
echo "  • Settings web UI: http://$(hostname -I | awk '{print $1}'):8080/"
echo "  • Config file:     ${CONFIG_DIR}/nicocast.conf"
echo ""
warn "On your Android device, open Smart View (or any Miracast app)"
warn "and look for '$(grep device_name ${CONFIG_DIR}/nicocast.conf | awk -F= '{print $2}' | xargs)'."
echo ""
