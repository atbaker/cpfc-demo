#!/usr/bin/env python3
"""Copy ngrok's current HTTPS tunnel URL into the dashboard QR code."""

import json
import sys
import urllib.request

ADMIN_TOKEN = "palace-admin-2026"


def main() -> int:
    try:
        with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=4) as response:
            tunnels = json.load(response)["tunnels"]
        public_url = next(item["public_url"] for item in tunnels if item["public_url"].startswith("https://"))
        body = json.dumps({"public_base_url": public_url}).encode()
        request = urllib.request.Request(
            "http://localhost:8000/api/admin/public-url",
            data=body,
            method="PUT",
            headers={"Content-Type": "application/json", "X-Admin-Token": ADMIN_TOKEN},
        )
        with urllib.request.urlopen(request, timeout=4) as response:
            result = json.load(response)
    except (OSError, KeyError, StopIteration) as exc:
        print(f"Unable to configure the tunnel: {exc}", file=sys.stderr)
        print("Start `ngrok http 8000` first, then run this command again.", file=sys.stderr)
        return 1

    print(f"Dashboard QR code now points to: {result['public_base_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
