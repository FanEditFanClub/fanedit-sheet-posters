#!/usr/bin/env python3
"""Poll the Fan Edit Central Database 'Newest Additions' tab; post new rows.

Reads the sheet through its public CSV export (the sheet is shared
"anyone with the link"), so no Google credentials are needed.

First run baselines (marks existing rows seen, posts nothing) so history is
never blasted out. Returns a report dict; run.py aggregates and alerts.
"""
from __future__ import annotations

import csv
import copy
import io
import urllib.parse
import urllib.request

from common import env, load_config, load_state, save_state, log
from destinations import (DestinationError, post_buffer_x, post_discord,
                          post_facebook_page, resolve_buffer_profile_id,
                          resolve_discord_channel_id)

DESTS = ("discord", "x", "facebook")
FB_LINK = "https://faneditcentral.wixsite.com/home"


def fetch_rows(sheet_id: str, tab: str) -> list:
    params = urllib.parse.urlencode({"tqx": "out:csv", "sheet": tab})
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?{params}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(raw)))
    # Row 1 is the header row; data starts at row 2 (matches old A2:L range).
    # Columns: A=date, B=fan editor, C=fan edit title, D=original title.
    return rows[1:] if rows else []


def row_key(row: list) -> str:
    title = row[2].strip().lower() if len(row) > 2 else ""
    editor = row[1].strip().lower() if len(row) > 1 else ""
    return f"{title}||{editor}"


def run(cfg: dict, ctx: dict) -> dict:
    report = {"source": "sheet", "new_posts": 0, "errors": [], "handoffs": []}
    try:
        rows = [r for r in fetch_rows(cfg["sheet"]["id"], cfg["sheet"]["tab"])
                if len(r) > 2 and r[2].strip()
                and not r[2].strip().lower().startswith("testing")]
    except Exception as e:  # noqa: BLE001
        report["errors"].append(f"sheet read: {e}")
        return report

    st = load_state("seen_sheet.json", {"seen_keys": [], "baselined": False})
    seen = set(st["seen_keys"])

    if not st.get("baselined"):
        st["seen_keys"] = sorted({row_key(r) for r in rows})
        st["baselined"] = True
        save_state("seen_sheet.json", st)
        log(f"BASELINE sheet: marked {len(st['seen_keys'])} existing rows seen, posted nothing.")
        return report

    verify = load_state("first_live_verify.json", {})
    # Merge in defaults defensively: a missing or partial verify file must
    # never crash a run (KeyError after posts already went out). deepcopy so
    # we never mutate the shared VERIFY_DEFAULT in ctx.
    for _job, _flags in ctx["verify_default"].items():
        verify.setdefault(_job, copy.deepcopy(_flags))
    new_rows = [r for r in rows if row_key(r) not in seen]
    if new_rows and not ctx.get("buffer_pid"):
        try:
            ctx["buffer_pid"] = resolve_buffer_profile_id(
                ctx["buffer_token"], cfg["buffer"]["channel_name"])
        except DestinationError as e:
            report["errors"].append(f"buffer profile resolve: {e}")

    for r in new_rows:
        editor = r[1].strip() if len(r) > 1 else ""
        title = r[2].strip()
        original = r[3].strip() if len(r) > 3 else ""
        tpl = cfg["templates"]["sheet"]
        texts = {d: tpl[d].format(editor=editor, title=title, original=original)
                 for d in DESTS}
        results: dict = {}
        for d in DESTS:
            try:
                if d == "discord":
                    post_discord(ctx["discord_token"],
                                 ctx["discord_sheet_cid"], texts[d])
                elif d == "x":
                    post_buffer_x(ctx["buffer_token"],
                                  ctx["buffer_pid"], texts[d])
                else:
                    post_facebook_page(ctx["fb_token"], ctx["fb_page_id"],
                                       texts[d], link=FB_LINK)
                results[d] = True
            except Exception as e:  # noqa: BLE001
                results[d] = f"ERROR: {e}"
        if all(v is True for v in results.values()):
            seen.add(row_key(r))
            report["new_posts"] += 1
            for dest, flag in (("discord", "discord_notified"),
                               ("x", "x_notified"),
                               ("facebook", "facebook_notified")):
                if not verify["sheet"][flag]:
                    verify["sheet"][flag] = True
                    report["handoffs"].append(f"sheet.{dest}")
        else:
            for dest, res in results.items():
                if res is not True:
                    report["errors"].append(f"sheet->{dest}: {res}")

    st["seen_keys"] = sorted(seen)
    save_state("seen_sheet.json", st)
    save_state("first_live_verify.json", verify)
    return report


if __name__ == "__main__":
    import json
    import sys
    sys.path.insert(0, ".")
    print("use run.py as the entrypoint")
