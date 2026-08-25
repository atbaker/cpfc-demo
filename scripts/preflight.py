#!/usr/bin/env python3
"""Check the local demo stack and print the links needed on stage."""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request

ADMIN_TOKEN = "palace-admin-2026"


def request_json(url: str, *, admin: bool = False) -> dict:
    headers = {"X-Admin-Token": ADMIN_TOKEN} if admin else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=4) as response:
        return json.load(response)


def check(label: str, operation) -> tuple[bool, object]:
    try:
        result = operation()
    except (OSError, RuntimeError, subprocess.SubprocessError, urllib.error.URLError, ValueError) as exc:
        print(f"FAIL  {label}: {exc}")
        return False, exc
    print(f"PASS  {label}")
    return True, result


def require_online_workers(dashboard: dict) -> None:
    required = ("naive-worker", "temporal-worker")
    missing = [name for name in required if not dashboard.get("workers", {}).get(name, {}).get("online")]
    if missing:
        raise RuntimeError(f"stale heartbeat: {', '.join(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--skip-ngrok", action="store_true")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    results = []
    results.append(check("web application", lambda: request_json(f"{base}/health")))
    results.append(
        check(
            "naive worker",
            lambda: subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "naive-worker",
                    "python",
                    "-c",
                    "import urllib.request; urllib.request.urlopen('http://localhost:8001/health', timeout=2)",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ),
        )
    )
    results.append(
        check(
            "Temporal server",
            lambda: subprocess.run(
                ["docker", "compose", "exec", "-T", "temporal", "temporal", "operator", "cluster", "health"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ),
        )
    )
    if not args.skip_ngrok:
        results.append(
            check(
                "ngrok configuration",
                lambda: subprocess.run(
                    ["ngrok", "config", "check"], check=True, capture_output=True, text=True, timeout=10
                ),
            )
        )

    dashboard_ok, dashboard = check(
        "admin dashboard API",
        lambda: request_json(f"{base}/api/admin/dashboard", admin=True),
    )
    results.append((dashboard_ok, dashboard))
    if dashboard_ok and isinstance(dashboard, dict):
        results.append(
            check(
                "application workers",
                lambda: require_online_workers(dashboard),
            )
        )
        print()
        print(f"Engine:      {dashboard['run']['engine']}")
        print(f"Admin:       {base}/admin?token={ADMIN_TOKEN}")
        print(f"Audience:    {dashboard['join_url']}")
        print("Temporal UI: http://localhost:8233")

    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
