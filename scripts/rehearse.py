#!/usr/bin/env python3
"""Exercise the current engine's happy path and crash-after-charge story."""

import argparse
import sys
import time

import httpx

ADMIN_TOKEN = "palace-admin-2026"


class Rehearsal:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.Client(base_url=self.base_url, timeout=8, headers={"X-Admin-Token": ADMIN_TOKEN})

    def dashboard(self) -> dict:
        return self.client.get("/api/admin/dashboard").raise_for_status().json()

    def fresh(self) -> dict:
        return self.client.post("/api/admin/runs/fresh").raise_for_status().json()["run"]

    def preset(self, name: str) -> None:
        self.client.post(f"/api/admin/presets/{name}").raise_for_status()

    def submit(self, alias: str) -> str:
        run = self.dashboard()["run"]
        response = self.client.post(
            "/api/orders",
            json={"join_code": run["join_code"], "supporter_alias": alias, "section": "Holmesdale Road"},
        )
        return response.raise_for_status().json()["order_id"]

    def order(self, order_id: str) -> dict:
        return self.client.get(f"/api/orders/{order_id}").raise_for_status().json()

    def wait_for(self, order_id: str, predicate, description: str) -> dict:
        deadline = time.monotonic() + self.timeout
        latest = {}
        while time.monotonic() < deadline:
            latest = self.order(order_id)
            if predicate(latest):
                return latest
            time.sleep(0.5)
        raise RuntimeError(f"Timed out waiting for {description}; last state: {latest}")

    def happy_path(self) -> None:
        self.fresh()
        self.preset("healthy")
        order_id = self.submit("Rehearsal Happy Path")
        order = self.wait_for(order_id, lambda item: item["health"] == "complete", "order completion")
        assert order["payment_id"] and order["ticket_id"] and order["points"] == 25
        print(f"PASS  {order['engine']} happy path: {order_id}")

    def crash_path(self) -> None:
        self.fresh()
        self.preset("healthy")
        engine = self.dashboard()["run"]["engine"]
        self.client.post("/api/admin/crash-token", json={"target_source": "audience"}).raise_for_status()
        order_id = self.submit("Rehearsal Crash Test")
        if engine == "temporal":
            order = self.wait_for(order_id, lambda item: item["health"] == "complete", "Temporal recovery")
            assert order["payment_id"] and order["ticket_id"]
            assert order["deduplicated_count"] >= 1
            print(f"PASS  Temporal recovered {order_id}; one payment and a ticket, duplicate charge prevented")
        else:
            order = self.wait_for(
                order_id,
                lambda item: item["charged_no_ticket"] and item["health"] == "stranded",
                "naive charged-without-ticket failure",
            )
            assert order["payment_id"] and not order["ticket_id"]
            print(f"PASS  Naive worker lost {order_id} after charge; payment exists and ticket does not")

    def load(self, count: int, rate: float, preset: str, crash_during_load: bool) -> None:
        self.fresh()
        self.preset(preset)
        engine = self.dashboard()["run"]["engine"]
        self.client.post(
            "/api/admin/generator/start",
            json={"target_count": count, "rate_per_second": rate},
        ).raise_for_status()
        headline_order_id = None
        if crash_during_load:
            time.sleep(0.5)
            self.client.post("/api/admin/crash-token", json={"target_source": "audience"}).raise_for_status()
            headline_order_id = self.submit("Load Test Volunteer")
        deadline = time.monotonic() + max(self.timeout, count / rate + 30)
        latest = self.dashboard()
        while time.monotonic() < deadline:
            latest = self.dashboard()
            generator = latest["generator"]
            if not generator["running"] and generator["submitted"] == count and latest["metrics"]["in_flight"] == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"Load run did not settle: {latest['metrics']} / {latest['generator']}")
        if headline_order_id and engine == "naive":
            headline = self.order(headline_order_id)
            assert headline["charged_no_ticket"] and headline["health"] == "stranded"
            assert latest["metrics"]["failed"] > 0
        elif headline_order_id:
            headline = self.order(headline_order_id)
            assert headline["health"] == "complete" and headline["deduplicated_count"] >= 1
        print(f"PASS  load run settled: {latest['metrics']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--scenario", choices=("normal", "crash", "all"), default="all")
    parser.add_argument("--load", type=int, default=0, metavar="COUNT")
    parser.add_argument("--rate", type=float, default=25)
    parser.add_argument(
        "--load-preset",
        choices=("healthy", "ticket-flaky", "payment-slow", "rush"),
        default="ticket-flaky",
    )
    parser.add_argument("--crash-during-load", action="store_true")
    args = parser.parse_args()

    rehearsal = Rehearsal(args.base_url, args.timeout)
    try:
        if args.scenario in {"normal", "all"}:
            rehearsal.happy_path()
        if args.scenario in {"crash", "all"}:
            rehearsal.crash_path()
        if args.load:
            rehearsal.load(args.load, args.rate, args.load_preset, args.crash_during_load)
    except (AssertionError, httpx.HTTPError, RuntimeError) as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        rehearsal.client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
