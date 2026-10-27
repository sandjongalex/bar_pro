/* Inventory finance only: preserves all unsaved stock-count fields. */
(function () {
  "use strict";

  if (!window.fetch || !window.FormData || !window.DOMParser) return;
  var pending = false;
  var financeId = "finance-inventory";

  function notify(panel, message, type) {
    var previous = panel.querySelector(".inv-finance-async-alert");
    if (previous) previous.remove();
    var alert = document.createElement("div");
    alert.className = "alert alert-" + type + " mt-3 inv-finance-async-alert";
    alert.setAttribute("role", "status");
    alert.setAttribute("aria-live", "polite");
    alert.textContent = message;
    panel.insertBefore(alert, panel.firstChild);
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var panel = document.getElementById(financeId);
    if (!panel || !form || !panel.contains(form) ||
        !form.classList.contains("inv-finance-form") &&
        !form.classList.contains("inv-finance-decision-form")) return;

    // Only the finance forms are intercepted. Stock forms remain unchanged.
    event.preventDefault();
    if (pending || !form.checkValidity()) {
      if (pending) notify(panel, "Un enregistrement est déjà en cours.", "info");
      else form.reportValidity();
      return;
    }
    var submitter = event.submitter || document.activeElement;
    var payload = new FormData(form);
    if (submitter && submitter.name) payload.set(submitter.name, submitter.value);
    var buttons = Array.prototype.slice.call(form.querySelectorAll('button[type="submit"]'));
    buttons.forEach(function (button) { button.disabled = true; });
    pending = true;
    notify(panel, "Enregistrement en cours…", "info");

    fetch(form.action, {
      method: "POST",
      credentials: "same-origin",
      body: payload,
      headers: { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" }
    }).then(function (response) {
      var contentType = response.headers.get("content-type") || "";
      if (contentType.indexOf("application/json") === -1) {
        throw new Error("Réponse inattendue du serveur. Vérifiez la session et les écritures avant de réessayer.");
      }
      return response.json().then(function (data) {
        if (!response.ok || !data.success) throw new Error(data.message || "Enregistrement refusé.");
        return data;
      });
    }).then(function (data) {
      if (data.html) {
        var next = new DOMParser().parseFromString(data.html, "text/html")
          .getElementById(financeId);
        if (next) {
          // Retain open/closed financial accordion sections, not the stock form.
          var openStates = Array.prototype.map.call(panel.querySelectorAll("details"), function (item) {
            return item.open;
          });
          Array.prototype.forEach.call(next.querySelectorAll("details"), function (item, index) {
            if (index < openStates.length) item.open = openStates[index];
          });
          panel.replaceWith(document.importNode(next, true));
          panel = document.getElementById(financeId);
        }
      }
      if (!data.html) {
        // Success was committed even if the fragment could not be refreshed.
        buttons.forEach(function (button) { button.disabled = true; });
        notify(panel, (data.message || "Enregistré.") +
          " Actualisation financière indisponible : consultez l'historique avant toute nouvelle saisie.", "warning");
      } else {
        notify(panel, data.message || "Enregistrement effectué.", "success");
      }
    }).catch(function (error) {
      // Preserve the original nonce on failures so a retry cannot duplicate
      // an expense or debt accidentally accepted by the server.
      notify(panel, error.message || "Enregistrement impossible. Vérifiez avant de réessayer.", "danger");
      buttons.forEach(function (button) { button.disabled = false; });
    }).then(function () {
      pending = false;
    });
  });
}());
