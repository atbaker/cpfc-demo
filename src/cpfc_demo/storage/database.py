import asyncio
import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

from cpfc_demo.domain.models import Engine, FaultUpdate, OrderRequest, OrderSource


def now_ts() -> float:
    return time.time()


class Database:
    def __init__(self, path: str, *, stranded_after_seconds: float = 4.0) -> None:
        self.path = Path(path)
        self.stranded_after_seconds = stranded_after_seconds
        self.connection: aiosqlite.Connection | None = None
        self.lock = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA foreign_keys=ON")

    @property
    def db(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not connected")
        return self.connection

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    async def initialize(self, engine: Engine, seed: int) -> dict[str, Any]:
        schema = (Path(__file__).with_name("schema.sql")).read_text()
        async with self.lock:
            await self.db.executescript(schema)
            active = await self._fetchone("SELECT * FROM demo_runs WHERE status = 'active' LIMIT 1")
            if active is None:
                active = await self._create_run(self.db, engine=engine, seed=seed)
            elif active["engine"] != engine:
                await self._close_run(self.db, active["id"])
                active = await self._create_run(self.db, engine=engine, seed=seed)
            await self.db.commit()
            return dict(active)

    async def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        cursor = await self.db.execute(sql, params)
        return await cursor.fetchone()

    async def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        cursor = await self.db.execute(sql, params)
        return list(await cursor.fetchall())

    async def _touch(self, connection: aiosqlite.Connection) -> int:
        await connection.execute("UPDATE metadata SET value = CAST(value AS INTEGER) + 1 WHERE key = 'revision'")
        row = await self._fetchone("SELECT CAST(value AS INTEGER) AS value FROM metadata WHERE key='revision'")
        assert row is not None
        return int(row["value"])

    async def _create_run(self, connection: aiosqlite.Connection, *, engine: Engine, seed: int) -> aiosqlite.Row:
        row = await self._fetchone("SELECT COALESCE(MAX(run_number), 0) + 1 AS n FROM demo_runs")
        assert row is not None
        run_number = int(row["n"])
        run_id = f"run-{uuid.uuid4().hex[:10]}"
        join_code = secrets.token_urlsafe(8)
        created_at = now_ts()
        await connection.execute(
            """
            INSERT INTO demo_runs(id, run_number, engine, status, seed, join_code, created_at)
            VALUES (?, ?, ?, 'active', ?, ?, ?)
            """,
            (run_id, run_number, engine, seed, join_code, created_at),
        )
        await connection.execute(
            """
            INSERT INTO fault_settings(
                run_id, reservation_failure_pct, payment_failure_pct,
                ticket_failure_pct, card_decline_pct, latency_ms, updated_at
            ) VALUES (?, 0, 0, 0, 0, 180, ?)
            """,
            (run_id, created_at),
        )
        await self._touch(connection)
        result = await self._fetchone("SELECT * FROM demo_runs WHERE id = ?", (run_id,))
        assert result is not None
        return result

    async def _summary(self, run_id: str) -> dict[str, int]:
        row = await self._fetchone(
            """
            SELECT
              COUNT(*) AS created,
              SUM(CASE WHEN c.order_id IS NOT NULL THEN 1 ELSE 0 END) AS completed,
              SUM(CASE WHEN o.orchestration_status IN ('failed','stranded') THEN 1 ELSE 0 END) AS failed,
              SUM(CASE WHEN p.order_id IS NOT NULL AND t.order_id IS NULL THEN 1 ELSE 0 END) AS charged_no_ticket
            FROM orders o
            LEFT JOIN confirmations c ON c.order_id = o.id
            LEFT JOIN payments p ON p.order_id = o.id
            LEFT JOIN tickets t ON t.order_id = o.id
            WHERE o.run_id = ?
            """,
            (run_id,),
        )
        assert row is not None
        return {key: int(row[key] or 0) for key in row.keys()}

    async def _close_run(self, connection: aiosqlite.Connection, run_id: str) -> None:
        timestamp = now_ts()
        await connection.execute(
            """
            UPDATE orders
            SET orchestration_status='stranded',
                last_message='Demo run closed before the order completed',
                updated_at=?
            WHERE run_id=?
              AND orchestration_status NOT IN ('complete','failed','stranded')
            """,
            (timestamp, run_id),
        )
        summary = await self._summary(run_id)
        await connection.execute(
            """
            UPDATE demo_runs
            SET status = 'closed', closed_at = ?, summary_json = ?
            WHERE id = ?
            """,
            (timestamp, json.dumps(summary), run_id),
        )

    async def active_run(self) -> dict[str, Any]:
        async with self.lock:
            row = await self._fetchone("SELECT * FROM demo_runs WHERE status='active' LIMIT 1")
            if row is None:
                raise RuntimeError("No active demo run")
            return dict(row)

    async def revision(self) -> int:
        async with self.lock:
            row = await self._fetchone("SELECT CAST(value AS INTEGER) AS value FROM metadata WHERE key='revision'")
            return int(row["value"] if row else 0)

    async def create_order(
        self,
        *,
        source: OrderSource,
        supporter_alias: str,
        section: str,
        price_pence: int,
    ) -> OrderRequest:
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                run = await self._fetchone("SELECT * FROM demo_runs WHERE status='active' LIMIT 1")
                if run is None:
                    raise RuntimeError("No active demo run")
                sequence_row = await self._fetchone(
                    "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS n FROM orders WHERE run_id=?",
                    (run["id"],),
                )
                assert sequence_row is not None
                sequence = int(sequence_row["n"])
                alias = supporter_alias.strip() or f"Supporter {sequence:03d}"
                order_id = f"CP-{run['run_number']:02d}-{sequence:04d}"
                workflow_id = f"ticket-order:{run['id']}:{order_id}" if run["engine"] == "temporal" else None
                created_at = now_ts()
                await self.db.execute(
                    """
                    INSERT INTO orders(
                      id, run_id, sequence_number, source, supporter_alias, section,
                      price_pence, engine, workflow_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        run["id"],
                        sequence,
                        source,
                        alias,
                        section,
                        price_pence,
                        run["engine"],
                        workflow_id,
                        created_at,
                        created_at,
                    ),
                )
                await self._event(
                    self.db,
                    order_id=order_id,
                    run_id=run["id"],
                    event_type="order_created",
                    step="requested",
                    message="Order accepted",
                )
                await self._touch(self.db)
                await self.db.commit()
                return OrderRequest(
                    order_id=order_id,
                    run_id=run["id"],
                    sequence_number=sequence,
                    source=source,
                    supporter_alias=alias,
                    section=section,
                    price_pence=price_pence,
                    engine=run["engine"],
                )
            except BaseException:
                await self.db.rollback()
                raise

    async def _event(
        self,
        connection: aiosqlite.Connection,
        *,
        order_id: str,
        run_id: str,
        event_type: str,
        step: str,
        message: str,
        attempt: int | None = None,
    ) -> None:
        timestamp = now_ts()
        await connection.execute(
            """
            INSERT INTO order_events(order_id, run_id, event_type, step, message, attempt, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, run_id, event_type, step, message, attempt, timestamp),
        )
        await connection.execute(
            "UPDATE orders SET updated_at=?, last_message=? WHERE id=?",
            (timestamp, message, order_id),
        )

    async def order_request(self, order_id: str) -> OrderRequest | None:
        async with self.lock:
            row = await self._fetchone("SELECT * FROM orders WHERE id=?", (order_id,))
            return self._to_request(row) if row else None

    @staticmethod
    def _to_request(row: aiosqlite.Row) -> OrderRequest:
        return OrderRequest(
            order_id=row["id"],
            run_id=row["run_id"],
            sequence_number=int(row["sequence_number"]),
            source=row["source"],
            supporter_alias=row["supporter_alias"],
            section=row["section"],
            price_pence=int(row["price_pence"]),
            engine=row["engine"],
        )

    async def faults(self, run_id: str) -> dict[str, Any]:
        async with self.lock:
            row = await self._fetchone(
                """
                SELECT f.*, r.seed
                FROM fault_settings f JOIN demo_runs r ON r.id=f.run_id
                WHERE f.run_id=?
                """,
                (run_id,),
            )
            if row is None:
                raise KeyError(run_id)
            return dict(row)

    async def update_faults(self, values: FaultUpdate) -> None:
        async with self.lock:
            run = await self._fetchone("SELECT id FROM demo_runs WHERE status='active'")
            if run is None:
                raise RuntimeError("No active run")
            await self.db.execute(
                """
                UPDATE fault_settings SET
                  reservation_failure_pct=?, payment_failure_pct=?, ticket_failure_pct=?,
                  card_decline_pct=?, latency_ms=?, updated_at=?
                WHERE run_id=?
                """,
                (
                    values.reservation_failure_pct,
                    values.payment_failure_pct,
                    values.ticket_failure_pct,
                    values.card_decline_pct,
                    values.latency_ms,
                    now_ts(),
                    run["id"],
                ),
            )
            await self._touch(self.db)
            await self.db.commit()

    async def record_transient_failure(self, order: OrderRequest, service: str, attempt: int, message: str) -> None:
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                await self.db.execute(
                    """
                    INSERT INTO service_attempts(order_id, run_id, service, attempt, outcome, created_at)
                    VALUES (?, ?, ?, ?, 'transient_failure', ?)
                    """,
                    (order.order_id, order.run_id, service, attempt, now_ts()),
                )
                await self._event(
                    self.db,
                    order_id=order.order_id,
                    run_id=order.run_id,
                    event_type="service_error",
                    step=service,
                    message=message,
                    attempt=attempt,
                )
                await self._touch(self.db)
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def record_decline(self, order: OrderRequest, attempt: int) -> None:
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                await self.db.execute(
                    """
                    INSERT INTO service_attempts(order_id, run_id, service, attempt, outcome, created_at)
                    VALUES (?, ?, 'payment', ?, 'declined', ?)
                    """,
                    (order.order_id, order.run_id, attempt, now_ts()),
                )
                await self.db.execute(
                    "UPDATE orders SET orchestration_status='failed' WHERE id=?",
                    (order.order_id,),
                )
                await self._event(
                    self.db,
                    order_id=order.order_id,
                    run_id=order.run_id,
                    event_type="payment_declined",
                    step="payment",
                    message="Simulated card declined — no charge created",
                    attempt=attempt,
                )
                await self._touch(self.db)
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def commit_effect(
        self,
        *,
        order: OrderRequest,
        service: str,
        attempt: int,
        table: str,
        id_column: str,
        resource_id: str,
        event_type: str,
        step: str,
        message: str,
        extra_columns: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        allowed = {
            ("reservations", "reservation_id"),
            ("tickets", "ticket_id"),
            ("loyalty_entries", "loyalty_id"),
            ("confirmations", "confirmation_id"),
        }
        if (table, id_column) not in allowed:
            raise ValueError("Unsupported effect table")
        key = f"{service}:{order.order_id}"
        extras = extra_columns or {}
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                existing = await self._fetchone(
                    f"SELECT {id_column} AS resource_id FROM {table} WHERE order_id=?",
                    (order.order_id,),
                )
                duplicate = existing is not None
                actual_id = existing["resource_id"] if existing else resource_id
                if not duplicate:
                    columns = ["order_id", "idempotency_key", id_column, *extras.keys(), "created_at"]
                    values = [order.order_id, key, actual_id, *extras.values(), now_ts()]
                    placeholders = ",".join("?" for _ in values)
                    await self.db.execute(
                        f"INSERT INTO {table}({','.join(columns)}) VALUES ({placeholders})",
                        tuple(values),
                    )
                await self.db.execute(
                    """
                    INSERT INTO service_attempts(order_id, run_id, service, attempt, outcome, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order.order_id,
                        order.run_id,
                        service,
                        attempt,
                        "duplicate" if duplicate else "success",
                        now_ts(),
                    ),
                )
                await self._event(
                    self.db,
                    order_id=order.order_id,
                    run_id=order.run_id,
                    event_type=f"{event_type}_duplicate" if duplicate else event_type,
                    step=step,
                    message=(f"Idempotent replay: {message}" if duplicate else message),
                    attempt=attempt,
                )
                if table == "confirmations":
                    await self.db.execute(
                        "UPDATE orders SET orchestration_status='complete' WHERE id=?",
                        (order.order_id,),
                    )
                await self._touch(self.db)
                await self.db.commit()
                return str(actual_id), duplicate
            except BaseException:
                await self.db.rollback()
                raise

    async def commit_payment(self, order: OrderRequest, attempt: int, payment_id: str) -> tuple[str, bool, bool]:
        key = f"charge:{order.order_id}"
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                existing = await self._fetchone("SELECT payment_id FROM payments WHERE order_id=?", (order.order_id,))
                duplicate = existing is not None
                actual_id = existing["payment_id"] if existing else payment_id
                if not duplicate:
                    await self.db.execute(
                        """
                        INSERT INTO payments(order_id, idempotency_key, payment_id, amount_pence, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (order.order_id, key, actual_id, order.price_pence, now_ts()),
                    )
                token = await self._fetchone(
                    """
                    SELECT id FROM crash_tokens
                    WHERE run_id=? AND consumed_at IS NULL
                      AND (target_source='any' OR target_source=?)
                      AND (target_order_id IS NULL OR target_order_id=?)
                    ORDER BY created_at LIMIT 1
                    """,
                    (order.run_id, order.source, order.order_id),
                )
                crash = token is not None
                if token is not None:
                    await self.db.execute(
                        """
                        UPDATE crash_tokens SET consumed_order_id=?, consumed_at=? WHERE id=?
                        """,
                        (order.order_id, now_ts(), token["id"]),
                    )
                await self.db.execute(
                    """
                    INSERT INTO service_attempts(order_id, run_id, service, attempt, outcome, created_at)
                    VALUES (?, ?, 'payment', ?, ?, ?)
                    """,
                    (
                        order.order_id,
                        order.run_id,
                        attempt,
                        "duplicate" if duplicate else "success",
                        now_ts(),
                    ),
                )
                await self._event(
                    self.db,
                    order_id=order.order_id,
                    run_id=order.run_id,
                    event_type="payment_deduplicated" if duplicate else "payment_charged",
                    step="payment",
                    message=(
                        "Duplicate charge prevented — original receipt returned"
                        if duplicate
                        else "Simulated payment charged"
                    ),
                    attempt=attempt,
                )
                await self._touch(self.db)
                await self.db.commit()
                return str(actual_id), duplicate, crash
            except BaseException:
                await self.db.rollback()
                raise

    async def mark_status(self, order_id: str, status: str, message: str) -> None:
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                order = await self._fetchone("SELECT * FROM orders WHERE id=?", (order_id,))
                if order is None:
                    raise KeyError(order_id)
                await self.db.execute("UPDATE orders SET orchestration_status=? WHERE id=?", (status, order_id))
                await self._event(
                    self.db,
                    order_id=order_id,
                    run_id=order["run_id"],
                    event_type=f"orchestration_{status}",
                    step="orchestration",
                    message=message,
                )
                await self._touch(self.db)
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def arm_crash(self, *, target_source: str, target_order_id: str | None = None) -> dict[str, Any]:
        async with self.lock:
            run = await self._fetchone("SELECT id FROM demo_runs WHERE status='active'")
            if run is None:
                raise RuntimeError("No active run")
            token_id = f"crash-{uuid.uuid4().hex[:10]}"
            await self.db.execute(
                """
                INSERT INTO crash_tokens(id, run_id, target_source, target_order_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token_id, run["id"], target_source, target_order_id, now_ts()),
            )
            await self._touch(self.db)
            await self.db.commit()
            return {"id": token_id, "target_source": target_source, "target_order_id": target_order_id}

    async def heartbeat(self, worker_type: str, instance_id: str, started_at: float) -> None:
        async with self.lock:
            await self.db.execute(
                """
                INSERT INTO worker_heartbeats(worker_type, instance_id, started_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(worker_type) DO UPDATE SET
                  instance_id=excluded.instance_id,
                  started_at=CASE
                    WHEN worker_heartbeats.instance_id != excluded.instance_id THEN excluded.started_at
                    ELSE worker_heartbeats.started_at
                  END,
                  last_seen_at=excluded.last_seen_at
                """,
                (worker_type, instance_id, started_at, now_ts()),
            )
            await self._touch(self.db)
            await self.db.commit()

    async def start_fresh_run(self, engine: Engine, seed: int) -> tuple[dict[str, Any], list[str]]:
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                active = await self._fetchone("SELECT * FROM demo_runs WHERE status='active' LIMIT 1")
                workflow_ids: list[str] = []
                if active is not None:
                    rows = await self._fetchall(
                        """
                        SELECT workflow_id FROM orders
                        WHERE run_id=? AND workflow_id IS NOT NULL
                          AND orchestration_status NOT IN ('complete','failed')
                        """,
                        (active["id"],),
                    )
                    workflow_ids = [row["workflow_id"] for row in rows]
                    await self._close_run(self.db, active["id"])
                run = await self._create_run(self.db, engine=engine, seed=seed)
                await self.db.commit()
                return dict(run), workflow_ids
            except BaseException:
                await self.db.rollback()
                raise

    async def dashboard_snapshot(self) -> dict[str, Any]:
        async with self.lock:
            revision_row = await self._fetchone(
                "SELECT CAST(value AS INTEGER) AS value FROM metadata WHERE key='revision'"
            )
            run = await self._fetchone("SELECT * FROM demo_runs WHERE status='active' LIMIT 1")
            if run is None:
                raise RuntimeError("No active run")
            faults = await self._fetchone("SELECT * FROM fault_settings WHERE run_id=?", (run["id"],))
            rows = await self._fetchall(
                """
                SELECT o.*,
                  r.reservation_id, r.seat,
                  p.payment_id,
                  t.ticket_id,
                  l.loyalty_id, l.points,
                  c.confirmation_id,
                  (SELECT event_type FROM order_events e
                    WHERE e.order_id=o.id ORDER BY e.id DESC LIMIT 1) AS last_event_type,
                  (SELECT step FROM order_events e
                    WHERE e.order_id=o.id ORDER BY e.id DESC LIMIT 1) AS last_step,
                  (SELECT COUNT(*) FROM order_events e
                    WHERE e.order_id=o.id AND e.event_type='payment_deduplicated') AS deduplicated_count
                FROM orders o
                LEFT JOIN reservations r ON r.order_id=o.id
                LEFT JOIN payments p ON p.order_id=o.id
                LEFT JOIN tickets t ON t.order_id=o.id
                LEFT JOIN loyalty_entries l ON l.order_id=o.id
                LEFT JOIN confirmations c ON c.order_id=o.id
                WHERE o.run_id=?
                ORDER BY o.sequence_number
                """,
                (run["id"],),
            )
            heartbeat_rows = await self._fetchall("SELECT * FROM worker_heartbeats")
            previous = await self._fetchone(
                """
                SELECT run_number, engine, summary_json FROM demo_runs
                WHERE status='closed' AND summary_json IS NOT NULL
                ORDER BY run_number DESC LIMIT 1
                """
            )
            crash = await self._fetchone(
                """
                SELECT * FROM crash_tokens WHERE run_id=? ORDER BY created_at DESC LIMIT 1
                """,
                (run["id"],),
            )

        current_time = now_ts()
        heartbeats = {
            row["worker_type"]: {
                "online": current_time - float(row["last_seen_at"]) < 3.5,
                "instance_id": row["instance_id"],
                "last_seen_at": row["last_seen_at"],
            }
            for row in heartbeat_rows
        }
        orders: list[dict[str, Any]] = []
        for row in rows:
            age = current_time - float(row["updated_at"])
            if row["orchestration_status"] in {"failed", "stranded"}:
                health = row["orchestration_status"]
                milestone = "failed"
            elif row["confirmation_id"]:
                health = "complete"
                milestone = "complete"
            else:
                if row["ticket_id"]:
                    milestone = "ticket"
                elif row["payment_id"]:
                    milestone = "payment"
                elif row["reservation_id"]:
                    milestone = "reserved"
                else:
                    milestone = "requested"
                worker = heartbeats.get(f"{row['engine']}-worker", {"online": False})
                if not worker["online"]:
                    health = "worker_unavailable"
                elif row["engine"] == "naive" and age > self.stranded_after_seconds:
                    health = "stranded"
                elif row["last_event_type"] == "service_error":
                    health = "retrying"
                else:
                    health = "processing"
            orders.append(
                {
                    "id": row["id"],
                    "sequence_number": row["sequence_number"],
                    "source": row["source"],
                    "supporter_alias": row["supporter_alias"],
                    "section": row["section"],
                    "price_pence": row["price_pence"],
                    "engine": row["engine"],
                    "workflow_id": row["workflow_id"],
                    "milestone": milestone,
                    "health": health,
                    "last_message": row["last_message"],
                    "last_step": row["last_step"],
                    "updated_at": row["updated_at"],
                    "created_at": row["created_at"],
                    "seat": row["seat"],
                    "reservation_id": row["reservation_id"],
                    "payment_id": row["payment_id"],
                    "ticket_id": row["ticket_id"],
                    "points": row["points"],
                    "charged_no_ticket": bool(row["payment_id"] and not row["ticket_id"]),
                    "deduplicated_count": int(row["deduplicated_count"] or 0),
                }
            )

        metrics = {
            "created": len(orders),
            "in_flight": sum(
                1 for order in orders if order["health"] in {"processing", "retrying", "worker_unavailable"}
            ),
            "completed": sum(order["health"] == "complete" for order in orders),
            "failed": sum(order["health"] in {"failed", "stranded"} for order in orders),
            "charged_no_ticket": sum(order["charged_no_ticket"] for order in orders),
            "duplicate_charges_prevented": sum(order["deduplicated_count"] for order in orders),
        }
        previous_summary = None
        if previous and previous["summary_json"]:
            previous_summary = {
                "run_number": previous["run_number"],
                "engine": previous["engine"],
                **json.loads(previous["summary_json"]),
            }
        return {
            "revision": int(revision_row["value"] if revision_row else 0),
            "server_time": current_time,
            "run": {
                "id": run["id"],
                "run_number": run["run_number"],
                "engine": run["engine"],
                "join_code": run["join_code"],
                "created_at": run["created_at"],
            },
            "faults": dict(faults) if faults else {},
            "orders": orders,
            "metrics": metrics,
            "workers": heartbeats,
            "crash_token": dict(crash) if crash else None,
            "previous_summary": previous_summary,
        }

    async def order_snapshot(self, order_id: str) -> dict[str, Any] | None:
        dashboard = await self.dashboard_snapshot()
        order = next((item for item in dashboard["orders"] if item["id"] == order_id), None)
        if order is None:
            return None
        async with self.lock:
            events = await self._fetchall(
                """
                SELECT event_type, step, message, attempt, created_at
                FROM order_events WHERE order_id=? ORDER BY id
                """,
                (order_id,),
            )
        return {
            **order,
            "run": dashboard["run"],
            "events": [dict(event) for event in events],
            "revision": dashboard["revision"],
        }
