#!/usr/bin/env bash
# =============================================================================
# NicoCast – pre-start helper (run as root via systemd ExecStartPre=+)
#
# Reads the operation_mode from nicocast.conf and configures the system:
#   performance  →  Stop NetworkManager so wpa_supplicant has exclusive control
#                   over wlan0 (P2P-only, lower latency).
#   hybrid       →  Ensure NetworkManager is running so wlan0 stays connected to
#                   the home Wi-Fi while the P2P interface handles Miracast.
# =============================================================================
set -euo pipefail

CONFIG="${NICOCAST_CONFIG:-/etc/nicocast/nicocast.conf}"
LOG_TAG="nicocast-prestart"

_log() { logger -t "${LOG_TAG}" "$*" || echo "[${LOG_TAG}] $*" >&2; }

# ─── Read operation_mode from config ─────────────────────────────────────────
MODE="hybrid"   # safe default
if [[ -f "${CONFIG}" ]]; then
    # Extract the value; strip inline comments and whitespace
    _raw=$(grep -E "^[[:space:]]*operation_mode[[:space:]]*=" "${CONFIG}" 2>/dev/null \
           | tail -1 \
           | sed 's/.*=[[:space:]]*//' \
           | sed 's/[[:space:]]*#.*//' \
           | tr -d '[:space:]')
    if [[ -n "${_raw}" ]]; then
        MODE="${_raw}"
    fi
fi

_log "operation_mode = ${MODE}"

# ─── Act on the mode ──────────────────────────────────────────────────────────
case "${MODE}" in
    performance)
        _log "Performance mode: stopping NetworkManager for exclusive wpa_supplicant control."
        if systemctl is-active --quiet NetworkManager 2>/dev/null; then
            systemctl stop NetworkManager
            _log "NetworkManager stopped."
        else
            _log "NetworkManager is already inactive – nothing to do."
        fi
        ;;
    hybrid)
        _log "Hybrid mode: ensuring NetworkManager is running alongside P2P interface."
        if ! systemctl is-active --quiet NetworkManager 2>/dev/null; then
            systemctl start NetworkManager 2>/dev/null || \
                _log "WARNING: Could not start NetworkManager (may not be installed)."
        else
            _log "NetworkManager is running – no changes made."
        fi
        _log "WARNING: Active Wi-Fi scanning by NetworkManager may increase video latency."
        ;;
    *)
        _log "WARNING: Unknown operation_mode '${MODE}' – defaulting to hybrid behaviour."
        ;;
esac
