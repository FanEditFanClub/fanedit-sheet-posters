#!/usr/bin/env python3
"""Set the 'Fan Edit Fan Club' webhook avatar in #cyber-chat."""
from __future__ import annotations

import base64
import json
import sys
import urllib.request

from common import env, log

CHANNEL_ID = "1529915534466875546"
WEBHOOK_NAME = "Fan Edit Fan Club"
ICON_PATH = "discord-icon.png"


def _api(token: str, path: str, method: str = "GET",
         body: dict | None = None) -> dict | list:
    req = urllib.request.Request(
        f"https://discord.com/api/v10{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bot {token}",
                 "Content-Type": "application/json",
                 "User-Agent": "fanedit/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def main() -> int:
    token = env("DISCORD_BOT_TOKEN")

    # Find the webhook
    hooks = _api(token, f"/channels/{CHANNEL_ID}/webhooks")
    if not isinstance(hooks, list):
        log(f"failed to list webhooks: {str(hooks)[:200]}")
        return 1
    hook = next((h for h in hooks if h.get("name") == WEBHOOK_NAME), None)
    if not hook:
        log(f"webhook '{WEBHOOK_NAME}' not found")
        return 1

    # Read and encode the icon
    with open(ICON_PATH, "rb") as f:
        img = base64.b64encode(f.read()).decode()
    data_uri = f"data:image/png;base64,{img}"
    log(f"icon encoded: {len(img)} chars")

    # Update the webhook avatar
    result = _api(token, f"/webhooks/{hook['id']}", "PATCH",
                  {"avatar": data_uri})
    if result.get("avatar"):
        log("webhook avatar updated successfully")
        return 0
    log(f"avatar update failed: {str(result)[:200]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
