import json

import pytest

from cpfc_demo.storage.database import Database


@pytest.fixture
async def database(tmp_path):
    value = Database(str(tmp_path / "demo.sqlite3"), stranded_after_seconds=0)
    await value.connect()
    await value.initialize("naive", 1861)
    try:
        yield value
    finally:
        await value.close()


@pytest.mark.asyncio
async def test_order_effects_are_idempotent(database: Database) -> None:
    order = await database.create_order(
        source="audience",
        supporter_alias="Alice",
        section="Holmesdale Road",
        price_pence=5500,
    )

    first_payment = await database.commit_payment(order, 1, "SIM-PAY-00001")
    replayed_payment = await database.commit_payment(order, 2, "SIM-PAY-should-not-win")
    first_ticket = await database.commit_effect(
        order=order,
        service="ticket",
        attempt=1,
        table="tickets",
        id_column="ticket_id",
        resource_id="DEMO-TKT-00001",
        event_type="ticket_issued",
        step="ticket",
        message="Digital demo ticket issued",
    )
    replayed_ticket = await database.commit_effect(
        order=order,
        service="ticket",
        attempt=2,
        table="tickets",
        id_column="ticket_id",
        resource_id="DEMO-TKT-should-not-win",
        event_type="ticket_issued",
        step="ticket",
        message="Digital demo ticket issued",
    )

    assert first_payment[:2] == ("SIM-PAY-00001", False)
    assert replayed_payment[:2] == ("SIM-PAY-00001", True)
    assert first_ticket == ("DEMO-TKT-00001", False)
    assert replayed_ticket == ("DEMO-TKT-00001", True)

    snapshot = await database.order_snapshot(order.order_id)
    assert snapshot is not None
    assert snapshot["payment_id"] == "SIM-PAY-00001"
    assert snapshot["ticket_id"] == "DEMO-TKT-00001"
    assert snapshot["deduplicated_count"] == 1


@pytest.mark.asyncio
async def test_crash_token_is_consumed_after_payment_commit(database: Database) -> None:
    order = await database.create_order(
        source="audience",
        supporter_alias="Bob",
        section="Main Stand",
        price_pence=5500,
    )
    await database.arm_crash(target_source="audience")

    first = await database.commit_payment(order, 1, "SIM-PAY-00001")
    replay = await database.commit_payment(order, 2, "SIM-PAY-should-not-win")

    assert first == ("SIM-PAY-00001", False, True)
    assert replay == ("SIM-PAY-00001", True, False)


@pytest.mark.asyncio
async def test_fresh_run_preserves_summary_and_marks_incomplete_as_stranded(database: Database) -> None:
    await database.create_order(
        source="synthetic",
        supporter_alias="Eagle 001",
        section="Arthur Wait",
        price_pence=5500,
    )

    new_run, workflow_ids = await database.start_fresh_run("naive", 1861)
    dashboard = await database.dashboard_snapshot()

    assert workflow_ids == []
    assert new_run["run_number"] == 2
    assert dashboard["previous_summary"] == {
        "run_number": 1,
        "engine": "naive",
        "created": 1,
        "completed": 0,
        "failed": 1,
        "charged_no_ticket": 0,
    }

    row = await database._fetchone("SELECT summary_json FROM demo_runs WHERE run_number=1")
    assert row is not None
    assert json.loads(row["summary_json"])["failed"] == 1
