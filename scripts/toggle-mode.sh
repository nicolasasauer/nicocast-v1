#!/usr/bin/env bash
# =============================================================================
# NicoCast – toggle-mode.sh
#
# Switches the operation_mode between 'hybrid' and 'performance' in the
# NicoCast configuration file, then restarts the service.
#
# Usage:
#   sudo toggle-mode.sh              – toggle to the other mode
#   sudo toggle-mode.sh hybrid       – switch explicitly to hybrid
#   sudo toggle-mode.sh performance  – switch explicitly to performance
#
# Modes:
#   hybrid      – NetworkManager keeps running; wlan0 stays connected to the
#                 home Wi-Fi while the P2P interface handles Miracast.
#                 NOTE: background Wi-Fi scanning may increase video latency.
#   performance – NetworkManager is stopped; wpa_supplicant has exclusive
#                 control over wlan0 (P2P-only, lower latency).
# =============================================================================
set -euo pipefail

CONFIG="${NICOCAST_CONFIG:-/etc/nicocast/nicocast.conf}"

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

# ─── Update the config file ───────────────────────────────────────────────────
info "Switching from '${CURRENT}' → '${TARGET}' …"

if grep -qE "^[[:space:]]*operation_mode[[:space:]]*=" "${CONFIG}"; then
    # Key exists – replace it in place
    sed -i "s|^[[:space:]]*operation_mode[[:space:]]*=.*|operation_mode = ${TARGET}|" "${CONFIG}"
else
    # Key missing – append it to the [general] section
    sed -i "/^\[general\]/a operation_mode = ${TARGET}" "${CONFIG}"
fi

info "Config updated: operation_mode = ${TARGET}"

# ─── Warn about mode implications ────────────────────────────────────────────
if [[ "${TARGET}" == "hybrid" ]]; then
    warn "Hybrid mode: NetworkManager will run alongside the P2P interface."
    warn "Background Wi-Fi scanning may increase video latency."
else
    warn "Performance mode: NetworkManager will be stopped at next service start."
    warn "The device will only be reachable via the P2P interface (or saved Wi-Fi fallback)."
fi

# ─── Restart the service ─────────────────────────────────────────────────────
if systemctl is-active --quiet nicocast 2>/dev/null; then
    info "Restarting nicocast service…"
    systemctl restart nicocast
    info "nicocast restarted in '${TARGET}' mode."
else
    info "nicocast service is not running – mode will take effect on next start."
fi
