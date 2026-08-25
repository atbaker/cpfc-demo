from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn(name="TicketOrderWorkflow")
class TicketOrderWorkflow:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"step": "requested", "health": "processing"}

    @workflow.run
    async def run(self, order: dict[str, Any]) -> dict[str, Any]:
        policy = RetryPolicy(
            initial_interval=timedelta(seconds=1),
            backoff_coefficient=2,
            maximum_interval=timedelta(seconds=5),
            maximum_attempts=0,
        )
        steps = [
            ("reserve_ticket", "reservation"),
            ("charge_payment", "payment"),
            ("issue_ticket", "ticket"),
            ("award_loyalty_points", "loyalty"),
            ("send_confirmation", "confirmation"),
        ]
        try:
            for activity_name, step in steps:
                self.state = {"step": step, "health": "processing"}
                await workflow.execute_activity(
                    activity_name,
                    order,
                    start_to_close_timeout=timedelta(seconds=5),
                    schedule_to_close_timeout=timedelta(seconds=90),
                    retry_policy=policy,
                )
        except ActivityError as exc:
            self.state = {"step": self.state["step"], "health": "failed"}
            try:
                await workflow.execute_activity(
                    "mark_order_failed",
                    {
                        "order_id": order["order_id"],
                        "message": f"Workflow stopped after retries: {self.state['step']}",
                    },
                    start_to_close_timeout=timedelta(seconds=5),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            except ActivityError:
                pass
            raise exc
        self.state = {"step": "complete", "health": "complete"}
        return {"order_id": order["order_id"], "status": "complete"}

    @workflow.query
    def status(self) -> dict[str, Any]:
        return self.state
