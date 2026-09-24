"""FB-only redo: post the fixed Buffy post, then delete the old badly-formatted one.
Never exits nonzero - prints FB_REPOSTED / FB_FAILED / FB_OLD_DELETED / FB_OLD_NOT_FOUND."""
import json
import os
import urllib.parse
import urllib.request

from destinations import post_facebook_page

fb_token = os.environ["FACEBOOK_PAGE_TOKEN"]
cfg = json.load(open("config.json"))
tpl = cfg["templates"]["sheet"]
vals = {
    "editor": "DeloresOlaf",
    "title": "Buffy the Vampire Slayer: Acid Party",
    "original": "Buffy the Vampire Slayer: Season 1-4",
}
fb_text = tpl["facebook"].format(**vals)
print("FB_TEXT:", fb_text)
page_id = cfg["facebook"]["page_id"]
FB_LINK = "https://faneditcentral.wixsite.com/home"

try:
    post_facebook_page(fb_token, page_id, fb_text, link=FB_LINK)
    print("FB_REPOSTED")
except Exception as e:
    print("FB_FAILED", str(e)[:300].replace(fb_token, "***"))


def fb_get(path, params):
    url = "https://graph.facebook.com/v21.0/" + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as r:
        return json.load(r)


try:
    feed = fb_get(page_id + "/posts",
                  {"fields": "id,message,created_time", "limit": "10",
                   "access_token": fb_token})
    old_id = None
    for p in feed.get("data", []):
        msg = p.get("message", "")
        if "Acid Party" in msg and "A Fan Edit of \n\n" in msg:
            old_id = p["id"]
            break
    if old_id:
        del_url = ("https://graph.facebook.com/v21.0/" + old_id + "?access_token="
                   + urllib.parse.quote(fb_token, safe=""))
        with urllib.request.urlopen(urllib.request.Request(del_url, method="DELETE"),
                                    timeout=60) as r:
            print("FB_OLD_DELETED", old_id, r.read()[:100])
    else:
        print("FB_OLD_NOT_FOUND")
except Exception as e:
    print("FB_DELETE_FAILED", str(e)[:300].replace(fb_token, "***"))
