import asyncio
import contextlib
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, status

from cpfc_demo.config import get_settings
from cpfc_demo.domain.models import OrderRequest, ServiceResponse
from cpfc_demo.services.client import (
    DemoServicesClient,
    PermanentServiceError,
    TransientServiceError,
    heartbeat_loop,
)

settings = get_settings()


def require_internal(x_internal_token: str = Header(default="")) -> None:
    if x_internal_token != settings.internal_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


class NaiveRuntime:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[OrderRequest] = asyncio.Queue(maxsize=2000)
        self.client = DemoServicesClient(settings.service_base_url, settings.internal_token)
        self.instance_id = f"naive-{uuid.uuid4().hex[:8]}"
        self.started_at = time.time()
        self.tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self.tasks = [asyncio.create_task(self.consume()) for _ in range(32)]
        self.tasks.append(
            asyncio.create_task(
                heartbeat_loop(
                    self.client,
                    worker_type="naive-worker",
                    instance_id=self.instance_id,
                    started_at=self.started_at,
                )
            )
        )

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.client.close()

    async def consume(self) -> None:
        while True:
            order = await self.queue.get()
            try:
                await self.process(order)
            finally:
                self.queue.task_done()

    async def _step(
        self,
        method: Callable[[OrderRequest, int], Awaitable[ServiceResponse]],
        order: OrderRequest,
    ) -> ServiceResponse:
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                result = await method(order, attempt)
                if result.crash_after_commit:
                    os._exit(86)
                return result
            except PermanentServiceError:
                raise
            except TransientServiceError as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.25)
        assert last_error is not None
        raise last_error

    async def process(self, order: OrderRequest) -> None:
        charged = False
        try:
            await self._step(self.client.reserve, order)
            await self._step(self.client.charge, order)
            charged = True
            await self._step(self.client.issue_ticket, order)
            await self._step(self.client.award_loyalty, order)
            await self._step(self.client.confirm, order)
        except PermanentServiceError as exc:
            await self.client.mark_status(order.order_id, "failed", str(exc))
        except TransientServiceError as exc:
            await self.client.mark_status(
                order.order_id,
                "stranded" if charged else "failed",
                f"In-process retries exhausted: {exc}",
            )


runtime = NaiveRuntime()


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await runtime.start()
    yield
    await runtime.close()


app = FastAPI(title="CPFC naïve order worker", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "worker": runtime.instance_id, "queued": runtime.queue.qsize()}


@app.post("/internal/jobs", status_code=202, dependencies=[Depends(require_internal)])
async def submit(order: OrderRequest) -> dict:
    try:
        runtime.queue.put_nowait(order)
    except asyncio.QueueFull as exc:
        raise HTTPException(status_code=503, detail="Naïve worker queue full") from exc
    return {"accepted": True, "order_id": order.order_id}
