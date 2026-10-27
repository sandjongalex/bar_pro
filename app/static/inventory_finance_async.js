/* Inventory finance submissions update only the financial panel, never the count form. */
(function () {
  "use strict";
  if (!window.fetch || !window.FormData || !window.DOMParser) return;
  var rootId = "finance-inventory";
  var inFlight = false;

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var panel = document.getElementById(rootId);
    if (!panel || !form || !panel.contains(form) || !form.matches("form.inv-finance-form, form.inv-finance-decision-form")) return;
    event.preventDefault();
    if (inFlight || !form.checkValidity()) return;

    var submitter = event.submitter || document.activeElement;
    var data = new FormData(form);
    if (submitter && submitter.name && submitter.value) data.set(submitter.name, submitter.value);
    var controls = form.querySelectorAll('button[type="submit"]');
    var message = document.createElement("div");
    message.setAttribute("role", "status");
    message.setAttribute("aria-live", "polite");
    message.className = "alert alert-info mt-2 inv-finance-feedback";
    message.textContent = "Enregistrement en cours…";
    var prior = panel.querySelector(".inv-finance-feedback");
    if (prior) prior.remove();
    form.appendChild(message);
    inFlight = true;
    controls.forEach(function (button) { button.disabled = true; });

    fetch(form.action, {
      method: "POST",
      body: data,
      credentials: "same-origin",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok || !body.success) throw new Error(body.message || "Enregistrement impossible");
        return body;
      });
    }).then(function (body) {
      message.textContent = body.message || "Enregistré.";
      message.className = "alert alert-success mt-2 inv-finance-feedback";
      // Read the current view without navigating: counting inputs and their autosave
      // continue uninterrupted. The response also supplies fresh anti-duplicate nonces.
      return fetch(window.location.href, {
        credentials: "same-origin", cache: "no-store",
        headers: { "Accept": "text/html" }
      }).then(function (response) {
        if (!response.ok) throw new Error("Actualisation financière indisponible");
        return response.text();
      }).then(function (html) {
        var page = new DOMParser().parseFromString(html, "text/html");
        var updated = page.getElementById(rootId);
        var current = document.getElementById(rootId);
        if (!updated || !current) throw new Error("Actualisation financière indisponible");
        var opened = Array.prototype.map.call(current.querySelectorAll("details"), function (detail) {
          return detail.open;
        });
        Array.prototype.forEach.call(updated.querySelectorAll("details"), function (detail, index) {
          if (index < opened.length) detail.open = opened[index];
        });
        current.replaceWith(document.importNode(updated, true));
        var feedback = document.createElement("div");
        feedback.className = "alert alert-success mt-2 inv-finance-feedback";
        feedback.setAttribute("role", "status");
        feedback.textContent = body.message || "Enregistré.";
        document.getElementById(rootId).insertBefore(feedback, document.getElementById(rootId).firstChild);
      }).catch(function () {
        // The write succeeded: never present this as a failed submission.
        message.textContent = (body.message || "Enregistré.") + " Les montants affichés seront actualisés à la prochaine ouverture de cette page.";
        controls.forEach(function (button) { button.disabled = true; });
      });
    }).catch(function (error) {
      message.className = "alert alert-danger mt-2 inv-finance-feedback";
      message.textContent = error.message || "Erreur réseau. Vérifiez l'opération avant de réessayer.";
      // On a network failure the server may have committed already; retain
      // the nonce so the backend can reject a duplicate safely.
      controls.forEach(function (button) { button.disabled = false; });
    }).then(function () { inFlight = false; });
  });
}());
