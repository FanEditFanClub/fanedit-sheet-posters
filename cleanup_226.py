#!/usr/bin/env python3
"""Delete the 226 migrated-shortcut posts from this morning's 9AM notifier run.

Deletes Optimus bot messages in #fan-edit-central-database posted between
2026-10-21 13:00 and 13:30 UTC (9:00-9:30 AM EDT).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error

from common import env, log

CHANNEL_ID = "1529952712261636106"  # #fan-edit-central-database
# 9:00 AM EDT = 13:00 UTC on 2026-10-21
# Discord snowflake for 2026-10-21 13:00:00 UTC:
# (unix_ms - 1420070400000) << 22
START_MS = 1761042000000  # 2026-10-21 13:00:00 UTC
END_MS = 1761043800000    # 2026-10-21 13:30:00 UTC
START_SNOW = str((START_MS - 1420070400000) << 22)
END_SNOW = str((END_MS - 1420070400000) << 22)


def _api(token: str, path: str, method: str = "GET") -> dict | list:
    req = urllib.request.Request(
        f"https://discord.com/api/v10{path}", method=method,
        headers={"Authorization": f"Bot {token}",
                 "User-Agent": "fanedit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry = float(e.headers.get("Retry-After", "5"))
            log(f"rate limited, waiting {retry}s")
            time.sleep(retry + 1)
            return _api(token, path, method)
        raise


def main() -> int:
    dry = env("DRY_RUN") == "1"
    token = env("DISCORD_BOT_TOKEN")

    # Get bot's own user ID to filter only our messages
    me = _api(token, "/users/@me")
    bot_id = me["id"]
    log(f"bot id: {bot_id}")

    # Fetch messages in the window
    messages = _api(
        token,
        f"/channels/{CHANNEL_ID}/messages?after={START_SNOW}&limit=100")
    if not isinstance(messages, list):
        log(f"failed to list messages: {str(messages)[:200]}")
        return 1

    # Filter to bot's messages in the time window
    targets = []
    for m in messages:
        mid = int(m["id"])
        if mid < int(START_SNOW) or mid > int(END_SNOW):
            continue
        if m.get("author", {}).get("id") != bot_id:
            continue
        targets.append(m)

    log(f"found {len(targets)} bot messages in the 9:00-9:30 AM window")

    if dry:
        for m in targets[:5]:
            content = m.get("content", "")[:80]
            print(f"  would delete {m['id']}: {content}")
        if len(targets) > 5:
            print(f"  ... and {len(targets)-5} more")
        return 0

    deleted = 0
    for m in targets:
        try:
            _api(token, f"/channels/{CHANNEL_ID}/messages/{m['id']}",
                 "DELETE")
            deleted += 1
            if deleted % 20 == 0:
                log(f"deleted {deleted}/{len(targets)}")
            time.sleep(0.5)  # avoid rate limits
        except Exception as e:  # noqa: BLE001
            log(f"failed to delete {m['id']}: {e}")

    log(f"done: deleted {deleted}/{len(targets)}")
    return 0 if deleted == len(targets) else 1


if __name__ == "__main__":
    sys.exit(main())
