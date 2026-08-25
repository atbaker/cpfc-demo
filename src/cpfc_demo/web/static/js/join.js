const form = document.querySelector("#join-form");
const error = document.querySelector("#form-error");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.textContent = "";
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  button.textContent = "Creating your demo order…";
  try {
    const response = await fetch("/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        join_code: document.querySelector("#join-code").value,
        supporter_alias: document.querySelector("#supporter-alias").value,
        section: document.querySelector("#section").value,
      }),
    });
    if (!response.ok) {
      const payload = await response.json();
      throw new Error(payload.detail || "Could not create the order");
    }
    const payload = await response.json();
    window.location.assign(payload.url);
  } catch (caught) {
    error.textContent = caught.message;
    button.disabled = false;
    button.textContent = "Get my demo ticket";
  }
});

