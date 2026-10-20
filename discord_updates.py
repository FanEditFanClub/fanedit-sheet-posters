#!/usr/bin/env python3
"""Post new official Discord blog posts to #discord-moderators.

The mod team wants to see Discord platform updates (feature launches,
changelogs, patch notes, safety changes) as they drop. This watches the
official Discord blog RSS feed and posts each new item to the moderators
channel via the Optimus bot.

Runs on GitHub Actions: .github/workflows/discord-updates.yml (daily).
First run baselines (marks everything seen, posts nothing) so the channel
isn't spammed with the archive.

Env:
  DISCORD_BOT_TOKEN   (required) - bot token
  DISCORD_MOD_CHANNEL (default "discord-moderators")
  GITHUB_PAT / GITHUB_REPO / GITHUB_BRANCH - state persistence (same as run.py)
  DRY_RUN=1 - print what would post instead of posting (no state saved)
"""
from __future__ import annotations

import sys
import urllib.request
import xml.etree.ElementTree as ET

from common import env, load_state, save_state, log
from destinations import (DestinationError, post_discord,
                          resolve_discord_channel_id)

BLOG_RSS = "https://discord.com/blog/rss.xml"
RSS_UA = ("fanedit-discord-updates/1.0 "
          "(+https://github.com/FanEditFanClub/fanedit-sheet-posters)")
STATE_NAME = "discord_updates.json"


def fetch_items() -> list:
    """Fetch and parse the Discord blog RSS feed, newest first."""
    req = urllib.request.Request(BLOG_RSS, headers={"User-Agent": RSS_UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    root = ET.fromstring(raw)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or "").strip() or link
        if title and link:
            items.append({"title": title, "link": link, "guid": guid})
    return items


def push_state_quietly() -> None:
    try:
        from run import push_state
        push_state("discord-updates")
    except Exception as e:  # noqa: BLE001
        log(f"state push issue: {e}")


def main() -> int:
    dry = env("DRY_RUN") == "1"
    token = env("DISCORD_BOT_TOKEN")
    if not token and not dry:
        log("FATAL: DISCORD_BOT_TOKEN is not set")
        return 1
    channel = env("DISCORD_MOD_CHANNEL", "discord-moderators")

    try:
        items = fetch_items()
    except Exception as e:  # noqa: BLE001
        log(f"blog RSS fetch failed: {e}")
        return 1
    log(f"blog RSS: {len(items)} items")

    st = load_state(STATE_NAME, {})
    seen = set(st.get("seen", []))
    if not st:
        # First run: baseline silently (never blast the archive).
        save_state(STATE_NAME, {"seen": [i["guid"] for i in items]})
        push_state_quietly()
        log(f"BASELINE: marked {len(items)} items seen, posted nothing")
        return 0

    new = [i for i in items if i["guid"] not in seen]
    new.reverse()  # oldest first, so the channel reads chronologically
    log(f"{len(new)} new items")
    if not new:
        return 0

    if dry:
        for i in new:
            print(f"WOULD POST: {i['title']} — {i['link']}")
        return 0

    try:
        cid = resolve_discord_channel_id(token, channel)
    except DestinationError as e:
        log(f"channel resolve failed: {e}")
        return 1

    failed = 0
    for i in new:
        msg = f"\U0001f4e2 **Discord update:** {i['title']}\n{i['link']}"
        try:
            post_discord(token, cid, msg[:2000])
            seen.add(i["guid"])
            log(f"posted: {i['title'][:80]}")
        except DestinationError as e:
            failed += 1
            log(f"post failed for {i['link']}: {e}")
    # Only items that posted are marked seen, so failures retry next run.
    save_state(STATE_NAME, {"seen": sorted(seen)})
    push_state_quietly()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
