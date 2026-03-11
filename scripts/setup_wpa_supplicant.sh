#!/usr/bin/env bash
# =============================================================================
# NicoCast – wpa_supplicant P2P setup for Raspberry Pi Zero 2W
# =============================================================================
# This script:
#   1. Backs up the existing wpa_supplicant.conf
#   2. Writes a new config that enables P2P with WFD subelements
#   3. Restarts wpa_supplicant
# =============================================================================
set -euo pipefail

IFACE="${1:-wlan0}"
WPA_CONF="/etc/wpa_supplicant/wpa_supplicant.conf"
WPA_P2P_CONF="/etc/wpa_supplicant/wpa_supplicant-p2p.conf"
WPA_SERVICE="wpa_supplicant@${IFACE}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || error "Run as root (sudo)"

# ─── Back up existing wpa_supplicant config ───────────────────────────────────
if [[ -f "${WPA_CONF}" ]]; then
    cp "${WPA_CONF}" "${WPA_CONF}.backup-$(date +%Y%m%d-%H%M%S)"
    info "Backed up existing wpa_supplicant.conf"
fi

# ─── Write new P2P-capable wpa_supplicant config ─────────────────────────────
info "Writing wpa_supplicant P2P config to ${WPA_P2P_CONF}…"
cat > "${WPA_P2P_CONF}" <<'WPAEOF'
# wpa_supplicant configuration for NicoCast (Wi-Fi Direct P2P + WFD Miracast)
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=DE

# ── Device identification ──────────────────────────────────────────────────
# Device name shown to source devices (overridden at runtime by NicoCast)
device_name=NicoCast
# WPS Device Type: 7 = Display (OUI 00:50:F2, sub-category 01 = television)
device_type=7-0050F204-1
# Supported WPS config methods
config_methods=keypad display push_button

# ── P2P settings ──────────────────────────────────────────────────────────
# Always become the Group Owner (no existing AP needed)
p2p_go_intent=15
# Persist P2P group across reconnects
persistent_reconnect=1
# Use the same physical interface for P2P (saves a virtual interface)
p2p_no_group_iface=0

# ── WFD (Wi-Fi Display / Miracast) advertisement ──────────────────────────
# Subelement 0: primary sink, session available, port 7236, 50 Mbps
# 00 = Subelement ID 0
# 06 = data length 6 bytes
# 0011 = WFD Device Info (primary sink + session available)
# 1C44 = RTSP port 7236
# 0032 = max throughput 50 Mbps
wfd_subelems=000600111C440032
WPAEOF

chmod 600 "${WPA_P2P_CONF}"

# ─── Enable the interface-specific wpa_supplicant service ────────────────────
info "Enabling wpa_supplicant for interface ${IFACE}…"

# Symlink the new config so the interface-specific service uses it
ln -sf "${WPA_P2P_CONF}" "/etc/wpa_supplicant/wpa_supplicant-${IFACE}.conf"

# Disable the legacy global service and enable the per-interface one
systemctl stop wpa_supplicant 2>/dev/null || true
systemctl disable wpa_supplicant 2>/dev/null || true

systemctl enable "${WPA_SERVICE}"
systemctl restart "${WPA_SERVICE}"

# Give wpa_supplicant time to create its control socket
for i in $(seq 1 10); do
    if [[ -S "/var/run/wpa_supplicant/${IFACE}" ]]; then
        info "wpa_supplicant control socket ready."
        break
    fi
    sleep 1
done

# ─── Verify P2P is available ─────────────────────────────────────────────────
if wpa_cli -i "${IFACE}" status | grep -q "p2p_state"; then
    info "P2P is active on ${IFACE}."
else
    warn "P2P status check inconclusive – NicoCast will enable P2P at startup."
fi

info "wpa_supplicant P2P setup complete."
