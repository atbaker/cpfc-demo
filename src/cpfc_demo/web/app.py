import asyncio
import contextlib
import io
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import qrcode
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from cpfc_demo.config import Settings, get_settings
from cpfc_demo.domain.models import (
    CrashArm,
    CreateOrderPayload,
    FaultUpdate,
    GeneratorStart,
    OrderRequest,
    ServiceRequest,
    StatusUpdate,
)
from cpfc_demo.engines.router import CheckoutDispatcher
from cpfc_demo.services.simulator import ServiceSimulator
from cpfc_demo.storage.database import Database

settings = get_settings()
WEB_ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_ROOT / "templates")


class HeartbeatPayload(BaseModel):
    worker_type: str
    instance_id: str
    started_at: float


class PublicUrlPayload(BaseModel):
    public_base_url: str


class SyntheticGenerator:
    def __init__(self, context: "AppContext") -> None:
        self.context = context
        self.task: asyncio.Task | None = None
        self.running = False
        self.rate = 25.0
        self.target = 500
        self.submitted = 0
        self.started_at: float | None = None

    async def start(self, rate: float, target: int) -> None:
        await self.pause()
        self.rate = rate
        self.target = target
        self.submitted = 0
        self.started_at = time.time()
        self.running = True
        self.task = asyncio.create_task(self._run())

    async def pause(self) -> None:
        self.running = False
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def reset(self) -> None:
        await self.pause()
        self.submitted = 0
        self.started_at = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        interval = 1 / self.rate
        started = loop.time()
        pending: set[asyncio.Task] = set()

        async def submit(sequence: int) -> None:
            try:
                await self.context.create_and_dispatch(
                    source="synthetic",
                    supporter_alias=f"Eagle {sequence:03d}",
                    section=(
                        "Holmesdale Road" if sequence % 3 == 0 else "Arthur Wait" if sequence % 3 == 1 else "Main Stand"
                    ),
                )
            except Exception:
                pass

        try:
            while self.running and self.submitted < self.target:
                target_time = started + (self.submitted * interval)
                delay = target_time - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                self.submitted += 1
                task = asyncio.create_task(submit(self.submitted))
                pending.add(task)
                task.add_done_callback(pending.discard)
            if pending:
                await asyncio.gather(*pending)
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self.running = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "rate_per_second": self.rate,
            "target_count": self.target,
            "submitted": self.submitted,
            "started_at": self.started_at,
        }


class AppContext:
    def __init__(self, settings_value: Settings) -> None:
        self.settings = settings_value
        self.database = Database(
            settings_value.data_path,
            stranded_after_seconds=settings_value.stranded_after_seconds,
        )
        self.dispatcher = CheckoutDispatcher(settings_value)
        self.simulator = ServiceSimulator(self.database)
        self.public_base_url = settings_value.public_base_url.rstrip("/")
        self.generator = SyntheticGenerator(self)

    async def start(self) -> None:
        await self.database.connect()
        await self.database.initialize(
            self.settings.checkout_engine,
            self.settings.default_seed,
        )

    async def close(self) -> None:
        await self.generator.pause()
        await self.dispatcher.close()
        await self.database.close()

    async def create_and_dispatch(
        self,
        *,
        source: str,
        supporter_alias: str,
        section: str,
    ) -> OrderRequest:
        order = await self.database.create_order(
            source=source,
            supporter_alias=supporter_alias,
            section=section,
            price_pence=5500,
        )
        try:
            await self.dispatcher.submit(order)
        except Exception as exc:
            await self.database.mark_status(
                order.order_id,
                "failed",
                f"Order could not be handed to {order.engine} engine: {type(exc).__name__}",
            )
        return order


@contextlib.asynccontextmanager
async def lifespan(app_value: FastAPI) -> AsyncIterator[None]:
    context = AppContext(get_settings())
    await context.start()
    app_value.state.context = context
    yield
    await context.close()


app = FastAPI(title="CPFC Cup Night Ticket Operations", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")


def context_from(request: Request) -> AppContext:
    return request.app.state.context


def require_internal(
    request: Request,
    x_internal_token: str = Header(default=""),
) -> AppContext:
    context = context_from(request)
    if x_internal_token != context.settings.internal_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return context


def require_admin(request: Request, x_admin_token: str = Header(default="")) -> AppContext:
    context = context_from(request)
    token = x_admin_token or request.cookies.get("cpfc_admin_token", "")
    if token != context.settings.admin_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return context


@app.get("/health")
async def health(request: Request) -> dict:
    context = context_from(request)
    run = await context.database.active_run()
    return {
        "status": "ok",
        "engine": context.settings.checkout_engine,
        "run_id": run["id"],
    }


@app.get("/", response_class=HTMLResponse)
async def root(request: Request) -> RedirectResponse:
    run = await context_from(request).database.active_run()
    return RedirectResponse(f"/join/{run['join_code']}")


@app.get("/join/{join_code}", response_class=HTMLResponse)
async def join(request: Request, join_code: str) -> HTMLResponse:
    context = context_from(request)
    run = await context.database.active_run()
    if join_code != run["join_code"]:
        return templates.TemplateResponse(
            request=request,
            name="expired.html",
            context={"message": "This demo run has ended. Please scan the latest QR code."},
            status_code=410,
        )
    return templates.TemplateResponse(
        request=request,
        name="join.html",
        context={"join_code": join_code, "run": run},
    )


@app.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_page(request: Request, order_id: str) -> HTMLResponse:
    order = await context_from(request).database.order_snapshot(order_id)
    if order is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="order.html",
        context={"order_id": order_id},
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request, token: str = "") -> Response:
    context = context_from(request)
    if token:
        if token != context.settings.admin_token:
            raise HTTPException(status_code=401, detail="Invalid presenter token")
        response = RedirectResponse("/admin", status_code=303)
        response.set_cookie(
            "cpfc_admin_token",
            token,
            httponly=True,
            samesite="strict",
        )
        return response
    if request.cookies.get("cpfc_admin_token") != context.settings.admin_token:
        return HTMLResponse(
            "<h1>Presenter access required</h1><p>Open the admin URL with your presenter token.</p>",
            status_code=401,
        )
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"temporal_ui_url": context.settings.temporal_ui_url},
    )


@app.post("/api/orders", status_code=201)
async def create_order(request: Request, payload: CreateOrderPayload) -> dict:
    context = context_from(request)
    run = await context.database.active_run()
    if payload.join_code != run["join_code"]:
        raise HTTPException(status_code=410, detail="This demo run has ended")
    order = await context.create_and_dispatch(
        source="audience",
        supporter_alias=payload.supporter_alias,
        section=payload.section,
    )
    return {"order_id": order.order_id, "url": f"/orders/{order.order_id}"}


@app.get("/api/orders/{order_id}")
async def get_order(request: Request, order_id: str, revision: int | None = None) -> Response:
    context = context_from(request)
    current_revision = await context.database.revision()
    if revision is not None and revision == current_revision:
        return Response(status_code=204)
    order = await context.database.order_snapshot(order_id)
    if order is None:
        raise HTTPException(status_code=404)
    order["temporal_ui_url"] = (
        f"{context.settings.temporal_ui_url}/namespaces/{context.settings.temporal_namespace}"
        f"/workflows/{order['workflow_id']}"
        if order.get("workflow_id")
        else None
    )
    return JSONResponse(order)


@app.post("/internal/services/reservations", dependencies=[Depends(require_internal)])
async def reserve(request: Request, payload: ServiceRequest) -> dict:
    return (await context_from(request).simulator.reserve(payload.order, payload.attempt)).model_dump()


@app.post("/internal/services/payments", dependencies=[Depends(require_internal)])
async def charge(request: Request, payload: ServiceRequest) -> dict:
    return (await context_from(request).simulator.charge(payload.order, payload.attempt)).model_dump()


@app.post("/internal/services/tickets", dependencies=[Depends(require_internal)])
async def ticket(request: Request, payload: ServiceRequest) -> dict:
    return (await context_from(request).simulator.issue_ticket(payload.order, payload.attempt)).model_dump()


@app.post("/internal/services/loyalty", dependencies=[Depends(require_internal)])
async def loyalty(request: Request, payload: ServiceRequest) -> dict:
    return (await context_from(request).simulator.award_loyalty(payload.order, payload.attempt)).model_dump()


@app.post("/internal/services/confirmations", dependencies=[Depends(require_internal)])
async def confirmation(request: Request, payload: ServiceRequest) -> dict:
    return (await context_from(request).simulator.confirm(payload.order, payload.attempt)).model_dump()


@app.post("/internal/orders/{order_id}/status", dependencies=[Depends(require_internal)])
async def update_status(request: Request, order_id: str, payload: StatusUpdate) -> dict:
    await context_from(request).database.mark_status(order_id, payload.status, payload.message)
    return {"ok": True}


@app.post("/internal/workers/heartbeat", dependencies=[Depends(require_internal)])
async def worker_heartbeat(request: Request, payload: HeartbeatPayload) -> dict:
    await context_from(request).database.heartbeat(
        payload.worker_type,
        payload.instance_id,
        payload.started_at,
    )
    return {"ok": True}


@app.get("/api/admin/dashboard", dependencies=[Depends(require_admin)])
async def dashboard(request: Request, revision: int | None = None) -> Response:
    context = context_from(request)
    current_revision = await context.database.revision()
    if revision is not None and revision == current_revision:
        return Response(status_code=204)
    snapshot = await context.database.dashboard_snapshot()
    snapshot["generator"] = context.generator.snapshot()
    snapshot["public_base_url"] = context.public_base_url
    snapshot["join_url"] = f"{context.public_base_url}/join/{snapshot['run']['join_code']}"
    snapshot["temporal_ui_url"] = context.settings.temporal_ui_url
    return JSONResponse(snapshot)


@app.put("/api/admin/faults", dependencies=[Depends(require_admin)])
async def update_faults(request: Request, payload: FaultUpdate) -> dict:
    await context_from(request).database.update_faults(payload)
    return {"ok": True}


@app.post("/api/admin/presets/{preset}", dependencies=[Depends(require_admin)])
async def preset(request: Request, preset: str) -> dict:
    values = {
        "healthy": FaultUpdate(latency_ms=180),
        "ticket-flaky": FaultUpdate(ticket_failure_pct=35, latency_ms=220),
        "payment-slow": FaultUpdate(latency_ms=1000),
        "rush": FaultUpdate(latency_ms=120),
    }
    if preset not in values:
        raise HTTPException(status_code=404, detail="Unknown preset")
    await context_from(request).database.update_faults(values[preset])
    return {"ok": True, "preset": preset}


@app.post("/api/admin/crash-token", dependencies=[Depends(require_admin)])
async def arm_crash(request: Request, payload: CrashArm) -> dict:
    return await context_from(request).database.arm_crash(
        target_source=payload.target_source,
        target_order_id=payload.target_order_id,
    )


@app.post("/api/admin/generator/start", dependencies=[Depends(require_admin)])
async def start_generator(request: Request, payload: GeneratorStart) -> dict:
    generator = context_from(request).generator
    await generator.start(payload.rate_per_second, payload.target_count)
    return generator.snapshot()


@app.post("/api/admin/generator/pause", dependencies=[Depends(require_admin)])
async def pause_generator(request: Request) -> dict:
    generator = context_from(request).generator
    await generator.pause()
    return generator.snapshot()


@app.post("/api/admin/runs/fresh", dependencies=[Depends(require_admin)])
async def fresh_run(request: Request) -> dict:
    context = context_from(request)
    await context.generator.reset()
    run, workflow_ids = await context.database.start_fresh_run(
        context.settings.checkout_engine,
        context.settings.default_seed,
    )
    asyncio.create_task(context.dispatcher.terminate_workflows(workflow_ids))
    return {"run": run, "terminated_workflow_count": len(workflow_ids)}


@app.put("/api/admin/public-url", dependencies=[Depends(require_admin)])
async def public_url(request: Request, payload: PublicUrlPayload) -> dict:
    context = context_from(request)
    value = payload.public_base_url.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="URL must start with http:// or https://")
    context.public_base_url = value
    return {"public_base_url": value}


@app.get("/api/admin/qr", dependencies=[Depends(require_admin)])
async def qr_code(request: Request) -> StreamingResponse:
    context = context_from(request)
    run = await context.database.active_run()
    url = f"{context.public_base_url}/join/{run['join_code']}"
    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")
