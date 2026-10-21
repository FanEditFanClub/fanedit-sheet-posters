#!/usr/bin/env python3
"""One-off 5,000 files milestone announcement.

Posts to: Discord #cyber-chat (Optimus bot), X (Buffer), Facebook Page (Graph API).
Triggered manually via workflow_dispatch for the 11:00 AM announcement.
"""
from __future__ import annotations

import sys

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


def main() -> int:
    dry = env("DRY_RUN") == "1"
    bot_token = env("DISCORD_BOT_TOKEN")
    buffer_token = env("BUFFER_TOKEN")
    fb_token = env("FACEBOOK_PAGE_TOKEN")
    fb_page_id = env("FACEBOOK_PAGE_ID", "1240699955783812")

    if dry:
        print("=== DISCORD ===\n" + DISCORD_TEXT)
        print("\n=== X ===\n" + X_TEXT)
        print("\n=== FB PAGE ===\n" + FB_TEXT)
        return 0

    errors = []

    # Discord #cyber-chat
    try:
        cid = resolve_discord_channel_id(bot_token, "cyber-chat")
        post_discord(bot_token, cid, DISCORD_TEXT[:2000])
        log("discord #cyber-chat: posted")
    except Exception as e:  # noqa: BLE001
        errors.append(f"discord: {e}")
        log(f"discord FAILED: {e}")

    # X via Buffer
    try:
        pid = resolve_buffer_profile_id(buffer_token, "faneditfanclub")
        post_buffer_x(buffer_token, pid, X_TEXT)
        log("x: posted via buffer")
    except Exception as e:  # noqa: BLE001
        errors.append(f"x: {e}")
        log(f"x FAILED: {e}")

    # Facebook Page via Graph API
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
