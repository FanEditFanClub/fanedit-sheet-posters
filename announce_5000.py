#!/usr/bin/env python3
"""One-off 5,000 files milestone announcement.

Posts to: Discord #cyber-chat (via "Fan Edit Fan Club" webhook, falls back to
Optimus bot), X (Buffer), Facebook Page (Graph API).
Triggered manually via workflow_dispatch for the 11:00 AM announcement.
"""
from __future__ import annotations

import json
import sys
import urllib.request

from common import env, log
from destinations import (
    DestinationError,
    post_discord,
    resolve_discord_channel_id,
    post_buffer_x,
    resolve_buffer_profile_id,
    post_facebook_page,
)

DISCORD_TEXT = """\U0001f3ac **MILESTONE ALERT** \U0001f3ac

THE WORLD'S LARGEST FAN EDIT COLLECTION just keeps growing \u2014 we've now catalogued over **5,000 fan edits**! \U0001f30d\U0001f525

Thousands more are coming. This is only the beginning.

DM us for the link to browse the collection!"""

X_TEXT = ("\U0001f3ac MILESTONE! THE WORLD'S LARGEST FAN EDIT COLLECTION now has "
          "over 5,000 fan edits catalogued! \U0001f30d\U0001f525 Thousands more "
          "coming! DM us for the link! #FanEdit #FanEditFanClub")

FB_TEXT = DISCORD_TEXT.replace("**", "")

WEBHOOK_NAME = "Fan Edit Fan Club"


def _discord_api(token: str, path: str, method: str = "GET",
                 body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://discord.com/api/v10{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bot {token}",
                 "Content-Type": "application/json",
                 "User-Agent": "fanedit/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def post_via_club_webhook(token: str, channel_id: str, text: str) -> bool:
    """Post to channel via a 'Fan Edit Fan Club' webhook; create if missing."""
    hooks = _discord_api(token, f"/channels/{channel_id}/webhooks")
    if not isinstance(hooks, list):
        raise DestinationError(f"webhook list failed: {str(hooks)[:150]}")
    hook = next((h for h in hooks if h.get("name") == WEBHOOK_NAME), None)
    if not hook:
        hook = _discord_api(token, f"/channels/{channel_id}/webhooks",
                            "POST", {"name": WEBHOOK_NAME})
        if not hook.get("token"):
            raise DestinationError(
                f"webhook create failed: {str(hook)[:150]}")
        log("created 'Fan Edit Fan Club' webhook")
    wid, wtoken = hook["id"], hook["token"]
    req = urllib.request.Request(
        f"https://discord.com/api/v10/webhooks/{wid}/{wtoken}",
        method="POST", data=json.dumps({"content": text[:2000]}).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "fanedit/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status not in (200, 204):
            raise DestinationError(f"webhook post HTTP {r.status}")
    return True


def main() -> int:
    dry = env("DRY_RUN") == "1"
    bot_token = env("DISCORD_BOT_TOKEN")
    buffer_token = env("BUFFER_TOKEN")
    fb_token = env("FACEBOOK_PAGE_TOKEN")
    fb_page_id = env("FACEBOOK_PAGE_ID", "1240699955783812")

    if dry:
        print("=== DISCORD (via Fan Edit Fan Club webhook) ===\n" + DISCORD_TEXT)
        print("\n=== X ===\n" + X_TEXT)
        print("\n=== FB PAGE ===\n" + FB_TEXT)
        return 0

    errors = []

    # Discord #cyber-chat via "Fan Edit Fan Club" webhook (fallback: bot)
    try:
        cid = resolve_discord_channel_id(bot_token, "\U0001f4d0cyber-chat\U0001f4ac")
        try:
            post_via_club_webhook(bot_token, cid, DISCORD_TEXT)
            log("discord #cyber-chat: posted via Fan Edit Fan Club webhook")
        except Exception as e:  # noqa: BLE001
            log(f"webhook failed ({e}); falling back to bot post")
            post_discord(bot_token, cid, DISCORD_TEXT[:2000])
            log("discord #cyber-chat: posted via bot (fallback)")
    except Exception as e:  # noqa: BLE001
        errors.append(f"discord: {e}")
        log(f"discord FAILED: {e}")

    # X via Buffer
    if env("SKIP_X") == "true":
        log("x: skipped (SKIP_X=true)")
    else:
        try:
            pid = resolve_buffer_profile_id(buffer_token, "faneditfanclub")
            post_buffer_x(buffer_token, pid, X_TEXT)
            log("x: posted via buffer")
        except Exception as e:  # noqa: BLE001
            errors.append(f"x: {e}")
            log(f"x FAILED: {e}")

    # Facebook Page via Graph API
    if env("SKIP_FB") == "true":
        log("facebook page: skipped (SKIP_FB=true)")
    else:
        try:
            post_facebook_page(fb_token, fb_page_id, FB_TEXT)
            log("facebook page: posted")
        except Exception as e:  # noqa: BLE001
            errors.append(f"fb page: {e}")
            log(f"fb page FAILED: {e}")

    if errors:
        log(f"ERRORS: {errors}")
        return 1
    log("all three posted successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
