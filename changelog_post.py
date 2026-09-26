#!/usr/bin/env python3
"""Daily changelog poster for #discord-moderators.

Reads changelog/entries.jsonl (one JSON object per line: {"ts": ISO-8601,
"text": "..."}), posts every entry newer than the last post as a
release-notes-style bullet list, then watermarks state/changelog.json so
entries are never posted twice.

Entries are appended by the agent whenever a Fan Edit Fan Club platform,
channel, or automation ships a change (see AGENTS.md changelog rule).
Quiet days post nothing.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import load_state, log  # noqa: E402
from destinations import post_discord, resolve_discord_channel_id  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
ENTRIES = os.path.join(BASE, "changelog", "entries.jsonl")
STATE_NAME = "changelog.json"


def env(k: str, d: str = "") -> str:
    return os.environ.get(k, d) or d


def load_entries() -> list:
    out = []
    try:
        with open(ENTRIES, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    ts = dt.datetime.fromisoformat(str(e.get("ts", "")))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=dt.timezone.utc)
                except (TypeError, ValueError):
                    continue
                text = " ".join(str(e.get("text", "")).split())
                if text:
                    out.append((ts, text))
    except FileNotFoundError:
        log("no changelog entries file yet")
    out.sort(key=lambda x: x[0])
    return out


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    st = load_state(STATE_NAME, {})
    if st.get("last_post_utc"):
        since = dt.datetime.fromisoformat(st["last_post_utc"])
        if since.tzinfo is None:
            since = since.replace(tzinfo=dt.timezone.utc)
    else:
        since = now - dt.timedelta(hours=24)

    new = [(ts, t) for ts, t in load_entries() if ts > since]
    if not new:
        log("no new changelog entries - skipping post")
        return 0

    date_label = now.astimezone(
        dt.timezone(dt.timedelta(hours=-4))).strftime("%b %d")
    lines = [f"**Fan Edit Fan Club \u2014 Daily Update \U0001f916** "
             f"({date_label})",
             ""]
    for _, text in new:
        lines.append(f"\u2022 {text}")
    msg = "\n".join(lines)[:1950]

    token = env("DISCORD_BOT_TOKEN")
    if not token:
        print("DRY RUN (no DISCORD_BOT_TOKEN):")
        print(msg)
        return 0

    cid = resolve_discord_channel_id(
        token, env("CHANGELOG_CHANNEL", "discord-moderators"))
    post_discord(token, cid, msg)
    log(f"posted {len(new)} changelog bullets to "
        f"#{env('CHANGELOG_CHANNEL', 'discord-moderators')}")

    from common import save_state
    save_state(STATE_NAME, {"last_post_utc": now.isoformat()})
    try:
        from run import push_state
        push_state("changelog")
    except Exception as e:  # noqa: BLE001
        log(f"state push issue: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
