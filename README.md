# td-hr-pruner

Scheduled clearing of TorrentDay Hit & Run warnings. Reads `seed_back.php`,
posts each row's clear-button payload to the same `/V3/API/API.php` endpoint
the site's own JavaScript hits.

## Configure

1. Log into TorrentDay in a browser, open DevTools → Application → Cookies,
   and copy the cookie header (every `name=value` joined by `; `).
2. Either:
   - set `TD_COOKIES` in `.env`, **or**
   - drop the cookie string into `/mnt/user/appdata/td-hr-pruner/cookies.txt`.

## Run

Local:
```
cp .env.example .env  # fill in TD_COOKIES
./run.sh --once --dry-run --verbose
```

Unraid (docker-compose):
```
docker compose up -d
```

## Knobs

| env | default | meaning |
| --- | --- | --- |
| `CLEAR_METHOD` | `upload_credit` | `upload_credit` or `bonus_points` |
| `MAX_TB_PER_RUN` | `10` | upload-credit cap per tick |
| `MAX_BONUS_PER_RUN` | `50000` | bonus-point cap per tick |
| `POLL_INTERVAL` | `10800` | seconds between ticks (default 3h) |
| `CLICK_DELAY` | `1.0` | seconds between individual clears |
| `DRY_RUN` | `false` | parse + plan, don't fire |

When the cap is exceeded, cheapest-first selection is used and the leftovers
are deferred to the next tick.
