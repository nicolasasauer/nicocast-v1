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
# Seconds to wait for the wpa_supplicant control socket to appear
WPA_SOCKET_TIMEOUT=15

# ─── Determine SD-card log file path from config ─────────────────────────────
_LOG_FILE=""
if [[ -f "${CONFIG}" ]]; then
    _lf=$(grep -E "^[[:space:]]*log_file[[:space:]]*=" "${CONFIG}" 2>/dev/null \
           | tail -1 \
           | sed 's/^[^=]*=[[:space:]]*//' \
           | sed 's/[[:space:]]*#.*//' \
           | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    if [[ -n "${_lf}" ]]; then
        _LOG_FILE="${_lf}"
    fi
fi

_log() {
    local msg="$*"
    # Always log to syslog / journalctl
    logger -t "${LOG_TAG}" "${msg}" 2>/dev/null || echo "[${LOG_TAG}] ${msg}" >&2
    # Also append to the SD-card log file when available
    if [[ -n "${_LOG_FILE}" ]]; then
        local dir
        dir="$(dirname "${_LOG_FILE}")"
        if [[ -d "${dir}" ]]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${LOG_TAG}] ${msg}" >> "${_LOG_FILE}" 2>/dev/null || true
        fi
    fi
}

# ─── Read operation_mode and interface from config ────────────────────────────
MODE="hybrid"   # safe default
IFACE="wlan0"   # safe default
if [[ -f "${CONFIG}" ]]; then
    # Extract operation_mode; strip inline comments and whitespace
    _raw=$(grep -E "^[[:space:]]*operation_mode[[:space:]]*=" "${CONFIG}" 2>/dev/null \
           | tail -1 \
           | sed 's/.*=[[:space:]]*//' \
           | sed 's/[[:space:]]*#.*//' \
           | tr -d '[:space:]')
    if [[ -n "${_raw}" ]]; then
        MODE="${_raw}"
    fi
    # Extract wifi interface
    _iface=$(grep -E "^[[:space:]]*interface[[:space:]]*=" "${CONFIG}" 2>/dev/null \
             | tail -1 \
             | sed 's/.*=[[:space:]]*//' \
             | sed 's/[[:space:]]*#.*//' \
             | tr -d '[:space:]')
    if [[ -n "${_iface}" ]]; then
        IFACE="${_iface}"
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
        # Ensure the standalone wpa_supplicant@<iface> service is running so
        # the P2P control socket is available for NicoCast.
        if ! systemctl is-active --quiet "wpa_supplicant@${IFACE}" 2>/dev/null; then
            _log "Starting wpa_supplicant@${IFACE}…"
            systemctl start "wpa_supplicant@${IFACE}" 2>/dev/null || \
                _log "WARNING: Could not start wpa_supplicant@${IFACE} – P2P may not work."
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

# ─── Ensure wpa_supplicant socket is ready ────────────────────────────────────
WPA_SOCKET="/var/run/wpa_supplicant/${IFACE}"
_log "Waiting for wpa_supplicant control socket at ${WPA_SOCKET}…"
_WAITED=0
while [[ ! -S "${WPA_SOCKET}" ]] && [[ ${_WAITED} -lt ${WPA_SOCKET_TIMEOUT} ]]; do
    sleep 1
    _WAITED=$(( _WAITED + 1 ))
done
if [[ -S "${WPA_SOCKET}" ]]; then
    _log "wpa_supplicant socket ready (waited ${_WAITED} s)."
else
    _log "WARNING: wpa_supplicant socket not found at ${WPA_SOCKET} after ${WPA_SOCKET_TIMEOUT} s – P2P may not work."
fi
