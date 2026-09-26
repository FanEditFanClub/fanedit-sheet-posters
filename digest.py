#!/usr/bin/env python3
"""Daily AI digest for #cyberchat: recap of the last 24h of fan-edit activity.

Sources (same as the instant posters, read-only here):
  - Reddit: the fan_edit_fan_club_reddit_feed multireddit RSS (items have
    pubDates, so "last 24h" is exact).
  - Sheet: the Fan Edit Central Database 'Newest Additions' tab. New rows
    append at the bottom, so a row-count watermark captures "since yesterday".
  - Collection: the 9am notifier's posted-log
    (state/posted_log.json in the private
    New-Fan-Edit-Fan-Club-Collection-File-Notifier repo) — files it posted
    to all channels in the last 24h. Needs the NOTIFIER_PAT secret
    (repo-contents read on that repo); without it the section is skipped.

The item lists go to the Gemini API (free tier) for a short hype-style recap,
posted to Discord as the Optimus bot. If Gemini is unavailable the digest
still goes out as a plain bullet list. Quiet days get a one-line note.

Env:
  DISCORD_BOT_TOKEN   (required) - bot token
  GEMINI_API_KEY      (optional) - Google AI Studio key; without it the digest
                      falls back to the plain list format
  NOTIFIER_PAT          (optional) - PAT with contents:read on the private
                      notifier repo; without it the collection section is
                      skipped
  DISCORD_DIGEST_CHANNEL (default "cyberchat")
  DISCORD_ERROR_CHANNEL  - errors surface here
  GITHUB_PAT / GITHUB_REPO / GITHUB_BRANCH - state persistence (same as run.py)
  DRY_RUN=1 - print the message instead of posting (no state saved)
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import base64
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from common import env, load_config, load_state, save_state, log
from destinations import (DestinationError, post_discord,
                          resolve_discord_channel_id,
                          resolve_discord_user_mention)

REDDIT_UA = "fanedit-digest/1.0 (by /u/faneditfanclub)"
# Overridable without a code change: set GEMINI_MODEL in the workflow env.
GEMINI_MODELS = tuple(
    m for m in (
        __import__("os").environ.get("GEMINI_MODEL", "").strip(),
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
    )
    if m
)
STATE_NAME = "digest.json"
MAX_ITEMS_PER_SOURCE = 40


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------- sources

def fetch_sheet_rows(sheet_id: str, tab: str) -> list:
    """Same CSV-export read as sync_sheet.py (public link, no credentials)."""
    params = urllib.parse.urlencode({"tqx": "out:csv", "sheet": tab})
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(raw)))
    data = rows[1:] if rows else []
    return [r for r in data
            if len(r) > 2 and r[2].strip()
            and not r[2].strip().lower().startswith("testing")]


def norm(v: str) -> str:
    return " ".join(v.split())


def fetch_reddit_items(rss_url: str, since: dt.datetime) -> list:
    """RSS items newer than `since`. Descriptive UA or Reddit 403s.

    Reddit rate-limits aggressively; retry 429/5xx with backoff.
    """
    import time
    last_err: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(rss_url, headers={"User-Agent": REDDIT_UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            break
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                wait = 15 * (attempt + 1)
                log(f"reddit {e.code}, retrying in {wait}s "
                    f"(attempt {attempt + 1}/4)")
                time.sleep(wait)
                continue
            raise
    else:
        raise last_err  # type: ignore[misc]
    # NOTE: Reddit's .rss endpoints serve Atom, not RSS 2.0.
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(raw)
    items = []
    for entry in root.findall("a:entry", ns):
        title_el = entry.find("a:title", ns)
        link_el = entry.find("a:link", ns)
        updated_el = entry.find("a:updated", ns)
        title = (title_el.text or "").strip() if title_el is not None else ""
        link = link_el.get("href", "").strip() if link_el is not None else ""
        try:
            published = dt.datetime.fromisoformat(
                (updated_el.text or "").strip().replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError, AttributeError):
            continue
        if published >= since and title:
            items.append({"title": title, "url": link,
                          "published": published.isoformat()})
    items.sort(key=lambda i: i["published"])
    return items


# ------------------------------------------------------------------ AI

NOTIFIER_REPO = ("FanEditFanClub/"
                   "New-Fan-Edit-Fan-Club-Collection-File-Notifier")
NOTIFIER_LOG_PATH = "state/posted_log.json"


def fetch_notifier_log(pat: str, since: dt.datetime) -> list | None:
    """Fetch the 9am notifier's posted-log from the private repo.

    Returns [{name, posted}] for entries posted within the window, or None
    when the log can't be read (no PAT / fetch failed / file missing yet).
    """
    if not pat:
        log("no NOTIFIER_PAT secret — collection section will be skipped")
        return None
    url = (f"https://api.github.com/repos/{NOTIFIER_REPO}/contents/"
           f"{NOTIFIER_LOG_PATH}?ref=main")
    try:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "User-Agent": REDDIT_UA,
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(base64.b64decode(
                json.load(r)["content"]).decode())
    except Exception as e:  # noqa: BLE001
        log(f"notifier log fetch failed: {e}")
        return None
    entries = (payload.get("entries", [])
               if isinstance(payload, dict) else [])
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        try:
            ts = dt.datetime.fromisoformat(e.get("posted_utc", ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            continue
        if ts >= since:
            out.append({"name": e.get("name", "?"), "posted": ts})
    out.sort(key=lambda x: x["posted"])
    log(f"notifier log: {len(out)} collection files posted in window")
    return out


def _gemini_models(api_key: str) -> list:
    """Ask the API which flash models this key can actually use.

    Model names retire fast (2.0 -> 2.5 -> 3.x in 2026), so resolve
    dynamically instead of hardcoding. Falls back to the static list.
    """
    static = [m for m in GEMINI_MODELS if not m.startswith("models/")]
    try:
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models?"
            "pageSize=100",
            headers={"x-goog-api-key": api_key})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        cands = []
        for m in data.get("models", []):
            name = m.get("name", "").replace("models/", "")
            if ("generateContent" not in
                    m.get("supportedGenerationMethods", [])):
                continue
            if "flash" not in name or "image" in name or "tts" in name \
                    or "transcribe" in name or "live" in name:
                continue
            cands.append(name)
        # prefer full flash over lite, newer generations first
        cands.sort(key=lambda n: ("lite" in n, n), reverse=False)
        full = [c for c in cands if "lite" not in c]
        lite = [c for c in cands if "lite" in c]
        ordered = full + lite
        if ordered:
            return ordered
    except Exception as e:  # noqa: BLE001
        log(f"gemini model list failed ({e}), using static list")
    return static


def _gemini_err_text(e: Exception) -> str:
    try:
        body = e.read().decode("utf-8", errors="replace")  # type: ignore
        return f"{type(e).__name__} {getattr(e, 'code', '?')}: {body[:200]}"
    except Exception:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


def gemini_recap(api_key: str, mention: str, reddit_items: list,
                 sheet_rows: list, notifier_files: list | None,
                 date_label: str) -> str:
    """Ask Gemini for a short Discord-formatted recap. Raises on failure."""
    head = f"**{mention} Daily Bot Channel Recap \U0001f916**"
    lines = [
        "You are the Optimus bot writing the daily recap for the Fan Edit",
        "Fan Club Discord server.",
        "",
        "OUTPUT FORMAT - follow exactly:",
        f"Line 1 (the headline, copy verbatim): {head}",
        "Line 2: blank line",
        "Line 3: ONE single paragraph of casual, hype, fan-community chat",
        "about what was new in the bot channels since yesterday's post.",
        "",
        "HARD RULES:",
        "- No bullet points, no numbered lists, no line breaks inside the",
        "  paragraph - one flowing paragraph only.",
        "- No links or URLs anywhere in the paragraph.",
        "- No markdown headings.",
        "- Keep the paragraph under 700 characters.",
        "- Mention the most interesting new items by name (titles, editors).",
        "- Skip any source with nothing new - do not announce emptiness,",
        "  just don't mention it.",
        "- Do not invent titles, editors, or links - only talk about items",
        "  from the lists below.",
        "- If ALL lists are empty, the paragraph is one chill line like:",
        "  nothing dropped today, feeds are quiet, check back tomorrow.",
        "",
        f"Date: {date_label}",
        "",
        "NEW REDDIT POSTS (last 24h):",
    ]
    if reddit_items:
        for it in reddit_items[:MAX_ITEMS_PER_SOURCE]:
            lines.append(f"- {it['title']} - {it['url']}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("NEW FAN EDIT CENTRAL DATABASE ENTRIES:")
    if sheet_rows:
        for r in sheet_rows[:MAX_ITEMS_PER_SOURCE]:
            editor = norm(r[1]) if len(r) > 1 else ""
            title = norm(r[2])
            original = norm(r[3]) if len(r) > 3 else ""
            by = f" by {editor}" if editor else ""
            of = f" (a fan edit of {original})" if original else ""
            lines.append(f'- "{title}"{by}{of}')
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("NEW FILES ADDED TO THE FAN EDIT FAN CLUB COLLECTION "
                 "(via the 9am notifier):")
    if notifier_files is None:
        lines.append("(notifier log unavailable)")
    elif notifier_files:
        for f in notifier_files[:MAX_ITEMS_PER_SOURCE]:
            lines.append(f"- {f['name']}")
    else:
        lines.append("(none)")

    prompt = "\n".join(lines)
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 600, "temperature": 0.7},
    }).encode()

    last_err = "no model tried"
    for model in _gemini_models(api_key):
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": api_key})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                resp = json.load(r)
            text = (resp["candidates"][0]["content"]["parts"][0]["text"]
                    .strip())
            if text:
                return text
            last_err = f"{model}: empty response"
        except Exception as e:  # noqa: BLE001
            last_err = f"{model}: {_gemini_err_text(e)}"
            continue
    raise RuntimeError(f"gemini recap failed ({last_err})")


# -------------------------------------------------------------- message

def build_fallback(mention: str, reddit_items: list, sheet_rows: list,
                   notifier_files: list | None) -> str:
    """Plain-prose fallback when Gemini is unavailable. No bullets, no links."""
    head = f"**{mention} Daily Bot Channel Recap \U0001f916**"
    bits = []
    if reddit_items:
        titles = [f'"{norm(it["title"])}"' for it in reddit_items[:3]]
        more = (f" and {len(reddit_items) - 3} more"
                if len(reddit_items) > 3 else "")
        bits.append(f"the Reddit feed had {len(reddit_items)} new "
                    f"post{'s' if len(reddit_items) != 1 else ''} including "
                    f"{', '.join(titles)}{more}")
    if sheet_rows:
        ents = []
        for r in sheet_rows[:3]:
            editor = norm(r[1]) if len(r) > 1 else ""
            title = norm(r[2])
            ents.append(f'"{title}"' + (f" by {editor}" if editor else ""))
        more = (f" and {len(sheet_rows) - 3} more"
                if len(sheet_rows) > 3 else "")
        bits.append(f"the database picked up {len(sheet_rows)} new "
                    f"{'entries' if len(sheet_rows) != 1 else 'entry'} "
                    f"including {', '.join(ents)}{more}")
    if notifier_files:
        names = [f'"{f["name"]}"' for f in notifier_files[:3]]
        more = (f" and {len(notifier_files) - 3} more"
                if len(notifier_files) > 3 else "")
        bits.append(f"{len(notifier_files)} new file"
                    f"{'s' if len(notifier_files) != 1 else ''} landed in "
                    f"the collection including {', '.join(names)}{more}")
    if bits:
        para = ("Fresh drops since yesterday: " + "; ".join(bits) + ". "
                "That's everything new in the bot channels.")
    else:
        para = ("Quiet day in the bot channels - nothing new dropped in the "
                "last 24 hours. The feeds are watching, see you tomorrow.")
    return f"{head}\n\n{para}"


def build_message(mention: str, date_label: str, reddit_items: list,
                  sheet_rows: list, notifier_files: list | None,
                  api_key: str) -> str:
    head = f"**{mention} Daily Bot Channel Recap \U0001f916**"
    if not reddit_items and not sheet_rows and not notifier_files:
        return (head + "\n\nQuiet day in the bot channels - nothing new "
                "dropped in the last 24 hours. The feeds are watching, "
                "see you tomorrow.")
    body = ""
    if api_key:
        try:
            body = gemini_recap(api_key, mention, reddit_items, sheet_rows,
                                notifier_files, date_label)
            log("gemini recap ok")
        except Exception as e:  # noqa: BLE001
            log(f"gemini recap failed, using fallback: {e}")
    if not body:
        body = build_fallback(mention, reddit_items, sheet_rows,
                              notifier_files)
    return body[:1950]


# ----------------------------------------------------------------- main

def main() -> int:
    dry = env("DRY_RUN") == "1"
    try:
        token = env("DISCORD_BOT_TOKEN")
        if not token and not dry:
            raise RuntimeError("DISCORD_BOT_TOKEN is not set")
    except RuntimeError as e:
        log(f"FATAL: {e}")
        return 1

    cfg = load_config()
    now = utcnow()

    manual = env("MANUAL_MESSAGE")
    if manual and not dry:
        # One-off post: skip sources/state entirely, just publish the text.
        # Use {headline} in the text as a placeholder for the title line.
        mention = resolve_discord_user_mention(token, "faneditfanclub")
        head = f"**{mention} Daily Bot Channel Recap \U0001f916**"
        msg = manual.replace("{headline}", head)[:1950]
        cid = resolve_discord_channel_id(
            token, env("DISCORD_DIGEST_CHANNEL", "cyberchat"))
        post_discord(token, cid, msg)
        log("manual digest message posted")
        return 0

    st = load_state(STATE_NAME, {})
    baselined = bool(st)

    since = (dt.datetime.fromisoformat(st["last_run_utc"])
             if st.get("last_run_utc") else now - dt.timedelta(hours=24))
    if since.tzinfo is None:
        since = since.replace(tzinfo=dt.timezone.utc)
    # Clamp: never look back more than 36h (avoids a giant catch-up post
    # after an outage), never forward.
    since = max(since, now - dt.timedelta(hours=36))
    since = min(since, now)

    try:
        reddit_items = fetch_reddit_items(cfg["reddit"]["rss_url"], since)
    except Exception as e:  # noqa: BLE001
        log(f"reddit read failed: {e}")
        reddit_items = None
    try:
        rows = fetch_sheet_rows(cfg["sheet"]["id"], cfg["sheet"]["tab"])
    except Exception as e:  # noqa: BLE001
        log(f"sheet read failed: {e}")
        rows = None

    if reddit_items is None or rows is None:
        err = "daily digest: source read failed " \
              f"(reddit={'ok' if reddit_items is not None else 'FAIL'}, " \
              f"sheet={'ok' if rows is not None else 'FAIL'})"
        log(err)
        if not dry:
            try:
                from run import alert, build_ctx
                alert(build_ctx(cfg), err)
            except Exception:  # noqa: BLE001
                pass
        return 1

    if not baselined:
        # First run: watermark the sheet silently (never blast 4,500 rows),
        # digest only the last-24h reddit window.
        new_rows: list = []
        note = ("Database tracking starts today - entries will appear in "
                "tomorrow's digest.")
        log(f"BASELINE digest: watermarked {len(rows)} sheet rows, "
            f"{len(reddit_items)} reddit items in window.")
    else:
        new_rows = rows[st.get("sheet_row_count", 0):]
        note = ""
        log(f"digest window: {len(reddit_items)} reddit items, "
            f"{len(new_rows)} new sheet rows.")

    # Collection files: read the 9am notifier's posted-log from its
    # private repo (needs NOTIFIER_PAT).
    notifier_files = fetch_notifier_log(env("NOTIFIER_PAT"), since)

    date_label = now.astimezone(
        dt.timezone(dt.timedelta(hours=-4))).strftime("%b %d")
    mention = resolve_discord_user_mention(token, "faneditfanclub")
    msg = build_message(mention, date_label, reddit_items, new_rows,
                        notifier_files, env("GEMINI_API_KEY"))
    if note:
        msg = f"{msg}\n_{note}_"
    msg = msg[:1950]

    if dry:
        print("=" * 60)
        print(msg)
        print("=" * 60)
        return 0

    try:
        cid = resolve_discord_channel_id(
            token, env("DISCORD_DIGEST_CHANNEL", "cyberchat"))
        post_discord(token, cid, msg)
        log(f"digest posted to #{env('DISCORD_DIGEST_CHANNEL', 'cyberchat')}")
    except Exception as e:  # noqa: BLE001
        log(f"digest post failed: {e}")
        try:
            from run import alert, build_ctx
            alert(build_ctx(cfg), f"daily digest post failed: {e}")
        except Exception:  # noqa: BLE001
            pass
        return 1

    save_state(STATE_NAME, {"last_run_utc": now.isoformat(),
                            "sheet_row_count": len(rows)})
    try:
        from run import push_state
        push_state("digest")
    except Exception as e:  # noqa: BLE001
        log(f"state push issue: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
