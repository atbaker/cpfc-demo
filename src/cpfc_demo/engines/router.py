import asyncio
from dataclasses import asdict
from datetime import timedelta

import httpx
from temporalio.client import Client
from temporalio.common import SearchAttributeKey, SearchAttributePair, TypedSearchAttributes

from cpfc_demo.config import Settings
from cpfc_demo.domain.models import OrderRequest


class CheckoutDispatcher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=5)
        self._temporal: Client | None = None
        self._temporal_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.http.aclose()

    async def temporal_client(self) -> Client:
        if self._temporal is not None:
            return self._temporal
        async with self._temporal_lock:
            if self._temporal is None:
                self._temporal = await Client.connect(
                    self.settings.temporal_address,
                    namespace=self.settings.temporal_namespace,
                )
            return self._temporal

    async def submit(self, order: OrderRequest) -> None:
        if order.engine == "naive":
            response = await self.http.post(
                f"{self.settings.naive_worker_url}/internal/jobs",
                json=asdict(order),
                headers={"X-Internal-Token": self.settings.internal_token},
            )
            response.raise_for_status()
            return
        client = await self.temporal_client()
        search_attributes = TypedSearchAttributes(
            [
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("DemoSessionId"),
                    order.run_id,
                )
            ]
        )
        await client.start_workflow(
            "TicketOrderWorkflow",
            asdict(order),
            id=f"ticket-order:{order.run_id}:{order.order_id}",
            task_queue=self.settings.temporal_task_queue,
            execution_timeout=timedelta(minutes=5),
            search_attributes=search_attributes,
            static_summary=f"CPFC demo ticket {order.order_id}",
        )

    async def terminate_workflows(self, workflow_ids: list[str]) -> None:
        if not workflow_ids:
            return
        try:
            client = await self.temporal_client()
        except Exception:
            return
        for workflow_id in workflow_ids:
            try:
                handle = client.get_workflow_handle(workflow_id)
                await handle.terminate("Demo session reset")
            except Exception:
                continue

    async def temporal_available(self) -> bool:
        try:
            await asyncio.wait_for(self.temporal_client(), timeout=2)
            return True
        except Exception:
            return False
