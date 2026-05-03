#!/bin/bash
set -e

# Drop to PUID/PGID if running as root (matches sync-from-audible / qbittorrent-pruner pattern)
if [ "$(id -u)" = "0" ] && [ -n "${PUID}" ]; then
    groupadd -o -g "${PGID:-100}" appgroup 2>/dev/null || true
    useradd -o -u "${PUID}" -g "${PGID:-100}" -d /config -s /bin/bash appuser 2>/dev/null || true

    if [ -f "${TD_COOKIES_FILE:-/config/cookies.txt}" ]; then
        chown "${PUID}:${PGID:-100}" "${TD_COOKIES_FILE:-/config/cookies.txt}" 2>/dev/null || true
        chmod 600 "${TD_COOKIES_FILE:-/config/cookies.txt}" 2>/dev/null || true
    fi

    exec gosu "${PUID}:${PGID:-100}" python3 -u /app/prune.py "$@"
fi

exec python3 -u /app/prune.py "$@"
