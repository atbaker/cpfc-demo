import os

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from cpfc_demo.domain.models import OrderRequest
from cpfc_demo.services.client import (
    DemoServicesClient,
    PermanentServiceError,
    TransientServiceError,
)


class TicketActivities:
    def __init__(self, client: DemoServicesClient) -> None:
        self.client = client

    @staticmethod
    def _order(value: dict) -> OrderRequest:
        return OrderRequest(**value)

    async def _invoke(self, method_name: str, value: dict) -> dict:
        order = self._order(value)
        attempt = activity.info().attempt
        method = getattr(self.client, method_name)
        try:
            result = await method(order, attempt)
        except PermanentServiceError as exc:
            raise ApplicationError(str(exc), non_retryable=True) from exc
        except TransientServiceError as exc:
            raise ApplicationError(str(exc)) from exc
        if result.crash_after_commit:
            os._exit(86)
        return result.model_dump()

    @activity.defn(name="reserve_ticket")
    async def reserve_ticket(self, order: dict) -> dict:
        return await self._invoke("reserve", order)

    @activity.defn(name="charge_payment")
    async def charge_payment(self, order: dict) -> dict:
        return await self._invoke("charge", order)

    @activity.defn(name="issue_ticket")
    async def issue_ticket(self, order: dict) -> dict:
        return await self._invoke("issue_ticket", order)

    @activity.defn(name="award_loyalty_points")
    async def award_loyalty_points(self, order: dict) -> dict:
        return await self._invoke("award_loyalty", order)

    @activity.defn(name="send_confirmation")
    async def send_confirmation(self, order: dict) -> dict:
        return await self._invoke("confirm", order)

    @activity.defn(name="mark_order_failed")
    async def mark_order_failed(self, payload: dict) -> None:
        try:
            await self.client.mark_status(payload["order_id"], "failed", payload["message"])
        except httpx.HTTPError as exc:
            raise ApplicationError(str(exc)) from exc
