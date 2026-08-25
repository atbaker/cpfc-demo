import asyncio

from fastapi import HTTPException

from cpfc_demo.domain.faults import should_fail
from cpfc_demo.domain.models import OrderRequest, ServiceResponse
from cpfc_demo.storage.database import Database


class ServiceSimulator:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def _prepare(
        self,
        order: OrderRequest,
        *,
        service: str,
        attempt: int,
        percentage_key: str | None,
    ) -> dict:
        faults = await self.database.faults(order.run_id)
        await asyncio.sleep(int(faults["latency_ms"]) / 1000)
        if percentage_key and should_fail(
            percentage=int(faults[percentage_key]),
            seed=int(faults["seed"]),
            order_sequence=order.sequence_number,
            service=service,
            attempt=attempt,
        ):
            message = f"{service.replace('_', ' ').title()} temporarily unavailable"
            await self.database.record_transient_failure(order, service, attempt, message)
            raise HTTPException(status_code=503, detail=message)
        return faults

    async def reserve(self, order: OrderRequest, attempt: int) -> ServiceResponse:
        await self._prepare(
            order,
            service="reservation",
            attempt=attempt,
            percentage_key="reservation_failure_pct",
        )
        row = chr(65 + order.sequence_number % 12)
        number = 1 + order.sequence_number % 30
        seat = f"{order.section[:3].upper()} · Row {row} · {number}"
        resource_id, duplicate = await self.database.commit_effect(
            order=order,
            service="reservation",
            attempt=attempt,
            table="reservations",
            id_column="reservation_id",
            resource_id=f"RSV-{order.order_id}",
            event_type="ticket_reserved",
            step="reservation",
            message=f"Ticket reserved: {seat}",
            extra_columns={"seat": seat},
        )
        return ServiceResponse(resource_id=resource_id, duplicate=duplicate)

    async def charge(self, order: OrderRequest, attempt: int) -> ServiceResponse:
        faults = await self._prepare(
            order,
            service="payment",
            attempt=attempt,
            percentage_key="payment_failure_pct",
        )
        if should_fail(
            percentage=int(faults["card_decline_pct"]),
            seed=int(faults["seed"]),
            order_sequence=order.sequence_number,
            service="card_decline",
            attempt=1,
        ):
            await self.database.record_decline(order, attempt)
            raise HTTPException(status_code=402, detail="Simulated card declined")
        resource_id, duplicate, crash = await self.database.commit_payment(
            order,
            attempt,
            payment_id=f"SIM-PAY-{order.order_id}",
        )
        return ServiceResponse(
            resource_id=resource_id,
            duplicate=duplicate,
            crash_after_commit=crash,
        )

    async def issue_ticket(self, order: OrderRequest, attempt: int) -> ServiceResponse:
        await self._prepare(
            order,
            service="ticket",
            attempt=attempt,
            percentage_key="ticket_failure_pct",
        )
        resource_id, duplicate = await self.database.commit_effect(
            order=order,
            service="ticket",
            attempt=attempt,
            table="tickets",
            id_column="ticket_id",
            resource_id=f"DEMO-TKT-{order.order_id}",
            event_type="ticket_issued",
            step="ticket",
            message="Digital demo ticket issued",
        )
        return ServiceResponse(resource_id=resource_id, duplicate=duplicate)

    async def award_loyalty(self, order: OrderRequest, attempt: int) -> ServiceResponse:
        await self._prepare(
            order,
            service="loyalty",
            attempt=attempt,
            percentage_key=None,
        )
        resource_id, duplicate = await self.database.commit_effect(
            order=order,
            service="points",
            attempt=attempt,
            table="loyalty_entries",
            id_column="loyalty_id",
            resource_id=f"PTS-{order.order_id}",
            event_type="loyalty_awarded",
            step="loyalty",
            message="25 demo loyalty points awarded",
            extra_columns={"points": 25},
        )
        return ServiceResponse(resource_id=resource_id, duplicate=duplicate)

    async def confirm(self, order: OrderRequest, attempt: int) -> ServiceResponse:
        await self._prepare(
            order,
            service="confirmation",
            attempt=attempt,
            percentage_key=None,
        )
        resource_id, duplicate = await self.database.commit_effect(
            order=order,
            service="confirmation",
            attempt=attempt,
            table="confirmations",
            id_column="confirmation_id",
            resource_id=f"CONF-{order.order_id}",
            event_type="confirmation_sent",
            step="confirmation",
            message="Demo confirmation sent",
        )
        return ServiceResponse(resource_id=resource_id, duplicate=duplicate)
