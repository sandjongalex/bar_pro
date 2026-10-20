/* Progressive V2 inventory counting. No stock mutation, no page refresh. */
(function () {
  "use strict";
  var form = document.getElementById("inv2-form");
  if (!form || typeof window.fetch !== "function") return;

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
