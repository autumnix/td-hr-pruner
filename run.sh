#!/bin/bash
# Run td-hr-pruner on macOS for local testing.
# Usage: ./run.sh [--once] [--dry-run] [--verbose]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load .env if present
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a; source "$SCRIPT_DIR/.env"; set +a
fi

# Cookies can be in TD_COOKIES env var or in $SCRIPT_DIR/cookies.txt
if [[ -z "${TD_COOKIES:-}" ]] && [[ -f "$SCRIPT_DIR/cookies.txt" ]]; then
    export TD_COOKIES_FILE="$SCRIPT_DIR/cookies.txt"
fi

if [[ -z "${TD_COOKIES:-}" ]] && [[ -z "${TD_COOKIES_FILE:-}" ]]; then
    echo "ERROR: set TD_COOKIES in .env or drop a cookies.txt next to this script."
    exit 1
fi

python3 -c "import requests, bs4" 2>/dev/null || {
    echo "ERROR: missing deps. pip3 install requests beautifulsoup4"
    exit 1
}

python3 "$SCRIPT_DIR/prune.py" "$@"
