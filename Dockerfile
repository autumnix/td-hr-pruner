FROM python:3.12-slim

# gosu for PUID/PGID drop, ca-certificates for TLS
RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY prune.py .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /app/prune.py

# Mount cookies.txt here (or pass TD_COOKIES env directly)
VOLUME ["/config"]

# Defaults — match Unraid nobody:users
ENV PUID=99 \
    PGID=100 \
    TD_BASE_URL="https://www.torrentday.com" \
    TD_VERIFY_TLS=true \
    CLEAR_METHOD=upload_credit \
    MAX_TB_PER_RUN=10 \
    MAX_BONUS_PER_RUN=50000 \
    CLICK_DELAY=1.0 \
    POLL_INTERVAL=10800 \
    CONTINUOUS=true \
    DRY_RUN=false \
    STATE_DIR=/config \
    AUTH_ALERT_INTERVAL=86400

ENTRYPOINT ["/entrypoint.sh"]
