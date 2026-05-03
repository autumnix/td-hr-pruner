# td-hr-pruner

Scheduled clearing of TorrentDay Hit & Run warnings. Reads `seed_back.php`,
posts each row's clear-button payload to the same `/V3/API/API.php` endpoint
the site's own JavaScript hits.

## Configure

### Get the cookie string

Log into TorrentDay in Chrome / Firefox, then:

1. **DevTools → Network tab.** Reload `seed_back.php`.
2. Click the request for `seed_back.php` → **Headers** → **Request Headers**.
3. Copy the entire value of the `Cookie:` header (it'll look like
   `uid=12345; pass=abc...; cf_clearance=...; ...`).

Then either:

- set `TD_COOKIES` in `.env` next to `docker-compose.yml`, **or**
- drop the cookie string (one line, no leading `Cookie:`) into
  `/mnt/user/appdata/td-hr-pruner/cookies.txt`.

### Pushover (optional)

Set `PUSHOVER_TOKEN` (your app's API token) and `PUSHOVER_USER` (your user
key) in `.env`. Each tick that touches at least one H&R sends a summary:

```
Title: td-hr-pruner: cleared 8 (3 remain)
Body:  Cleared 8/8 H&Rs (12.45 GB spent).
       3 remain — 2 over cap, 1 failed.
       First failure: Some.Torrent.Name — API error: ...
```

Failures bump priority to 1 so the notification bypasses quiet hours.

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
