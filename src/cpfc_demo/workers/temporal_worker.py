import asyncio
import contextlib
import time
import uuid

from temporalio.client import Client
from temporalio.worker import Worker

from cpfc_demo.config import get_settings
from cpfc_demo.services.client import DemoServicesClient, heartbeat_loop
from cpfc_demo.temporal.activities import TicketActivities
from cpfc_demo.temporal.workflows import TicketOrderWorkflow


async def run() -> None:
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    services = DemoServicesClient(settings.service_base_url, settings.internal_token)
    activities = TicketActivities(services)
    instance_id = f"temporal-{uuid.uuid4().hex[:8]}"
    started_at = time.time()
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[TicketOrderWorkflow],
        activities=[
            activities.reserve_ticket,
            activities.charge_payment,
            activities.issue_ticket,
            activities.award_loyalty_points,
            activities.send_confirmation,
            activities.mark_order_failed,
        ],
        max_concurrent_activities=100,
    )
    heartbeat_task = asyncio.create_task(
        heartbeat_loop(
            services,
            worker_type="temporal-worker",
            instance_id=instance_id,
            started_at=started_at,
        )
    )
    try:
        await worker.run()
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        await services.close()


if __name__ == "__main__":
    asyncio.run(run())
