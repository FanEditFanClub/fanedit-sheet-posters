"""Temp one-off: delete the old badly-formatted Buffy X post from Buffer."""
import json
import os
import urllib.request

buf_token = os.environ["BUFFER_TOKEN"]


def buf_gql(query):
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        "https://api.buffer.com", data=body,
        headers={"Authorization": "Bearer " + buf_token,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


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
