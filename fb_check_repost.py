"""One-off: check the FB page for a good-format Buffy post; repost only if missing."""
import json
import os
import urllib.parse
import urllib.request

from destinations import post_facebook_page

fb_token = os.environ["FACEBOOK_PAGE_TOKEN"]
cfg = json.load(open("config.json"))
page_id = cfg["facebook"]["page_id"]
FB_LINK = "https://faneditcentral.wixsite.com/home"


def fb_get(path, params):
    url = "https://graph.facebook.com/v21.0/" + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as r:
        return json.load(r)


feed = fb_get(page_id + "/posts",
              {"fields": "id,message,created_time", "limit": "10",
               "access_token": fb_token})
good_up = False
for p in feed.get("data", []):
    msg = p.get("message", "")
    snippet = msg[:80].replace("\n", "\\n")
    print("POST", p.get("created_time"), p["id"], snippet)
    if "Acid Party" in msg and "\n\n" not in msg:
        good_up = True

if good_up:
    print("FB_ALREADY_UP")
else:
    tpl = cfg["templates"]["sheet"]
    vals = {"editor": "DeloresOlaf",
            "title": "Buffy the Vampire Slayer: Acid Party",
            "original": "Buffy the Vampire Slayer: Season 1-4"}
    fb_text = tpl["facebook"].format(**vals)
    print("FB_TEXT:", fb_text)
    try:
        post_facebook_page(fb_token, page_id, fb_text, link=FB_LINK)
        print("FB_REPOSTED")
    except Exception as e:
        print("FB_FAILED", str(e)[:300].replace(fb_token, "***"))
