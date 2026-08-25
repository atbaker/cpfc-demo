const shell = document.querySelector(".order-shell");
const orderId = shell.dataset.orderId;
let revision = null;
let failures = 0;

const stepOrder = ["reservation", "payment", "ticket", "loyalty", "confirmation"];
const milestoneIndex = { requested: -1, reserved: 0, payment: 1, ticket: 2, complete: 4, failed: -1 };

function render(order) {
  document.querySelector("#order-reference").textContent = order.id;
  const current = milestoneIndex[order.milestone] ?? -1;
  document.querySelectorAll("#progress-steps li").forEach((item, index) => {
    item.className = "";
    const small = item.querySelector("small");
    if (index <= current) {
      item.classList.add("done");
      item.querySelector("span").textContent = "✓";
      small.textContent = index === 3 && order.points ? `${order.points} demo points` : "Complete";
    } else if (index === current + 1 && !["failed", "stranded"].includes(order.health)) {
      item.classList.add("active");
      small.textContent = order.health === "retrying" ? "Retrying safely" : "In progress";
    } else {
      item.querySelector("span").textContent = String(index + 1);
      small.textContent = "Waiting";
    }
  });

  const message = document.querySelector("#order-message");
  message.className = "order-message";
  if (order.health === "stranded" || order.health === "failed") {
    message.textContent = order.charged_no_ticket
      ? "Payment recorded; ticket not issued. This demo order needs attention."
      : order.last_message || "This demo order could not complete.";
    message.classList.add("risk");
    const item = document.querySelector(`[data-step="${stepOrder[Math.max(current + 1, 0)]}"]`);
    if (item) item.classList.add("risk");
  } else if (order.health === "worker_unavailable" || order.health === "retrying") {
    message.textContent = order.engine === "temporal"
      ? "Your order is safely waiting while processing resumes."
      : "The checkout processor is temporarily unavailable.";
    message.classList.add("safe");
  } else if (order.health === "complete") {
    message.textContent = "Your fictional Cup Night ticket is ready.";
  } else {
    message.textContent = order.last_message || "Your order is moving through the checkout.";
  }

  const ticket = document.querySelector("#demo-ticket");
  if (order.health === "complete") {
    ticket.hidden = false;
    document.querySelector("#ticket-name").textContent = order.supporter_alias;
    document.querySelector("#ticket-seat").textContent = order.seat || order.section;
  }
}

async function poll() {
  try {
    const suffix = revision === null ? "" : `?revision=${revision}`;
    const response = await fetch(`/api/orders/${encodeURIComponent(orderId)}${suffix}`);
    if (response.status === 204) return;
    if (!response.ok) throw new Error("Order update unavailable");
    const order = await response.json();
    revision = order.revision;
    failures = 0;
    render(order);
  } catch (error) {
    failures += 1;
    if (failures > 3) document.querySelector("#order-message").textContent = "Reconnecting to your demo order…";
  }
}

poll();
setInterval(poll, 750);

