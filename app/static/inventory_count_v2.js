/* Progressive V2 inventory counting. No stock mutation, no page refresh. */
(function () {
  "use strict";
  var form = document.getElementById("inv2-form");
  if (typeof window.fetch !== "function") return;

  var previewRoot = document.querySelector(".inv2-reconcile");
  var refreshBtn = document.getElementById("inv2-reconcile-refresh");
  var previewMessages = {
    "RECONCILED": "Conforme",
    "NOT_COUNTED": "Non compté",
    "DIFFERENCE": "Écart à vérifier",
    "LEDGER_MISMATCH": "Journal incohérent",
    "REFERENCE_MISSING": "Référence absente",
    "COUNT_CHECKPOINT_MISSING": "Recomptage nécessaire",
    "INVALID_CHECKPOINT": "Point de comptage invalide",
    "NEGATIVE_PROJECTION": "Comptage à vérifier"
  };
  var previewTimer = null;

  // Format API quantity strings without rounding their fractional units.
  // "22.000000" -> "22", "-1.250000" -> "-1.25", "0.000000" -> "0".
  function qty(value) {
    if (value === null || value === undefined || value === "") return "—";
    return String(value).replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1");
  }

  function setText(node, selector, value) {
    var target = node.querySelector(selector);
    if (target) target.textContent = qty(value);
  }

  function refreshPreview() {
    if (!previewRoot) return;
    if (refreshBtn) refreshBtn.disabled = true;
    var message = document.getElementById("inv2-reconcile-message");
    window.fetch(previewRoot.getAttribute("data-preview-url"), {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (!response.ok) throw new Error("Impossible de charger les mouvements");
      return response.json();
    }).then(function (data) {
      if (!data.success || !data.data) throw new Error("Réponse de rapprochement invalide");
      data.data.lines.forEach(function (line) {
        var node = previewRoot.querySelector('[data-reconcile-product="' + line.product_id + '"]');
        if (!node) return;
        node.setAttribute("data-reconcile-state", line.status);
        setText(node, "[data-reconcile-status]", previewMessages[line.status] || line.status);
        setText(node, "[data-reconcile-machine]", qty(line.current_machine) + " " + line.base_unit);
        setText(node, "[data-reconcile-projected]", line.projected_physical_now);
        setText(node, "[data-reconcile-difference]", line.difference_now);
        setText(node, "[data-reconcile-opening]", line.opening_stock);
        setText(node, "[data-reconcile-at-count]", line.expected_at_count);
        setText(node, "[data-reconcile-after]", line.delta_after_count);
        setText(node, "[data-reconcile-breakdown]", line.movement_breakdown.map(function (movement) {
          return movement.type + " (" + qty(movement.quantity_delta) + ")";
        }).join(", ") || "Aucun");
      });
      if (message) message.textContent = data.data.requires_review
        ? data.data.requires_review + " produit(s) à contrôler avant approbation. Les données restent indicatives."
        : "Mouvements actualisés. Les quantités physiques doivent toujours être confirmées sur place.";
    }).catch(function (error) {
      if (message) message.textContent = error.message + " — Réessayez.";
    }).then(function () {
      if (refreshBtn) refreshBtn.disabled = false;
    });
  }

  function schedulePreview() {
    if (previewTimer) window.clearTimeout(previewTimer);
    previewTimer = window.setTimeout(refreshPreview, 650);
  }

  if (refreshBtn) refreshBtn.addEventListener("click", refreshPreview);

  // Read-only preview must remain active while submission locks count editing.
  if (!form) return;
  var rows = Array.prototype.slice.call(form.querySelectorAll(".inv2-product"));
  var progress = document.getElementById("inv2-progress");
  var globalState = document.getElementById("inv2-global-state");
  var saveAllButton = document.getElementById("inv2-save-all");
  var csrf = form.querySelector('input[name="csrf_token"]').value;
  var url = form.getAttribute("data-count-url");


  function status(row, kind, message) {
    row.setAttribute("data-state", kind);
    row.querySelector("[data-status]").textContent = message;
  }

  function entry(row) {
    var units = row.querySelector(".inv2-units");
    var cases = row.querySelector(".inv2-cases");
    var note = row.querySelector(".inv2-comment");
    return {
      product_id: row.getAttribute("data-product-id"),
      units: units ? units.value.trim() : "",
      cases: cases ? cases.value.trim() : "",
      note: note ? note.value : "",
      revision: row.querySelector(".inv2-revision").value
    };
  }

  function validInputs(row) {
    var inputs = row.querySelectorAll('input[type="number"]');
    for (var i = 0; i < inputs.length; i++) {
      if (!inputs[i].checkValidity()) {
        status(row, "error", "Quantité invalide");
        return false;
      }
    }
    return true;
  }

  function schedule(row) {
    row._inv2Dirty = true;
    status(row, "saving", "Non sauvegardé");
    if (row._inv2Timer) clearTimeout(row._inv2Timer);
    row._inv2Timer = window.setTimeout(function () { saveRow(row); }, 700);
  }

  function saveRow(row) {
    if (row._inv2Timer) clearTimeout(row._inv2Timer);
    row._inv2Timer = null;
    if (!row._inv2Dirty || row._inv2Blocked) return Promise.resolve();
    if (row._inv2Busy) return row._inv2Busy;
    if (!validInputs(row)) {
      globalState.textContent = "Corrigez les quantités invalides avant de sauvegarder.";
      return Promise.resolve();
    }
    row._inv2Dirty = false;
    status(row, "saving", "Enregistrement…");
    var pending = window.fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify({ entries: [entry(row)] })
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || !data.success) {
          var error = new Error(data.error && data.error.message || "Échec de sauvegarde");
          error.conflict = response.status === 409;
          throw error;
        }
        return data.data;
      });
    }).then(function (data) {
      var productId = row.getAttribute("data-product-id");
      var saved = data.lines.filter(function (item) {
        return item.product_id === productId;
      })[0];
      if (!saved) throw new Error("Réponse de comptage incomplète");
      row.querySelector(".inv2-revision").value = saved.count_revision;
      progress.textContent = data.counted + " / " + data.total;
      if (!row._inv2Dirty) {
        status(row, "saved", saved.physical_quantity === null ? "Non compté · enregistré" : "Enregistré");
      }
      globalState.textContent = "Dernier comptage sauvegardé.";
      schedulePreview();
    }).catch(function (error) {
      row._inv2Dirty = true;
      if (error.conflict) row._inv2Blocked = true;
      status(row, "error", error.conflict ? "Conflit : actualiser" : "Erreur : réessayer");
      globalState.textContent = error.message || "La sauvegarde a échoué. Réessayez.";
    }).then(function () {
      row._inv2Busy = null;
      if (row._inv2Dirty && !row._inv2Blocked && row.getAttribute("data-state") !== "error") {
        return saveRow(row);
      }
    });
    row._inv2Busy = pending;
    return pending;
  }

  rows.forEach(function (row) {
    var fields = row.querySelectorAll(".inv2-cases,.inv2-units,.inv2-comment");
    Array.prototype.forEach.call(fields, function (field) {
      field.addEventListener("input", function () { schedule(row); });
      field.addEventListener("blur", function () {
        if (row._inv2Dirty && !row._inv2Blocked) saveRow(row);
      });
    });
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (saveAllButton.disabled) return;
    saveAllButton.disabled = true;
    globalState.textContent = "Enregistrement des modifications…";
    var sequence = Promise.resolve();
    rows.forEach(function (row) {
      sequence = sequence.then(function () {
        return saveRow(row);
      });
    });
    sequence.then(function () {
      var errors = rows.some(function (row) { return row._inv2Dirty || row._inv2Blocked; });
      globalState.textContent = errors
        ? "Certaines modifications ne sont pas sauvegardées. Corrigez-les ou actualisez en cas de conflit."
        : "Toutes les modifications ont été enregistrées. L'inventaire reste un brouillon.";
    }).then(function () {
      saveAllButton.disabled = false;
    }, function () {
      saveAllButton.disabled = false;
    });
  });

  window.addEventListener("beforeunload", function (event) {
    var dirty = rows.some(function (row) { return row._inv2Dirty || row._inv2Busy; });
    if (dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
})();
