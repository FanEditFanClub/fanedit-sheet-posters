#!/usr/bin/env python3
"""Daily AI digest for #cyberchat: recap of the last 24h of fan-edit activity.

Sources (same as the instant posters, read-only here):
  - Reddit: the fan_edit_fan_club_reddit_feed multireddit scan JSON
    (published by the daily 5pm ET browser scan; items carry exact
    timestamps, so "last 24h" is exact).
  - Sheet: the Fan Edit Central Database 'Newest Additions' tab. New rows
    append at the bottom, so a row-count watermark captures "since yesterday".
  - Collection: the 9am notifier's posted-log
    (state/posted_log.json in the private
    New-Fan-Edit-Fan-Club-Collection-File-Notifier repo) — files it posted
    to all channels in the last 24h. Needs the NOTIFIER_PAT secret
    (repo-contents read on that repo); without it the section is skipped.

The item lists go to the Gemini API (free tier) for a short hype-style recap,
posted to Discord as the Optimus bot. Deterministic count lines are always
appended: new Fan Edit Fan Club Collection files (a Discord channel mention,
never a file or Drive link) and new Fan Edit Central Database rows. If Gemini
is unavailable the digest still goes out as plain prose plus those counts.
Quiet days get a one-line note. Reddit Top picks keep their links.

Env:
  DISCORD_BOT_TOKEN   (required) - bot token
  GEMINI_API_KEY      (optional) - Google AI Studio key; without it the digest
                      falls back to the plain prose format
  NOTIFIER_PAT          (optional) - PAT with contents:read on the private
                      notifier repo; without it the collection section is
                      skipped
  COLLECTION_DISCORD_CHANNEL_ID (optional) - overrides the Collection channel
                      mention. Otherwise config.json collection.discord_channel_id
                      is used, then the notifier repo's channel/webhook config.
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
import re
import sys
import urllib.parse
import urllib.request

from common import env, load_config, load_state, save_state, log
from destinations import (DISCORD_API, DISCORD_UA, DestinationError,
                          _discord_headers, _get, post_discord,
                          resolve_discord_channel_id,
                          resolve_discord_user_mention)

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


REDDIT_UA = "fanedit-digest/1.0 (by /u/faneditfanclub)"


def fetch_scan_items(scan_url: str, since: dt.datetime) -> list:
    """Scan-JSON items newer than `since`.

    The daily 5pm ET browser scan publishes the multireddit listing to
    GitHub Pages (Reddit's public RSS died 2026-11-13). Same return shape
    as the old RSS reader: [{title, url, published}].
    """
    req = urllib.request.Request(scan_url, headers={"User-Agent": REDDIT_UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    items = []
    for p in data.get("posts", []):
        title = (p.get("title") or "").strip()
        link = (p.get("url") or "").strip()
        try:
            published = dt.datetime.fromisoformat(
                (p.get("published") or "").strip().replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError, AttributeError):
            continue
        if published >= since and title:
            items.append({"title": title, "url": link,
                          "subreddit": (p.get("subreddit") or "").strip(),
                          "author": (p.get("author") or "").strip(),
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
    if any(_looks_like_link(str(e.get("name") or "")) for e in out):
        log("stripped link(s) from collection file names before the recap")
    return out


# ------------------------------------------------------- link hygiene
#
# Collection file and Drive folder links stay out of the recap. People
# request those by DM or by reacting in the Collection channel. Reddit
# Top picks are added later and are the only links allowed through.

_URL_RE = re.compile(r"https?://\S+", re.I)
_ANGLE_URL_RE = re.compile(r"<(https?://[^>\s]+)>", re.I)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((?:<)?https?://[^)\s>]+(?:>)?\)", re.I)
_BARE_DRIVE_RE = re.compile(
    r"\b(?:drive|docs)\.google\.com\S*|\bgoogleusercontent\.com\S*", re.I)
_WEBHOOK_RE = re.compile(
    r"https?://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/"
    r"(\d+)/([A-Za-z0-9_\-]+)",
    re.I,
)
_CHANNEL_LINK_RE = re.compile(
    r"https?://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/"
    r"\d+/(\d{17,20})",
    re.I,
)
_SNOWFLAKE_RE = re.compile(r"(?<!\d)(\d{17,20})(?!\d)")


def _looks_like_link(text: str) -> bool:
    low = text.lower()
    return ("http://" in low or "https://" in low or "drive.google" in low
            or "docs.google" in low)


def _redact(msg: str) -> str:
    msg = _WEBHOOK_RE.sub("https://discord.com/api/webhooks/<redacted>", msg)
    msg = re.sub(r"(?i)(authorization:\s*)\S+", r"\1<redacted>", msg)
    return msg


def strip_prohibited_links(text: str) -> str:
    """Drop URLs from prose. Collection/Drive links must never be posted.

    Markdown link labels are kept when they are titles rather than URLs.
    Discord channel mentions (<#id>) are not URLs and are left alone.
    """
    def _label(match: re.Match) -> str:
        label = (match.group(1) or "").strip()
        if not label or _looks_like_link(label):
            return ""
        return label

    text = _MD_LINK_RE.sub(_label, text)
    text = _ANGLE_URL_RE.sub("", text)
    text = _URL_RE.sub("", text)
    text = _BARE_DRIVE_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    text = re.sub(r"\(\s*\)", "", text)
    return text.strip()


def _safe_reddit_url(url: str) -> bool:
    """True for a Reddit post URL. Drive and other hosts never pass."""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host or "google" in host:
        return False
    return host in ("reddit.com", "redd.it") or host.endswith(".reddit.com")


def _safe_label(text: str) -> str:
    return strip_prohibited_links(norm(text)).strip(" \"'")


# --------------------------------------------- collection channel mention

def _collection_name_score(name: str) -> int:
    n = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    if "collection" not in n:
        return 0
    score = 70
    if "fan" in n:
        score += 10
    if "edit" in n:
        score += 10
    if "club" in n:
        score += 5
    if any(bad in n for bad in ("cleanup", "request", "moderator", "error")):
        score -= 40
    return score


def _ckey(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _gh_json(pat: str, url: str):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": REDDIT_UA,
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _notifier_paths(pat: str) -> list:
    if not pat:
        return []
    last_err = "no branch tried"
    for branch in ("main", "master"):
        url = (f"https://api.github.com/repos/{NOTIFIER_REPO}/git/trees/"
               f"{branch}?recursive=1")
        try:
            data = _gh_json(pat, url)
        except Exception as e:  # noqa: BLE001
            last_err = _redact(f"{type(e).__name__}: {e}")[:180]
            continue
        if data.get("truncated"):
            log("notifier repo tree was truncated")
        paths = [n.get("path", "") for n in data.get("tree", [])
                 if n.get("type") == "blob" and n.get("path")]
        log(f"notifier repo tree: {len(paths)} files on {branch}")
        return paths
    log(f"notifier repo tree fetch failed ({last_err})")
    return []


def _interesting_notifier_path(path: str) -> bool:
    low = path.lower()
    if "posted_log" in low or low.startswith("state/"):
        return False
    if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip",
                     ".woff", ".lock", ".mp4", ".mkv")):
        return False
    name = low.rsplit("/", 1)[-1]
    if name.startswith("readme"):
        return True
    if "/workflows/" in f"/{low}" or low.startswith(".github/"):
        return True
    return any(k in low for k in (
        "discord", "webhook", "channel", "config", "notify", "secret",
        ".env"))


def _notifier_text(pat: str, path: str) -> str:
    quoted = urllib.parse.quote(path, safe="/")
    url = (f"https://api.github.com/repos/{NOTIFIER_REPO}/contents/"
           f"{quoted}?ref=main")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github.raw",
        "User-Agent": REDDIT_UA,
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(200_000).decode("utf-8", errors="replace")


def _webhook_channel_id(webhook_id: str, webhook_token: str) -> str:
    """Discord returns channel_id for a webhook URL. The token is never logged."""
    url = f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}"
    req = urllib.request.Request(
        url, headers={"User-Agent": DISCORD_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as e:  # noqa: BLE001
        log("discord webhook channel lookup failed: "
            + _redact(f"{type(e).__name__}: {e}")[:160])
        return ""
    return str(data.get("channel_id") or "")


def _bot_text_channels(token: str) -> list:
    if not token:
        return []
    headers = _discord_headers(token)
    try:
        guilds = _get(f"{DISCORD_API}/users/@me/guilds", headers,
                      "discord guilds")
    except DestinationError as e:
        log(f"discord guild list failed: {_redact(str(e))[:160]}")
        return []
    found = []
    for g in guilds if isinstance(guilds, list) else []:
        try:
            channels = _get(
                f"{DISCORD_API}/guilds/{g.get('id', '')}/channels",
                headers, "discord channels")
        except DestinationError:
            continue
        for c in channels if isinstance(channels, list) else []:
            if c.get("type") == 0 and c.get("id"):
                found.append({"id": str(c["id"]),
                              "name": str(c.get("name") or "")})
    return found


def _channel_name(token: str, channel_id: str, known: dict) -> str:
    if channel_id in known:
        return known[channel_id]
    if not token:
        return ""
    try:
        data = _get(f"{DISCORD_API}/channels/{channel_id}",
                    _discord_headers(token), "discord channel")
        name = str(data.get("name") or "") if isinstance(data, dict) else ""
    except DestinationError:
        name = ""
    known[channel_id] = name
    return name


def _add_candidate(cands: dict, cid: str, score: int, reason: str) -> None:
    cid = str(cid or "").strip()
    if not re.fullmatch(r"\d{17,20}", cid):
        return
    prev = cands.get(cid)
    if prev is None or score > prev["score"]:
        cands[cid] = {"id": cid, "score": score, "reason": reason}
    elif prev is not None and reason not in prev["reason"]:
        prev["reason"] = prev["reason"] + "; " + reason


def discover_collection_channel_id(pat: str, token: str) -> tuple:
    """Return (channel_id, reason) for the Collection Discord channel.

    Prefers an id published in the notifier repo (config, channel link, or
    a webhook's channel_id). Uses the bot's channel list to confirm names.
    """
    cands: dict = {}
    channels = _bot_text_channels(token)
    by_id = {c["id"]: c["name"] for c in channels}
    for c in channels:
        score = _collection_name_score(c["name"])
        if score:
            _add_candidate(cands, c["id"], score,
                           f"bot-visible channel #{c['name']}")

    paths = [p for p in _notifier_paths(pat) if _interesting_notifier_path(p)]
    paths = paths[:40]
    if paths:
        log("notifier config files scanned: " + ", ".join(paths))
    for path in paths:
        try:
            text = _notifier_text(pat, path)
        except Exception as e:  # noqa: BLE001
            log(f"notifier file skipped ({path}): "
                + _redact(f"{type(e).__name__}")[:120])
            continue
        path_bias = 0
        low_path = path.lower()
        if "collection" in low_path:
            path_bias += 15
        if any(bad in low_path for bad in ("error", "moderator", "cleanup")):
            path_bias -= 25
        for match in _CHANNEL_LINK_RE.finditer(text):
            score = 100 if "collection" in text[
                max(0, match.start() - 180):match.end() + 180].lower() else 50
            _add_candidate(cands, match.group(1), score + path_bias,
                           f"discord channel link in {path}")
        for match in re.finditer(r"<#(\d{17,20})>", text):
            window = text[max(0, match.start() - 180):match.end() + 180]
            score = 90 if "collection" in window.lower() else 45
            _add_candidate(cands, match.group(1), score + path_bias,
                           f"channel mention in {path}")
        for match in re.finditer(
                r"(?i)(channel[ _]?id|discord[ _]?channel(?:[ _]?id)?"
                r"|collection[ _]?channel(?:[ _]?id)?)"
                r"\W{0,40}(\d{17,20})", text):
            score = 95 if "collection" in match.group(1).lower() else 60
            if "collection" in text[
                    max(0, match.start() - 120):match.end() + 120].lower():
                score += 15
            _add_candidate(cands, match.group(2), score + path_bias,
                           f"{match.group(1)} in {path}")
        for match in re.finditer(
                r"(?i)(?:channel|discord)\W{0,40}"
                r"[\"']([a-z0-9][a-z0-9 _\-]{2,80})[\"']", text):
            wanted = _ckey(match.group(1))
            for chan in channels:
                if _ckey(chan["name"]) == wanted:
                    _add_candidate(
                        cands, chan["id"],
                        88 + path_bias + _collection_name_score(chan["name"]) // 10,
                        f"notifier names #{chan['name']} in {path}")
        for match in _WEBHOOK_RE.finditer(text):
            cid = _webhook_channel_id(match.group(1), match.group(2))
            if not cid:
                continue
            name = _channel_name(token, cid, by_id)
            score = 80 + path_bias + _collection_name_score(name)
            via = f"webhook channel_id from {path}"
            if name:
                via += f" (#{name})"
            _add_candidate(cands, cid, score, via)
        for match in re.finditer(r"(?i)collection", text):
            window = text[max(0, match.start() - 80):match.end() + 220]
            for snow in _SNOWFLAKE_RE.findall(window):
                _add_candidate(cands, snow, 55 + path_bias,
                               f"id near 'collection' in {path}")

    for cid, cand in cands.items():
        name = _channel_name(token, cid, by_id)
        extra = _collection_name_score(name)
        if extra:
            cand["score"] += extra // 5
            if name and f"#{name}" not in cand["reason"]:
                cand["reason"] += f" (#{name})"

    if not cands:
        return "", "no candidates"
    ranked = sorted(cands.values(), key=lambda c: (-c["score"], c["id"]))
    summary = "; ".join(
        f"<#{c['id']}> score={c['score']} {c['reason']}" for c in ranked[:6])
    log("collection channel candidates: " + summary)
    top = ranked[0]
    if top["score"] < 70:
        return "", f"low confidence ({top['score']})"
    if len(ranked) > 1 and ranked[1]["score"] >= top["score"] - 5 \
            and ranked[1]["id"] != top["id"]:
        # Prefer the candidate whose reason/name actually says collection.
        def _pref(c: dict) -> tuple:
            name = by_id.get(c["id"], "")
            return (_collection_name_score(name), c["score"])
        top = max(ranked[:2], key=_pref)
    return top["id"], top["reason"]


def resolve_collection_mention(pat: str, token: str, cfg: dict) -> str:
    """Discord <#id> mention for the Fan Edit Fan Club Collection channel."""
    pinned = str(
        env("COLLECTION_DISCORD_CHANNEL_ID")
        or (cfg.get("collection") or {}).get("discord_channel_id")
        or "").strip()
    if re.fullmatch(r"\d{17,20}", pinned):
        name = _channel_name(token, pinned, {})
        log(f"collection channel pinned: {pinned}"
            + (f" (#{name})" if name else ""))
        return f"<#{pinned}>"
    try:
        cid, reason = discover_collection_channel_id(pat, token)
    except Exception as e:  # noqa: BLE001
        log("collection channel discovery failed: "
            + _redact(f"{type(e).__name__}: {e}")[:200])
        cid, reason = "", "error"
    if cid:
        log(f"collection channel resolved: {cid} ({reason})")
        return f"<#{cid}>"
    log("WARNING: Collection Discord channel id was not found "
        f"({reason}); count line will name the channel in text")
    return "the Fan Edit Fan Club Collection"


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
        cands.sort(key=lambda n: n, reverse=True)  # newest generation first
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


def gemini_recap(api_key: str, reddit_items: list,
                 sheet_rows: list, notifier_files: list | None,
                 date_label: str) -> str:
    """Ask Gemini for the recap paragraph ONLY (no headline). The headline is
    prepended deterministically by build_message - never trust the model with
    markup, it mangles the bold markers (seen 2026-10-08: literal ** posted).
    Raises on failure."""
    lines = [
        "You are the Optimus bot writing the daily recap for the Fan Edit",
        "Fan Club Discord server.",
        "",
        "OUTPUT - follow exactly:",
        "Write ONE paragraph of 2-4 sentences of casual, hype, fan-community",
        "chat about what was new in the bot channels since yesterday's post.",
        "Output ONLY the paragraph text - no headline, no title line,",
        "no bold markers, no bullet points, no numbered lists, no line breaks.",
        "(A separate 'Top picks' link list is appended automatically.)",
        "",
        "HARD RULES:",
        "- One flowing paragraph of 2-4 sentences.",
        "- No markdown formatting at all (no **, no headings).",
        "- Be specific: name titles, which subreddit they came from, and",
        "  editors/authors when given; group similar items (e.g. several",
        "  Marvel edits) and call out anything standout.",
        "- Mention the most interesting new items by name (titles, editors).",
        "- Skip any source with nothing new - do not announce emptiness,",
        "  just don't mention it.",
        "- Do not invent titles, editors, or links - only talk about items",
        "  from the lists below.",
        "- NEVER include a URL, markdown link, Google Drive link, folder",
        "  link, or a link to a collection file. Name titles only.",
        "  Collection links are requested by DM or by reacting with the",
        "  emoji in the collection channel. Count lines are appended for",
        "  you, so do not add your own tally or emoji bullets.",
        "- If ALL lists are empty, the paragraph is one chill line like:",
        "  nothing dropped today, feeds are quiet, check back tomorrow.",
        "",
        f"Date: {date_label}",
        "",
        "NEW REDDIT POSTS (last 24h):",
    ]
    if reddit_items:
        for it in reddit_items[:MAX_ITEMS_PER_SOURCE]:
            sub = it.get("subreddit", "")
            au = it.get("author", "")
            tag = f"[r/{sub}] " if sub else ""
            by = f" (posted by u/{au})" if au else ""
            lines.append(f"- {tag}{it['title']}{by}")
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
        added = 0
        for f in notifier_files[:MAX_ITEMS_PER_SOURCE]:
            name = _safe_label(str(f.get("name") or ""))
            if name:
                lines.append(f"- {name}")
                added += 1
        if not added:
            lines.append("(none)")
    else:
        lines.append("(none)")

    prompt = "\n".join(lines)
    def _body(thinking_off: bool) -> bytes:
        gen = {"maxOutputTokens": 2048, "temperature": 0.7}
        if thinking_off:
            gen["thinkingConfig"] = {"thinkingBudget": 0}
        return json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                           "generationConfig": gen}).encode()

    def _call(model: str, body: bytes) -> str:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": api_key})
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.load(r)
        parts = resp["candidates"][0]["content"].get("parts", [])
        return "".join(p.get("text", "") for p in parts
                       if not p.get("thought")).strip()

    last_err = "no model tried"
    for model in _gemini_models(api_key):
        text = ""
        # Try with thinking disabled first; some models reject
        # thinkingConfig / a zero budget (HTTP 400), so retry without it.
        for thinking_off in (True, False):
            try:
                text = _call(model, _body(thinking_off))
                if text:
                    break
                last_err = f"{model}: empty response"
            except Exception as e:  # noqa: BLE001
                last_err = f"{model}: {_gemini_err_text(e)}"
                if getattr(e, "code", None) != 400:
                    break
        if text:
            log(f"gemini model used: {model}")
            return text
    raise RuntimeError(f"gemini recap failed ({last_err})")


# -------------------------------------------------------------- message

def source_bullets(sheet_rows: list, notifier_files: list | None,
                   collection_mention: str) -> str:
    """Per-source counts. Collection is a <#id> mention, never a file link.

    Database count is always shown, including 0. The collection line is
    omitted only when the notifier log could not be read (unknown, not zero).
    """
    lines = []
    if notifier_files is not None:
        n = len(notifier_files)
        word = "fan edit" if n == 1 else "fan edits"
        lines.append(
            f"\u2022 \U0001f4c1 {n} new {word} added to {collection_mention}")
    n = len(sheet_rows or [])
    word = "row" if n == 1 else "rows"
    lines.append(
        "\u2022 \U0001f4ca "
        f"{n} new {word} added to the Fan Edit Central Database")
    return "\n".join(lines)


def assemble_message(head: str, para: str, reddit_items: list,
                     sheet_rows: list, notifier_files: list | None,
                     collection_mention: str) -> str:
    """Headline, prose, count bullets, then Reddit Top picks.

    Prose is scrubbed of every URL so a model or filename cannot leak a
    Collection file or Drive folder link. Top picks are re-attached after.
    """
    para = strip_prohibited_links(para)
    parts = [head, "", para]
    bullets = source_bullets(sheet_rows, notifier_files, collection_mention)
    if bullets:
        parts.extend(["", bullets])
    picks = top_picks(reddit_items)
    if picks:
        parts.extend(["", picks])
    return "\n".join(parts)[:1950]


def build_fallback(mention: str, reddit_items: list, sheet_rows: list,
                   notifier_files: list | None,
                   collection_mention: str) -> str:
    """Plain-prose fallback when Gemini is unavailable.

    No Collection file or Drive links. Count bullets are still included.
    """
    head = f"**{mention} Daily Bot Channel Recap \U0001f916**"
    bits = []
    if reddit_items:
        titles = [f'"{_safe_label(it["title"])}"'
                  for it in reddit_items[:3] if _safe_label(it["title"])]
        more = (f" and {len(reddit_items) - 3} more"
                if len(reddit_items) > 3 else "")
        listed = f" including {', '.join(titles)}{more}" if titles else ""
        bits.append(f"the Reddit feed had {len(reddit_items)} new "
                    f"post{'s' if len(reddit_items) != 1 else ''}{listed}")
    if sheet_rows:
        ents = []
        for r in sheet_rows[:3]:
            editor = _safe_label(r[1]) if len(r) > 1 else ""
            title = _safe_label(r[2])
            if not title:
                continue
            ents.append(f'"{title}"' + (f" by {editor}" if editor else ""))
        more = (f" and {len(sheet_rows) - 3} more"
                if len(sheet_rows) > 3 else "")
        listed = f" including {', '.join(ents)}{more}" if ents else ""
        bits.append(f"the database picked up {len(sheet_rows)} new "
                    f"{'entries' if len(sheet_rows) != 1 else 'entry'}"
                    f"{listed}")
    if notifier_files:
        names = []
        for f in notifier_files[:3]:
            name = _safe_label(str(f.get("name") or ""))
            if name:
                names.append(f'"{name}"')
        more = (f" and {len(notifier_files) - 3} more"
                if len(notifier_files) > 3 else "")
        listed = f" including {', '.join(names)}{more}" if names else ""
        bits.append(f"{len(notifier_files)} new file"
                    f"{'s' if len(notifier_files) != 1 else ''} landed in "
                    f"the collection{listed}")
    if bits:
        para = ("Fresh drops since yesterday: " + "; ".join(bits) + ". "
                "That's everything new in the bot channels.")
    else:
        para = ("Quiet day in the bot channels - nothing new dropped in the "
                "last 24 hours. The feeds are watching, see you tomorrow.")
    return assemble_message(head, para, reddit_items, sheet_rows,
                            notifier_files, collection_mention)


def build_message(mention: str, date_label: str, reddit_items: list,
                  sheet_rows: list, notifier_files: list | None,
                  api_key: str, collection_mention: str) -> str:
    head = f"**{mention} Daily Bot Channel Recap \U0001f916**"
    if not reddit_items and not sheet_rows and not notifier_files:
        para = ("Quiet day in the bot channels - nothing new dropped in "
                "the last 24 hours. The feeds are watching, see you "
                "tomorrow.")
        return assemble_message(head, para, reddit_items, sheet_rows,
                                notifier_files, collection_mention)
    para = ""
    if api_key:
        try:
            para = gemini_recap(api_key, reddit_items, sheet_rows,
                                notifier_files, date_label)
            log("gemini recap ok")
        except Exception as e:  # noqa: BLE001
            log(f"gemini recap failed, using fallback: {e}")
    if not para:
        return build_fallback(mention, reddit_items, sheet_rows,
                              notifier_files, collection_mention)
    # Defensive cleanup: the model is asked for a bare paragraph, but if it
    # emits a headline line or markdown anyway, strip it - the real headline
    # is prepended deterministically below so it can never render broken.
    lines = [ln.strip() for ln in para.strip().splitlines() if ln.strip()]
    if lines and "daily bot channel recap" in lines[0].lower():
        lines = lines[1:]
    para = strip_prohibited_links(" ".join(lines).replace("**", ""))
    if not para:
        return build_fallback(mention, reddit_items, sheet_rows,
                              notifier_files, collection_mention)
    return assemble_message(head, para, reddit_items, sheet_rows,
                            notifier_files, collection_mention)


def top_picks(reddit_items: list, n: int = 5) -> str:
    """Up to n newest Reddit items as masked links; <url> suppresses embeds.

    Only reddit.com / redd.it URLs are included. Drive and other hosts are
    dropped so a Collection or folder link cannot ride along in Top picks.
    """
    if not reddit_items:
        return ""
    rows = []
    for item in reversed(reddit_items[-n:]):
        url = item.get("url") or ""
        if not _safe_reddit_url(url):
            continue
        title = _safe_label(item.get("title") or "")[:120]
        if not title:
            continue
        rows.append(f"\u2022 [{title}](<{url}>)")
    return ("**Top picks:**\n" + "\n".join(rows)) if rows else ""


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
        channel = (env("MANUAL_CHANNEL")
                   or env("DISCORD_DIGEST_CHANNEL", "cyberchat"))
        cid = resolve_discord_channel_id(token, channel)
        post_discord(token, cid, msg)
        log(f"manual message posted to #{channel}")
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
        reddit_items = fetch_scan_items(cfg["reddit"]["scan_url"], since)
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
    mention = ("@FanEditFanClub" if dry and not token
               else resolve_discord_user_mention(token, "faneditfanclub"))
    collection_mention = resolve_collection_mention(
        env("NOTIFIER_PAT"), token, cfg)
    msg = build_message(mention, date_label, reddit_items, new_rows,
                        notifier_files, env("GEMINI_API_KEY"),
                        collection_mention)
    if note:
        msg = f"{msg}\n_{note}_"
    msg = msg[:1950]
    log("digest message:\n" + msg)

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
