#!/usr/bin/env python3
"""Clear TorrentDay H&R warnings on a schedule.

Each tick:
  1. GET /seed_back.php with the user's session cookies.
  2. Parse the H&R table for `<button class="jxi" data-jxi="...">` rows.
     Two buttons per row: "Clear with N Bonus Points" and "Clear with X GB
     Upload Credit". CLEAR_METHOD selects which one to fire.
  3. Sort cheapest-first, cap by MAX_*_PER_RUN, then POST each button's
     data-jxi payload to /V3/API/API.php (the same endpoint the site's
     jQuery .jxi handler hits).
  4. Pushover summary.

The data-jxi value is an already-urlencoded form body (Fn=...&id=...&token=...)
that the site's JS sends verbatim. We do the same.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Literal

import requests
from bs4 import BeautifulSoup


log = logging.getLogger("td-hr-pruner")

ClearMethod = Literal["upload_credit", "bonus_points"]


# ---------- Models ----------

@dataclass
class ClearAction:
    """One pending H&R clear: which torrent, which method, the payload."""
    torrent_name: str
    method: ClearMethod
    cost_label: str       # human-readable, e.g. "9.75 GB" or "682"
    cost_units: float     # for capping: bytes for upload_credit, points for bonus_points
    data_jxi: str         # opaque urlencoded body sent verbatim


# ---------- Parsing ----------

# Matches "Clear with 9.75 GB Upload Credit", "Clear with 500 MB Upload Credit",
# "Clear with 1.38 K Bonus Points", "Clear with 682 Bonus Points", etc.
_CLEAR_RE = re.compile(
    r"Clear\s+with\s+([\d.]+)\s*([KMGT]?B|K)?\s*(Upload\s+Credit|Bonus\s+Points)",
    re.IGNORECASE,
)

_UNIT_BYTES = {
    "B": 1,
    "KB": 1024,
    "MB": 1024 ** 2,
    "GB": 1024 ** 3,
    "TB": 1024 ** 4,
}


def _parse_cost(label_value: str, label_unit: str | None, kind: ClearMethod) -> tuple[float, str]:
    """Return (units, human_label).

    units = bytes for upload_credit, raw points for bonus_points.
    """
    val = float(label_value)
    if kind == "upload_credit":
        unit = (label_unit or "B").upper()
        if unit == "K":  # site abbreviates KB as just "K" in some places — defensive
            unit = "KB"
        bytes_ = val * _UNIT_BYTES.get(unit, 1)
        return bytes_, f"{label_value} {label_unit or ''}".strip()
    # bonus_points: "K" suffix means *1000 (e.g. "1.38 K Bonus Points")
    if (label_unit or "").upper() == "K":
        val *= 1000
    return val, label_value + (f" {label_unit}" if label_unit else "")


def parse_clear_actions(html: str, method: ClearMethod) -> list[ClearAction]:
    """Extract every clear button matching `method` from the H&R page HTML.

    Returns one ClearAction per H&R torrent. If a torrent has no button for the
    requested method, it's skipped (logged at debug).
    """
    soup = BeautifulSoup(html, "html.parser")
    out: list[ClearAction] = []

    # Each H&R lives in a <tr class="browse"> with a name cell + clear-button cell.
    for tr in soup.select("tr.browse"):
        name_cell = tr.select_one("td:first-child")
        torrent_name = name_cell.get_text(strip=True) if name_cell else "<unknown>"

        chosen: ClearAction | None = None
        for btn in tr.select("button.jxi"):
            text = btn.get_text(" ", strip=True)
            m = _CLEAR_RE.search(text)
            if not m:
                continue
            value, unit, kind_text = m.group(1), m.group(2), m.group(3)
            btn_method: ClearMethod = (
                "upload_credit" if "upload" in kind_text.lower() else "bonus_points"
            )
            if btn_method != method:
                continue
            data_jxi = btn.get("data-jxi", "")
            if not data_jxi:
                log.warning("button missing data-jxi for %s; skipping", torrent_name)
                continue
            units, label = _parse_cost(value, unit, btn_method)
            chosen = ClearAction(
                torrent_name=torrent_name,
                method=btn_method,
                cost_label=label,
                cost_units=units,
                data_jxi=data_jxi,
            )
            break

        if chosen is None:
            log.debug("no %s button for %s; skipping", method, torrent_name)
            continue
        out.append(chosen)

    return out


# ---------- TorrentDay client ----------

class TDClient:
    def __init__(self, base_url: str, cookie_header: str, user_agent: str, verify_tls: bool = True):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.verify = verify_tls
        self.session.headers.update({
            "User-Agent": user_agent,
            "Cookie": cookie_header,
            "Referer": f"{self.base_url}/seed_back.php",
            "Origin": self.base_url,
            "X-Requested-With": "XMLHttpRequest",
        })

    def fetch_hr_page(self) -> str:
        # Don't send Cookie via the kwarg — it's already in self.session.headers.
        r = self.session.get(f"{self.base_url}/seed_back.php", timeout=30)
        r.raise_for_status()
        if "/login" in r.url or "Login" in r.text[:200]:
            raise RuntimeError("auth failed — cookies look stale or invalid")
        return r.text

    def fire_clear(self, action: ClearAction) -> dict:
        # The site's jQuery handler POSTs the raw data-jxi string as the form
        # body (after $(this).serialize() which is empty for a button with no
        # form siblings). Content-Type is application/x-www-form-urlencoded.
        r = self.session.post(
            f"{self.base_url}/V3/API/API.php",
            data=action.data_jxi,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        r.raise_for_status()
        try:
            payload = r.json()
        except ValueError:
            raise RuntimeError(f"non-JSON response: {r.text[:200]}")
        # Engine.api.init does `alert(i.er)` if `er` is set — i.e. that's an error.
        if isinstance(payload, dict) and payload.get("er"):
            raise RuntimeError(f"API error: {payload['er']}")
        return payload


# ---------- Pushover ----------

def pushover_notify(cfg: "Config", title: str, message: str, priority: int = 0) -> None:
    if not cfg.pushover_token or not cfg.pushover_user:
        log.debug("pushover not configured; skipping notification: %s", title)
        return
    try:
        r = requests.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": cfg.pushover_token,
                "user": cfg.pushover_user,
                "title": title,
                "message": message[:1024],
                "priority": priority,
            },
            timeout=15,
        )
        r.raise_for_status()
    except Exception:
        log.exception("failed to send pushover notification")


# ---------- Pruning ----------

def _fmt_units(units: float, method: ClearMethod) -> str:
    if method == "upload_credit":
        if units >= 1024 ** 4:
            return f"{units / 1024**4:.2f} TB"
        if units >= 1024 ** 3:
            return f"{units / 1024**3:.2f} GB"
        if units >= 1024 ** 2:
            return f"{units / 1024**2:.0f} MB"
        return f"{units:.0f} B"
    return f"{units:.0f} points"


def select_within_cap(actions: list[ClearAction], cap_units: float) -> tuple[list[ClearAction], list[ClearAction]]:
    """Cheapest-first selection. Returns (chosen, skipped_due_to_cap)."""
    sorted_actions = sorted(actions, key=lambda a: a.cost_units)
    chosen: list[ClearAction] = []
    skipped: list[ClearAction] = []
    spent = 0.0
    for a in sorted_actions:
        if spent + a.cost_units <= cap_units:
            chosen.append(a)
            spent += a.cost_units
        else:
            skipped.append(a)
    return chosen, skipped


def prune_once(cfg: "Config") -> None:
    client = TDClient(cfg.base_url, cfg.cookies, cfg.user_agent, verify_tls=cfg.verify_tls)
    html = client.fetch_hr_page()
    actions = parse_clear_actions(html, cfg.method)

    if not actions:
        log.info("no H&Rs to clear")
        return

    cap_units = (
        cfg.max_tb_per_run * 1024 ** 4
        if cfg.method == "upload_credit"
        else cfg.max_bonus_per_run
    )
    chosen, skipped = select_within_cap(actions, cap_units)
    total_cost = sum(a.cost_units for a in chosen)

    log.info("found %d H&Rs; clearing %d with %s (~%s); skipping %d over cap",
             len(actions), len(chosen), cfg.method, _fmt_units(total_cost, cfg.method), len(skipped))

    for a in chosen:
        log.info("  - %s (%s)", a.torrent_name, a.cost_label)
    for a in skipped:
        log.info("  [skip-cap] %s (%s)", a.torrent_name, a.cost_label)

    if cfg.dry_run:
        log.info("DRY RUN — no clears performed")
        return

    cleared_actions: list[ClearAction] = []
    failed_actions: list[tuple[ClearAction, str]] = []
    for a in chosen:
        try:
            client.fire_clear(a)
            cleared_actions.append(a)
            log.info("cleared: %s", a.torrent_name)
        except Exception as e:
            log.warning("failed to clear %s: %s", a.torrent_name, e)
            failed_actions.append((a, str(e)))
        time.sleep(cfg.click_delay)

    spent = sum(a.cost_units for a in cleared_actions)
    remaining = len(skipped) + len(failed_actions)

    # Title: shows the headline numbers so it's readable from the Pushover banner.
    title = f"td-hr-pruner: cleared {len(cleared_actions)}"
    if remaining:
        title += f" ({remaining} remain)"

    body_lines = [
        f"Cleared {len(cleared_actions)}/{len(chosen)} H&Rs ({_fmt_units(spent, cfg.method)} spent).",
    ]
    if remaining:
        breakdown = []
        if skipped:
            breakdown.append(f"{len(skipped)} over cap")
        if failed_actions:
            breakdown.append(f"{len(failed_actions)} failed")
        body_lines.append(f"{remaining} remain — " + ", ".join(breakdown) + ".")
    if failed_actions:
        # First failure reason (truncated) is usually enough context.
        first = failed_actions[0]
        body_lines.append(f"First failure: {first[0].torrent_name} — {first[1][:120]}")

    msg = "\n".join(body_lines)
    log.info(msg.replace("\n", " | "))

    if cleared_actions or failed_actions:
        pushover_notify(
            cfg,
            title,
            msg,
            priority=1 if failed_actions else 0,
        )


# ---------- Config ----------

@dataclass
class Config:
    base_url: str
    cookies: str
    user_agent: str
    verify_tls: bool

    method: ClearMethod
    max_tb_per_run: float
    max_bonus_per_run: float
    click_delay: float

    poll_interval: int
    continuous: bool
    dry_run: bool

    pushover_token: str
    pushover_user: str


def env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise SystemExit(f"missing required env var: {name}")
    return val or ""


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_cookies() -> str:
    """Load cookies from TD_COOKIES env var, or from /config/cookies.txt."""
    raw = os.environ.get("TD_COOKIES", "").strip()
    if raw:
        return raw
    path = os.environ.get("TD_COOKIES_FILE", "/config/cookies.txt")
    if os.path.isfile(path):
        with open(path) as f:
            return f.read().strip()
    raise SystemExit(
        "missing cookies — set TD_COOKIES env var or mount /config/cookies.txt "
        "(format: 'uid=...; pass=...; ...')"
    )


def load_config(args: argparse.Namespace) -> Config:
    method_raw = env("CLEAR_METHOD", "upload_credit").strip().lower()
    if method_raw not in ("upload_credit", "bonus_points"):
        raise SystemExit(f"CLEAR_METHOD must be 'upload_credit' or 'bonus_points', got {method_raw!r}")

    return Config(
        base_url=env("TD_BASE_URL", "https://www.torrentday.com"),
        cookies=load_cookies(),
        user_agent=env(
            "TD_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        ),
        verify_tls=env_bool("TD_VERIFY_TLS", True),

        method=method_raw,  # type: ignore[arg-type]
        max_tb_per_run=float(env("MAX_TB_PER_RUN", "10")),
        max_bonus_per_run=float(env("MAX_BONUS_PER_RUN", "50000")),
        click_delay=float(env("CLICK_DELAY", "1.0")),

        poll_interval=int(env("POLL_INTERVAL", "10800")),
        continuous=env_bool("CONTINUOUS", True) and not args.once,
        dry_run=args.dry_run or env_bool("DRY_RUN", False),

        pushover_token=env("PUSHOVER_TOKEN", ""),
        pushover_user=env("PUSHOVER_USER", ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear TorrentDay H&R warnings on a schedule.")
    parser.add_argument("--once", action="store_true", help="Run a single tick and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Plan but don't fire clears.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cfg = load_config(args)
    log.info("td-hr-pruner starting: method=%s poll=%ds dry_run=%s",
             cfg.method, cfg.poll_interval, cfg.dry_run)

    while True:
        try:
            prune_once(cfg)
        except Exception:
            log.exception("tick failed")
            pushover_notify(cfg, "td-hr-pruner: tick failed", "see container logs", priority=1)

        if not cfg.continuous:
            return 0
        log.info("sleeping %ds", cfg.poll_interval)
        time.sleep(cfg.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
