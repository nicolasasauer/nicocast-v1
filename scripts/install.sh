#!/usr/bin/env bash
# =============================================================================
# NicoCast – Installation script for Raspberry Pi Zero 2W
# (Raspberry Pi OS Bookworm / Bullseye, 32-bit or 64-bit)
# =============================================================================
# Run as root:   sudo bash scripts/install.sh
# Optional env vars:
#   WIFI_COUNTRY  – two-letter country code for Wi-Fi regulatory domain
#                   (default: DE).  Example: WIFI_COUNTRY=US sudo bash install.sh
# =============================================================================
set -euo pipefail

INSTALL_DIR="/opt/nicocast"
CONFIG_DIR="/etc/nicocast"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_USER="nicocast"
WIFI_COUNTRY="${WIFI_COUNTRY:-DE}"

# ─── Colour helpers ───────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[+]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
error()   { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
check_root() { [[ $EUID -eq 0 ]] || error "This script must be run as root (sudo)"; }

check_root

# ─── Detect architecture ──────────────────────────────────────────────────────
ARCH="$(uname -m)"   # aarch64 = 64-bit ARM, armv7l = 32-bit ARM
info "Detected architecture: ${ARCH}"

# ─── 0. Unblock Wi-Fi (rfkill) ────────────────────────────────────────────────
info "Unblocking Wi-Fi radio…"
if command -v rfkill &>/dev/null; then
    rfkill unblock wifi || true
fi

# ─── 1. System dependencies ───────────────────────────────────────────────────
info "Installing system dependencies…"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    wpasupplicant wireless-tools iproute2 rfkill \
    dnsmasq \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    libgstreamer1.0-0 \
    libgstreamer-plugins-base1.0-0

# gstreamer1.0-omx-rpi is only available on 32-bit (armhf) Raspberry Pi OS.
# On 64-bit (aarch64) Bookworm/Bullseye, hardware H.264 decoding is provided
# by v4l2h264dec (in gstreamer1.0-plugins-good) via the V4L2 M2M interface.
if [[ "${ARCH}" == "armv7l" ]]; then
    info "32-bit ARM detected – attempting to install gstreamer1.0-omx-rpi…"
    apt-get install -y --no-install-recommends gstreamer1.0-omx-rpi 2>/dev/null || \
        warn "gstreamer1.0-omx-rpi not available; v4l2h264dec will be used instead."
fi

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
    # On RPi OS Lite (no desktop/compositor) kmssink is the correct video sink.
    # Override the default if we detect a headless (Lite) image.
    if ! dpkg -l xserver-xorg-core &>/dev/null 2>&1; then
        info "Headless OS detected – setting video_sink = kmssink in config."
        sed -i 's/^video_sink = auto/video_sink = kmssink/' \
            "${CONFIG_DIR}/nicocast.conf"
    fi
    info "Config written to ${CONFIG_DIR}/nicocast.conf – edit as needed."
else
    warn "Config already exists at ${CONFIG_DIR}/nicocast.conf – not overwriting."
fi
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${CONFIG_DIR}"

# ─── 6. NetworkManager: release wlan0 so wpa_supplicant can manage it ─────────
if systemctl is-active --quiet NetworkManager 2>/dev/null; then
    info "NetworkManager detected – marking wlan0 as unmanaged…"
    NM_UNMANAGED_FILE="/etc/NetworkManager/conf.d/nicocast-unmanaged.conf"
    cat > "${NM_UNMANAGED_FILE}" <<'NMEOF'
# Written by NicoCast installer.
# wlan0 is managed by wpa_supplicant for Wi-Fi Direct P2P.
[keyfile]
unmanaged-devices=interface-name:wlan0
NMEOF
    systemctl reload NetworkManager 2>/dev/null || true
    info "NetworkManager will no longer manage wlan0."
fi

# ─── 7. wpa_supplicant P2P configuration ─────────────────────────────────────
info "Configuring wpa_supplicant for Wi-Fi Direct P2P (country: ${WIFI_COUNTRY})…"
WIFI_COUNTRY="${WIFI_COUNTRY}" bash "${REPO_DIR}/scripts/setup_wpa_supplicant.sh"

# ─── 8. dnsmasq: prevent it from overriding system DNS ───────────────────────
info "Disabling system-wide dnsmasq service (NicoCast runs its own instance)…"
systemctl stop dnsmasq 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true

# ─── 9. Systemd service ───────────────────────────────────────────────────────
info "Installing systemd service…"
cp "${REPO_DIR}/systemd/nicocast.service" /etc/systemd/system/nicocast.service
chown root:root /etc/systemd/system/nicocast.service
chmod 644 /etc/systemd/system/nicocast.service
systemctl daemon-reload
systemctl enable nicocast.service
systemctl start nicocast.service

# ─── 10. Summary ───────────────────────────────────────────────────────────────
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
