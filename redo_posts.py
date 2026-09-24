"""Temp one-off: repost the Buffy row to Facebook + X with fixed templates,
then delete the old badly-formatted posts. New posts go up FIRST so there
is never a gap. Run once via the temp posters.yml, then delete this file."""
import json
import os
import urllib.parse
import urllib.request

from destinations import (
    post_buffer_x,
    post_facebook_page,
    resolve_buffer_profile_id,
)

cfg = json.load(open("config.json"))
tpl = cfg["templates"]["sheet"]
vals = {
    "editor": "DeloresOlaf",
    "title": "Buffy the Vampire Slayer: Acid Party",
    "original": "Buffy the Vampire Slayer: Season 1-4",
}
x_text = tpl["x"].format(**vals)
fb_text = tpl["facebook"].format(**vals)
print("X_TEXT:", x_text)
print("FB_TEXT:", fb_text)

buf_token = os.environ["BUFFER_TOKEN"]
fb_token = os.environ["FACEBOOK_PAGE_TOKEN"]
page_id = cfg["facebook"]["page_id"]
FB_LINK = "https://faneditcentral.wixsite.com/home"

# 1. repost X with fixed formatting
pid = resolve_buffer_profile_id(buf_token, cfg["buffer"]["channel_name"])
post_buffer_x(buf_token, pid, x_text)
print("X_REPOSTED")

# 2. repost Facebook with fixed formatting
post_facebook_page(fb_token, page_id, fb_text, link=FB_LINK)
print("FB_REPOSTED")


def fb_get(path, params):
    url = "https://graph.facebook.com/v21.0/" + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as r:
        return json.load(r)


# 3. find and delete the old FB post (old template had "A Fan Edit of <blank line>")
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


def buf_gql(query):
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        "https://api.buffer.com", data=body,
        headers={"Authorization": "Bearer " + buf_token,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


# 4. delete the old Buffer/X post (try both documented input field names)
OLD_BUF_ID = "6ab553d0457da0b99bc6edee"
for id_field in ("id", "postId"):
    q = ("mutation { deletePost(input: { " + id_field + ': "' + OLD_BUF_ID + '" }) '
         "{ __typename ... on DeletePostSuccess { post { id status } } "
         "... on MutationError { message } } }")
    resp = buf_gql(q)
    errs = resp.get("errors") or []
    data = (resp.get("data") or {}).get("deletePost") or {}
    print("BUFFER_DELETE_ATTEMPT", id_field,
          "typename=", data.get("__typename"),
          "errors=", str(errs)[:200],
          "message=", data.get("message"))
    if data.get("__typename") == "DeletePostSuccess":
        print("BUFFER_DELETE_OK")
        break
