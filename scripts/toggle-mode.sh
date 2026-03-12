#!/usr/bin/env bash
# =============================================================================
# NicoCast – toggle-mode.sh
#
# Switches the operation_mode between 'hybrid' and 'performance' in the
# NicoCast configuration file and applies the required system changes.
#
# Usage:
#   sudo toggle-mode.sh              – toggle to the other mode
#   sudo toggle-mode.sh hybrid       – switch explicitly to hybrid
#   sudo toggle-mode.sh performance  – switch explicitly to performance
#
# Modes:
#   hybrid      – NetworkManager keeps running and continues to own wlan0.
#                 Your Wi-Fi connection and SSH session are preserved.
#                 NicoCast configures Wi-Fi Direct P2P on top via the shared
#                 wpa_supplicant control socket.
#                 NOTE: background Wi-Fi scanning may increase video latency.
#
#   performance – NetworkManager is stopped at service start; a standalone
#                 wpa_supplicant@wlan0 service takes exclusive control of wlan0
#                 (P2P-only, lower latency, no regular Wi-Fi/SSH).
#                 WARNING: switching to performance mode will disconnect you
#                 from Wi-Fi and end any active SSH session.
# =============================================================================
set -euo pipefail

CONFIG="${NICOCAST_CONFIG:-/etc/nicocast/nicocast.conf}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WPA_SETUP="${SCRIPT_DIR}/setup_wpa_supplicant.sh"
# Fall back to the installed location used by the systemd service
[[ -x "${WPA_SETUP}" ]] || WPA_SETUP="/opt/nicocast/scripts/setup_wpa_supplicant.sh"
NM_UNMANAGED_FILE="/etc/NetworkManager/conf.d/nicocast-unmanaged.conf"
# Seconds to wait after reloading NetworkManager / starting wpa_supplicant
NM_RELOAD_WAIT=3

# ─── Colour helpers ───────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

# ─── Root check ───────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || error "This script must be run as root (sudo toggle-mode.sh)"

# ─── Config file must exist ───────────────────────────────────────────────────
[[ -f "${CONFIG}" ]] || error "Config file not found: ${CONFIG}"

# ─── Determine current mode ───────────────────────────────────────────────────
CURRENT=$(grep -E "^[[:space:]]*operation_mode[[:space:]]*=" "${CONFIG}" 2>/dev/null \
          | tail -1 \
          | sed 's/.*=[[:space:]]*//' \
          | sed 's/[[:space:]]*#.*//' \
          | tr -d '[:space:]')
CURRENT="${CURRENT:-hybrid}"

# ─── Determine Wi-Fi interface ───────────────────────────────────────────────
IFACE=$(grep -E "^[[:space:]]*interface[[:space:]]*=" "${CONFIG}" 2>/dev/null \
        | tail -1 | sed 's/.*=[[:space:]]*//' | sed 's/[[:space:]]*#.*//' \
        | tr -d '[:space:]')
IFACE="${IFACE:-wlan0}"

# ─── Determine target mode ────────────────────────────────────────────────────
if [[ $# -ge 1 ]]; then
    TARGET="$1"
else
    # Toggle
    if [[ "${CURRENT}" == "hybrid" ]]; then
        TARGET="performance"
    else
        TARGET="hybrid"
    fi
fi

# Validate
case "${TARGET}" in
    hybrid|performance) ;;
    *) error "Invalid mode '${TARGET}'. Valid values: hybrid | performance" ;;
esac

if [[ "${CURRENT}" == "${TARGET}" ]]; then
    info "Already in '${TARGET}' mode – nothing to do."
    exit 0
fi

# ─── Stop the service before making system changes ───────────────────────────
SERVICE_WAS_ACTIVE=false
if systemctl is-active --quiet nicocast 2>/dev/null; then
    SERVICE_WAS_ACTIVE=true
    info "Stopping nicocast service…"
    systemctl stop nicocast
fi

# ─── Apply system changes for the target mode ─────────────────────────────────
if [[ "${TARGET}" == "performance" ]]; then
    # ── hybrid → performance ──────────────────────────────────────────────────
    warn "Switching to performance mode."
    warn "wlan0 will be taken from NetworkManager and dedicated to P2P."
    warn "WARNING: Your Wi-Fi connection and any active SSH session will be lost!"
    echo ""

    # 1. Detect Wi-Fi country for wpa_supplicant config
    WIFI_COUNTRY=""
    # Try iw regulatory domain first
    WIFI_COUNTRY=$(iw reg get 2>/dev/null \
                   | grep "^country" | head -1 \
                   | awk '{print $2}' | tr -d ':' || true)
    # Try existing wpa_supplicant configs
    if [[ -z "${WIFI_COUNTRY}" ]]; then
        WIFI_COUNTRY=$(grep "^country=" \
                       /etc/wpa_supplicant/wpa_supplicant*.conf 2>/dev/null \
                       | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)
    fi
    WIFI_COUNTRY="${WIFI_COUNTRY:-DE}"
    info "Using Wi-Fi country code: ${WIFI_COUNTRY}"

    # 2. Tell NetworkManager to stop managing wlan0
    info "Marking ${IFACE} as unmanaged in NetworkManager…"
    mkdir -p /etc/NetworkManager/conf.d
    cat > "${NM_UNMANAGED_FILE}" <<NMEOF
# Written by NicoCast toggle-mode.sh (performance mode).
# ${IFACE} is managed exclusively by wpa_supplicant for Wi-Fi Direct P2P.
[keyfile]
unmanaged-devices=interface-name:${IFACE}
NMEOF
    systemctl reload NetworkManager 2>/dev/null || true
    sleep "${NM_RELOAD_WAIT}"   # give NM time to release the interface

    # 3. Set up standalone wpa_supplicant@wlan0 with P2P / WFD config
    info "Configuring wpa_supplicant for Wi-Fi Direct P2P…"
    if [[ -x "${WPA_SETUP}" ]]; then
        WIFI_COUNTRY="${WIFI_COUNTRY}" bash "${WPA_SETUP}"
    else
        error "setup_wpa_supplicant.sh not found at ${WPA_SETUP}"
    fi

else
    # ── performance → hybrid ──────────────────────────────────────────────────
    info "Switching to hybrid mode."
    info "NetworkManager will take back ${IFACE} and reconnect to your saved Wi-Fi."

    # 1. Remove the NM unmanaged override so NM takes back wlan0
    if [[ -f "${NM_UNMANAGED_FILE}" ]]; then
        rm -f "${NM_UNMANAGED_FILE}"
        info "Removed NM unmanaged config."
    fi

    # 2. Stop and disable the standalone wpa_supplicant@wlan0 service
    if systemctl is-active --quiet "wpa_supplicant@${IFACE}" 2>/dev/null; then
        systemctl stop    "wpa_supplicant@${IFACE}" 2>/dev/null || true
        info "Stopped wpa_supplicant@${IFACE}."
    fi
    if systemctl is-enabled --quiet "wpa_supplicant@${IFACE}" 2>/dev/null; then
        systemctl disable "wpa_supplicant@${IFACE}" 2>/dev/null || true
        info "Disabled wpa_supplicant@${IFACE}."
    fi

    # 3. Reload NM so it picks up the (now removed) unmanaged override and
    #    reconnects to the saved Wi-Fi network
    if systemctl is-active --quiet NetworkManager 2>/dev/null; then
        systemctl reload NetworkManager 2>/dev/null || true
        info "NetworkManager reloaded – it will reconnect to the saved Wi-Fi network."
    else
        systemctl start NetworkManager 2>/dev/null || true
        info "NetworkManager started."
    fi
    sleep "${NM_RELOAD_WAIT}"   # give NM time to reassociate with the saved AP
fi

# ─── Update the config file ───────────────────────────────────────────────────
if grep -qE "^[[:space:]]*operation_mode[[:space:]]*=" "${CONFIG}"; then
    sed -i "s|^[[:space:]]*operation_mode[[:space:]]*=.*|operation_mode = ${TARGET}|" "${CONFIG}"
else
    sed -i "/^\[general\]/a operation_mode = ${TARGET}" "${CONFIG}"
fi
info "Config updated: operation_mode = ${TARGET}"

# ─── Restart the service ─────────────────────────────────────────────────────
if [[ "${SERVICE_WAS_ACTIVE}" == "true" ]]; then
    info "Starting nicocast service in '${TARGET}' mode…"
    systemctl start nicocast
    info "nicocast started."
else
    info "nicocast service was not running – mode will take effect on next start."
fi

echo ""
if [[ "${TARGET}" == "performance" ]]; then
    warn "Performance mode active. wlan0 is dedicated to Wi-Fi Direct P2P."
    warn "To restore Wi-Fi/SSH: sudo toggle-mode.sh hybrid"
else
    info "Hybrid mode active. Wi-Fi and SSH are available."
    info "NicoCast is advertising as a Miracast sink via the shared wpa_supplicant."
fi

