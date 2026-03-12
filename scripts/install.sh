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

# ─── Detect boot partition (Bookworm: /boot/firmware, older: /boot) ───────────
if [[ -d /boot/firmware ]]; then
    BOOT_DIR="/boot/firmware"
else
    BOOT_DIR="/boot"
fi
BOOT_LOG_DIR="${BOOT_DIR}/nicocast"
INSTALL_LOG="${BOOT_LOG_DIR}/install.log"

# ─── Colour helpers ───────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
# _logfile is initially a no-op; redefined below once the log directory exists.
_logfile() { :; }
info()    { echo -e "${GREEN}[+]${NC} $*"; _logfile "[INFO ] $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; _logfile "[WARN ] $*"; }
error()   { echo -e "${RED}[✗]${NC} $*" >&2; _logfile "[ERROR] $*"; exit 1; }
check_root() { [[ $EUID -eq 0 ]] || error "This script must be run as root (sudo)"; }

check_root

# ─── SD-card install log (created here so every subsequent step is captured) ─
mkdir -p "${BOOT_LOG_DIR}" 2>/dev/null || true
chmod 777 "${BOOT_LOG_DIR}" 2>/dev/null || true
# Redefine _logfile now that the directory exists and we know its path.
_logfile() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "${INSTALL_LOG}" 2>/dev/null || true; }

_logfile "======================================================"
_logfile "  NicoCast installation started"
_logfile "  Date/time : $(date '+%Y-%m-%d %H:%M:%S')"
_logfile "  Host      : $(hostname)"
_logfile "  Kernel    : $(uname -r)"
_logfile "  Arch      : $(uname -m)"
_logfile "  OS        : $(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '\"' || echo unknown)"
_logfile "  WIFI_COUNTRY: ${WIFI_COUNTRY}"
_logfile "======================================================"

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

# Install helper scripts
info "Installing NicoCast scripts to ${INSTALL_DIR}/scripts…"
mkdir -p "${INSTALL_DIR}/scripts"
cp "${REPO_DIR}/scripts/nicocast-prestart.sh" "${INSTALL_DIR}/scripts/"
chmod 755 "${INSTALL_DIR}/scripts/nicocast-prestart.sh"

# ─── 4. Python virtual environment ───────────────────────────────────────────
info "Creating Python virtual environment…"
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install --quiet \
    flask

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
    # Update log_file to the detected boot partition so logs are accessible
    # directly from the SD card when plugged into a PC.
    info "Setting log_file to ${BOOT_LOG_DIR}/nicocast.log (SD-card readable)."
    sed -i "s|^log_file = .*|log_file = ${BOOT_LOG_DIR}/nicocast.log|" \
        "${CONFIG_DIR}/nicocast.conf"
    info "Config written to ${CONFIG_DIR}/nicocast.conf – edit as needed."
else
    warn "Config already exists at ${CONFIG_DIR}/nicocast.conf – not overwriting."
fi

# ── Always start in hybrid mode so SSH stays connected after install ──────────
# Hybrid mode keeps NetworkManager running, which means the existing Wi-Fi
# connection (and any SSH session) is preserved.  The user can switch to the
# lower-latency performance mode later with:  sudo toggle-mode.sh
info "Ensuring operation_mode = hybrid so SSH stays connected after install…"
if grep -qE "^[[:space:]]*operation_mode[[:space:]]*=" "${CONFIG_DIR}/nicocast.conf" 2>/dev/null; then
    sed -i 's/^[[:space:]]*operation_mode[[:space:]]*=.*/operation_mode = hybrid/' \
        "${CONFIG_DIR}/nicocast.conf"
else
    # Line is absent (e.g. custom config without the key) – append it.
    echo "operation_mode = hybrid" >> "${CONFIG_DIR}/nicocast.conf"
fi
_logfile "operation_mode forced to hybrid (SSH-safe default)"

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${CONFIG_DIR}"

# ─── 5b. Log directories ─────────────────────────────────────────────────────
info "Creating log directories…"
# /var/log/nicocast is used as a fallback and for development environments.
mkdir -p /var/log/nicocast
chown "${SERVICE_USER}:${SERVICE_USER}" /var/log/nicocast
chmod 750 /var/log/nicocast
# Boot-partition log directory: readable from any PC when the SD card is inserted.
info "Creating SD-card-readable log directory at ${BOOT_LOG_DIR}…"
mkdir -p "${BOOT_LOG_DIR}"
# The boot partition is typically owned by root; ensure the service user can write.
chown "${SERVICE_USER}:${SERVICE_USER}" "${BOOT_LOG_DIR}" 2>/dev/null || \
    chmod 777 "${BOOT_LOG_DIR}"

# ─── 6 & 7. NetworkManager / wpa_supplicant (hybrid mode – preserve Wi-Fi) ────
# NicoCast defaults to hybrid mode so the existing Wi-Fi connection (and any
# active SSH session) is preserved after installation.
#
# In hybrid mode NetworkManager keeps managing wlan0.  NicoCast's Python code
# accesses the shared wpa_supplicant control socket to configure Wi-Fi Direct
# P2P on top of the existing connection.  No changes to NetworkManager or
# wpa_supplicant are needed at install time.
#
# Switching wlan0 to P2P-only (wpa_supplicant@wlan0, no NM) is ONLY done when
# the user explicitly requests performance mode:   sudo toggle-mode.sh
info "Hybrid mode: NetworkManager will keep managing wlan0."
info "Your existing Wi-Fi connection and SSH session are preserved."

# Clean up any unmanaged config left over from an older NicoCast installation
# that incorrectly released wlan0 in hybrid mode.
NM_UNMANAGED_FILE="/etc/NetworkManager/conf.d/nicocast-unmanaged.conf"
if [[ -f "${NM_UNMANAGED_FILE}" ]]; then
    rm -f "${NM_UNMANAGED_FILE}"
    systemctl reload NetworkManager 2>/dev/null || true
    sleep 2
    info "Removed legacy NM unmanaged config from previous install (wlan0 restored to NM)."
fi

# Similarly, disable the standalone wpa_supplicant@wlan0 service if it was
# left enabled by a previous install to avoid conflicting with NM.
if systemctl is-enabled --quiet wpa_supplicant@wlan0 2>/dev/null; then
    systemctl stop    wpa_supplicant@wlan0 2>/dev/null || true
    systemctl disable wpa_supplicant@wlan0 2>/dev/null || true
    info "Disabled standalone wpa_supplicant@wlan0 service (hybrid mode uses NM's instance)."
fi

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
# Give the service a moment to start, then capture its initial status for the log
sleep 2
_logfile "--- systemd service status after start ---"
systemctl status nicocast.service --no-pager 2>&1 | while IFS= read -r line; do _logfile "  ${line}"; done || true

# ─── 10. Install toggle-mode script ──────────────────────────────────────────
info "Installing toggle-mode.sh to /usr/local/bin/toggle-mode.sh…"
cp "${REPO_DIR}/scripts/toggle-mode.sh" /usr/local/bin/toggle-mode.sh
chmod 755 /usr/local/bin/toggle-mode.sh

# ─── 11. Summary ───────────────────────────────────────────────────────────────
echo ""
info "════════════════════════════════════════════"
info "  NicoCast installed successfully!"
info "════════════════════════════════════════════"
echo ""
echo "  • Service status:   sudo systemctl status nicocast"
echo "  • Live logs:        sudo journalctl -fu nicocast"
echo "  • Settings web UI:  http://$(hostname -I | awk '{print $1}'):8080/"
echo "  • Config file:      ${CONFIG_DIR}/nicocast.conf"
echo "  • Install log (SD): ${INSTALL_LOG}"
echo "  • Runtime log (SD): ${BOOT_LOG_DIR}/nicocast.log"
echo "  • Toggle mode:      sudo toggle-mode.sh  (hybrid ↔ performance)"
echo ""
warn "NicoCast is running in HYBRID mode (NetworkManager stays active)."
warn "Your SSH/Wi-Fi connection is preserved."
warn "NicoCast advertises itself as a Miracast sink via the existing wpa_supplicant."
warn "For lower video latency, switch to performance mode (wlan0 dedicated to P2P):"
warn "  sudo toggle-mode.sh"
warn "  NOTE: performance mode will disconnect you from Wi-Fi/SSH until you toggle back."
echo ""
warn "On your Android device, open Smart View (or any Miracast app)"
warn "and look for '$(grep device_name ${CONFIG_DIR}/nicocast.conf | awk -F= '{print $2}' | xargs)'."
_logfile "======================================================"
_logfile "  NicoCast installation complete"
_logfile "======================================================"
echo ""
