# Cup Night Ticket Rush

A live, local-first Temporal demo for Crystal Palace employees. It tells one story twice: a conventional in-memory worker loses an order immediately after charging a payment, while an OSS Temporal Workflow resumes after the same process crash, issues the ticket, awards loyalty points, and prevents a duplicate charge.

This is a fictional cup match and uses simulated payments only.

## Stage-ready start

Requirements: Docker Desktop, `uv`, `ngrok`, and Chrome.

```bash
docker compose up --build -d
uv run python scripts/preflight.py
```

Open:

- Presenter dashboard: <http://localhost:8000/admin?token=palace-admin-2026>
- Temporal UI: <http://localhost:8233>
- Audience page: use the live QR code in the presenter dashboard

To put audience phones through ngrok, run this in a second terminal and leave it open:

```bash
ngrok http 8000
```

Then update the QR code automatically:

```bash
uv run python scripts/connect_ngrok.py
```

The app itself remains local; ngrok only gives attendee phones a temporary HTTPS route to it.

## The one-line reveal

The current engine is intentionally selected in one readable line near the top of [`src/cpfc_demo/config.py`](src/cpfc_demo/config.py):

```python
CHECKOUT_ENGINE: Literal["naive", "temporal"] = "naive"
```

For the reveal, change `"naive"` to `"temporal"` and save. The web container reloads, archives the naïve run with its headline metrics, and creates a fresh Temporal run. Refreshing the source file in an editor is the only feature-flag mechanism shown to the room; the dashboard deliberately has no competing engine switch.

## Ten-minute live script

1. **0:00–1:00 — Set the scene.** Open the presenter dashboard at 1920×1080. Point out the fictional cup tie, five checkout steps, loyalty points, and that payments are simulated.
2. **1:00–2:00 — Invite the room.** Show the QR modal. Ask two or three people to place a demo order. Their phones follow the order live to ticket and 25 points.
3. **2:00–3:30 — Establish the naïve version.** Start roughly 40 synthetic orders at 10/s. Show the Trello-style flow and explain that the worker owns only an in-memory queue.
4. **3:30–5:00 — Make failure consequential.** Arm **Crash next audience order after payment commit**, then ask for one volunteer order. The worker really exits. Docker restarts it, but the charged order remains red with no ticket because the lost call stack and queue item existed only in memory.
5. **5:00–6:00 — Reveal Temporal.** In `src/cpfc_demo/config.py`, replace `"naive"` with `"temporal"` and save. Show the single source line, return to the dashboard, and point out the preserved naïve-run summary.
6. **6:00–7:30 — Repeat the identical failure.** Arm the same crash and place another audience order. The Temporal activity worker really exits after the same payment commit. The Workflow remains durable, the restarted worker retries, and the payment service returns its idempotent receipt instead of charging again. The order completes with a ticket and loyalty points.
7. **7:30–9:00 — Add scale and adversity.** Select **Ticketing flaky**, start 500 orders at 25/s, and show compact tile mode plus automatic retries. Open one card and then its Temporal history if useful.
8. **9:00–10:00 — Land the idea.** Temporal did not make the downstream systems infallible. It preserved intent, retries, and progress across failure so ordinary Python code reached a correct outcome.

Keep the story moving even if phones cannot join: the synthetic generator can perform every visual beat without external connectivity.

## Presenter controls

- **Healthy**, **Ticketing flaky**, **Slow services**, and **Rush mode** presets
- Individual reservation, payment, ticket, decline, and latency controls
- One-shot real process crash after a committed audience payment
- Synthetic traffic start/pause, configurable up to 1,000 orders at 30/s
- Fresh run, which clears the current board without deleting prior Temporal histories and shows the previous run summary
- Public URL/QR configuration
- Filters and a detail drawer for individual orders

At more than 50 orders the board switches from detailed cards to compact status tiles so 500-order runs remain legible on a 1920×1080 display.

## Rehearsal and recovery

Exercise the happy path and crash path for whichever engine is selected:

```bash
uv run python scripts/rehearse.py
```

Optionally include a scale run:

```bash
uv run python scripts/rehearse.py --scenario normal --load 500 --rate 25
```

To rehearse the full high-volume failure beat, including the volunteer order that triggers a real process exit:

```bash
uv run python scripts/rehearse.py --scenario normal --load 500 --rate 25 --crash-during-load
```

Useful recovery commands:

```bash
docker compose ps
docker compose logs --tail=100 web naive-worker temporal-worker temporal
docker compose restart web naive-worker temporal-worker
uv run python scripts/preflight.py --skip-ngrok
```

The stage-safe reset is the dashboard's **Fresh run** button. It archives the board, resets fault controls, creates a new join code, and asks Temporal to terminate any still-running Workflows from that run. It does not delete Temporal history.

For a complete destructive reset during development only, stop the stack and explicitly remove its named data volumes:

```bash
docker compose down -v
docker compose up --build -d
```

`down -v` permanently removes the demo's application database and local Temporal history, so do not run it during the presentation.

## Local development

```bash
uv sync --frozen
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest
```

Architecture and detailed acceptance criteria live in [`planning/cup-night-ticket-rush-plan.md`](planning/cup-night-ticket-rush-plan.md).

## Safety and attribution

- No real payment processor, supporter account, email, seat inventory, or loyalty system is contacted.
- The presenter and internal service APIs use demo-only tokens. Do not expose this application as a permanent public service.
- Crystal Palace and Temporal marks are used for this internal partner presentation. Source URLs are recorded in [`src/cpfc_demo/web/static/assets/ATTRIBUTION.md`](src/cpfc_demo/web/static/assets/ATTRIBUTION.md).
