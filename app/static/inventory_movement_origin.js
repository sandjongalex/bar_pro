/* Explain frozen inventory non-sale totals. Read-only; no form submit. */
(function () {
  "use strict";
  var modal = document.getElementById("inventoryOriginModal");
  if (!modal) return;
  var title = document.getElementById("inventoryOriginTitle");
  var summary = document.getElementById("inventoryOriginSummary");
  var status = document.getElementById("inventoryOriginStatus");
  var list = document.getElementById("inventoryOriginList");
  var requestId = 0;

  function quantity(value) {
    if (value === null || value === undefined) return "—";
    return String(value).replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1");
  }
  function addLine(parent, className, value) {
    var node = document.createElement("span");
    node.className = className;
    node.textContent = value;
    parent.appendChild(node);
    return node;
  }
  function render(data) {
    summary.textContent = "";
    list.textContent = "";
    var net = quantity(data.snapshot_total);
    addLine(summary, "inventory-origin-total", "Total dans l'inventaire : " + (Number(data.snapshot_total) > 0 ? "+" : "") + net + " bouteille(s)");
    if (!data.matches_snapshot) {
      status.className = "alert alert-warning mt-3";
      status.textContent = "Attention : le journal actuel (" + quantity(data.journal_total) + ") ne correspond pas au total figé de l'inventaire (" + net + "). Vérifier les écritures.";
    } else {
      status.className = "small text-secondary mt-3";
      status.textContent = data.movements.length ? "Opérations qui composent ce total :" : "Aucune opération hors ventes sur cette période.";
    }
    data.movements.forEach(function (move) {
      var card = document.createElement("article");
      card.className = "inventory-origin-entry";
      var heading = document.createElement("div");
      heading.className = "inventory-origin-entry-head";
      addLine(heading, "inventory-origin-type", move.type);
      addLine(heading, "inventory-origin-delta", (Number(move.quantity) > 0 ? "+" : "") + quantity(move.quantity) + " bouteille(s)");
      card.appendChild(heading);
      addLine(card, "inventory-origin-date", move.date + " · " + move.author + " · Mouvement #" + move.id);
      addLine(card, "inventory-origin-reason", "Motif : " + move.reason);
      list.appendChild(card);
    });
    if (data.has_more) {
      addLine(list, "inventory-origin-more", "Plus de 100 opérations : consultez l'historique de stock pour les suivantes.");
    }
  }

  document.addEventListener("click", function (event) {
    var trigger = event.target.closest ? event.target.closest(".inventory-origin-trigger") : null;
    if (!trigger) return;
    event.preventDefault();
    title.textContent = "Origine des mouvements · " + trigger.getAttribute("data-origin-product");
    summary.textContent = "";
    list.textContent = "";
    status.className = "small text-secondary mt-3";
    status.textContent = "Chargement de l'historique…";
    if (window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(modal).show();
    }
    if (!window.fetch) {
      status.textContent = "Ce navigateur ne permet pas d'afficher l'historique sans rechargement.";
      return;
    }
    var currentRequest = ++requestId;
    window.fetch(trigger.getAttribute("data-origin-url"), {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (!response.ok) throw new Error("Historique indisponible (" + response.status + ").");
      return response.json();
    }).then(function (result) {
      if (currentRequest !== requestId) return;
      if (!result.success || !result.data) throw new Error("Réponse d'historique invalide.");
      render(result.data);
    }).catch(function (error) {
      if (currentRequest !== requestId) return;
      status.className = "alert alert-warning mt-3";
      status.textContent = error.message + " Réessayez.";
    });
  });
}());
