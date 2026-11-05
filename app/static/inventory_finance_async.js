/* Inventory finance AJAX: update only the finance panel, never the stock count page. */
(function () {
  "use strict";

  var pending = false;
  var financeId = "finance-inventory";

  function hasClass(element, name) {
    return !!element && (" " + element.className + " ").indexOf(" " + name + " ") !== -1;
  }

  function notify(panel, message, type) {
    if (!panel || !panel.parentNode) return;
    var previous = panel.querySelector(".inv-finance-async-alert");
    if (previous && previous.parentNode) previous.parentNode.removeChild(previous);
    var alert = document.createElement("div");
    alert.className = "alert alert-" + type + " mt-3 inv-finance-async-alert";
    alert.setAttribute("role", type === "danger" ? "alert" : "status");
    alert.setAttribute("aria-live", type === "danger" ? "assertive" : "polite");
    alert.textContent = message;
    panel.insertBefore(alert, panel.firstChild);
  }

  function parsePanel(html) {
    if (window.DOMParser) {
      var parsed = new DOMParser().parseFromString(html, "text/html");
      return parsed.getElementById(financeId);
    }
    var wrapper = document.createElement("div");
    wrapper.innerHTML = html;
    return wrapper.querySelector("#" + financeId);
  }

  function replacePanel(panel, html) {
    if (!html || !panel || !panel.parentNode) return null;
    var next = parsePanel(html);
    if (!next) return null;
    var openStates = Array.prototype.map.call(
      panel.querySelectorAll("details"),
      function (item) { return item.open; }
    );
    Array.prototype.forEach.call(
      next.querySelectorAll("details"),
      function (item, index) {
        if (index < openStates.length) item.open = openStates[index];
      }
    );
    var replacement = document.importNode
      ? document.importNode(next, true)
      : next.cloneNode(true);
    panel.parentNode.replaceChild(replacement, panel);
    return replacement;
  }

  function hasNamedDataField(form, name) {
    var fields = form.querySelectorAll("input[name],select[name],textarea[name]");
    for (var i = 0; i < fields.length; i++) {
      if (fields[i].name === name && !fields[i].disabled) return true;
    }
    return false;
  }

  function encodedForm(form, submitter) {
    var pairs = [];
    function add(name, value) {
      pairs.push(encodeURIComponent(name) + "=" + encodeURIComponent(value));
    }
    var elements = form.elements || [];
    for (var i = 0; i < elements.length; i++) {
      var field = elements[i];
      if (!field.name || field.disabled) continue;
      var type = (field.type || "").toLowerCase();
      if (type === "submit" || type === "button" || type === "reset" ||
          type === "file" || ((type === "checkbox" || type === "radio") && !field.checked)) {
        continue;
      }
      if (field.tagName && field.tagName.toLowerCase() === "select" && field.multiple) {
        for (var j = 0; j < field.options.length; j++) {
          if (field.options[j].selected) add(field.name, field.options[j].value);
        }
      } else {
        add(field.name, field.value);
      }
    }
    if (submitter && submitter.name && !hasNamedDataField(form, submitter.name)) {
      add(submitter.name, submitter.value);
    }
    return pairs.join("&");
  }

  function makePayload(form, submitter) {
    if (window.FormData) {
      var data = new FormData(form);
      if (submitter && submitter.name && !hasNamedDataField(form, submitter.name)) {
        data.append(submitter.name, submitter.value);
      }
      return { body: data, isFormData: true };
    }
    return { body: encodedForm(form, submitter), isFormData: false };
  }

  function decodeResponse(status, contentType, body) {
    if (!contentType || contentType.toLowerCase().indexOf("application/json") === -1) {
      throw new Error("Réponse inattendue. Vérifiez votre session avant de réessayer.");
    }
    var data;
    try {
      data = JSON.parse(body);
    } catch (error) {
      throw new Error("Réponse financière illisible. Vérifiez l'historique avant toute nouvelle saisie.");
    }
    if (status < 200 || status >= 300 || !data.success) {
      throw new Error(data.message || "Enregistrement refusé.");
    }
    return data;
  }

  function sendWithFetch(action, payload, callback) {
    var headers = {
      "X-Requested-With": "XMLHttpRequest",
      "Accept": "application/json"
    };
    if (!payload.isFormData) {
      headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8";
    }
    window.fetch(action, {
      method: "POST",
      credentials: "same-origin",
      body: payload.body,
      headers: headers
    }).then(function (response) {
      var contentType = response.headers.get("content-type") || "";
      return response.text().then(function (body) {
        return decodeResponse(response.status, contentType, body);
      });
    }).then(function (data) {
      callback(null, data);
    }).catch(function (error) {
      callback(error);
    });
  }

  function sendWithXhr(action, payload, callback) {
    if (!window.XMLHttpRequest) {
      callback(new Error("Ce navigateur ne permet pas l'enregistrement asynchrone."));
      return;
    }
    var xhr = new XMLHttpRequest();
    xhr.open("POST", action, true);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    xhr.setRequestHeader("Accept", "application/json");
    if (!payload.isFormData) {
      xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded;charset=UTF-8");
    }
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      try {
        callback(null, decodeResponse(
          xhr.status,
          xhr.getResponseHeader("Content-Type") || "",
          xhr.responseText
        ));
      } catch (error) {
        callback(error);
      }
    };
    xhr.onerror = function () {
      callback(new Error("Connexion interrompue. Vérifiez l'historique avant de refaire la saisie."));
    };
    xhr.send(payload.body);
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var panel = document.getElementById(financeId);
    if (!panel || !form || !panel.contains(form) ||
        (!hasClass(form, "inv-finance-form") &&
         !hasClass(form, "inv-finance-decision-form"))) return;

    event.preventDefault();
    if (pending) {
      notify(panel, "Un enregistrement financier est déjà en cours.", "info");
      return;
    }
    if (form.checkValidity && !form.checkValidity()) {
      if (form.reportValidity) form.reportValidity();
      return;
    }

    var submitter = event.submitter || document.activeElement;
    var action = form.getAttribute("action");
    if (!action && typeof form.action === "string") action = form.action;
    if (!action) {
      notify(panel, "Destination du formulaire introuvable. Aucun rechargement n'a été effectué.", "danger");
      return;
    }

    var payload = makePayload(form, submitter);
    var buttons = Array.prototype.slice.call(
      form.querySelectorAll('button[type="submit"],input[type="submit"]')
    );
    buttons.forEach(function (button) { button.disabled = true; });
    pending = true;
    notify(panel, "Enregistrement en cours…", "info");

    function finish(error, data) {
      if (error) {
        notify(panel, error.message || "Enregistrement impossible. Vérifiez avant de réessayer.", "danger");
        buttons.forEach(function (button) { button.disabled = false; });
        pending = false;
        return;
      }
      if (data.html) {
        var updatedPanel = replacePanel(panel, data.html);
        if (updatedPanel) {
          notify(updatedPanel, data.message || "Enregistrement effectué.", "success");
        } else {
          buttons.forEach(function (button) { button.disabled = true; });
          notify(panel, (data.message || "Enregistrement effectué.") +
            " Les données sont enregistrées, mais le résumé n'a pas pu être actualisé. Consultez l'historique avant toute nouvelle saisie.", "warning");
        }
      } else {
        buttons.forEach(function (button) { button.disabled = true; });
        notify(panel, (data.message || "Enregistrement effectué.") +
          " Les données sont enregistrées, mais le résumé financier est indisponible. Consultez l'historique avant toute nouvelle saisie.", "warning");
      }
      pending = false;
    }

    if (window.fetch) sendWithFetch(action, payload, finish);
    else sendWithXhr(action, payload, finish);
  }, true);
}());
