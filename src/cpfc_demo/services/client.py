import asyncio
from dataclasses import asdict

import httpx

from cpfc_demo.domain.models import OrderRequest, ServiceResponse


class ServiceCallError(RuntimeError):
    pass


class TransientServiceError(ServiceCallError):
    pass


class PermanentServiceError(ServiceCallError):
    pass


class DemoServicesClient:
    def __init__(self, base_url: str, internal_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-Internal-Token": internal_token},
            timeout=8,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _call(self, path: str, order: OrderRequest, attempt: int) -> ServiceResponse:
        try:
            response = await self.client.post(
                path,
                json={"order": asdict(order), "attempt": attempt},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientServiceError(str(exc)) from exc
        if response.status_code == 402:
            raise PermanentServiceError(response.json().get("detail", "Simulated card declined"))
        if response.status_code >= 500:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise TransientServiceError(str(detail))
        if response.status_code >= 400:
            raise PermanentServiceError(response.text)
        return ServiceResponse.model_validate(response.json())

    async def reserve(self, order: OrderRequest, attempt: int) -> ServiceResponse:
        return await self._call("/internal/services/reservations", order, attempt)

    async def charge(self, order: OrderRequest, attempt: int) -> ServiceResponse:
        return await self._call("/internal/services/payments", order, attempt)

    async def issue_ticket(self, order: OrderRequest, attempt: int) -> ServiceResponse:
        return await self._call("/internal/services/tickets", order, attempt)

    async def award_loyalty(self, order: OrderRequest, attempt: int) -> ServiceResponse:
        return await self._call("/internal/services/loyalty", order, attempt)

    async def confirm(self, order: OrderRequest, attempt: int) -> ServiceResponse:
        return await self._call("/internal/services/confirmations", order, attempt)

    async def mark_status(self, order_id: str, status: str, message: str) -> None:
        response = await self.client.post(
            f"/internal/orders/{order_id}/status",
            json={"status": status, "message": message},
        )
        response.raise_for_status()

    async def heartbeat(self, worker_type: str, instance_id: str, started_at: float) -> None:
        response = await self.client.post(
            "/internal/workers/heartbeat",
            json={
                "worker_type": worker_type,
                "instance_id": instance_id,
                "started_at": started_at,
            },
        )
        response.raise_for_status()


async def heartbeat_loop(
    client: DemoServicesClient,
    *,
    worker_type: str,
    instance_id: str,
    started_at: float,
) -> None:
    while True:
        try:
            await client.heartbeat(worker_type, instance_id, started_at)
        except Exception:
            pass
        await asyncio.sleep(1)
