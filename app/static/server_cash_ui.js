/* Waitress cash tender preview. Server is authoritative for actual payment values. */
(function () {
  'use strict';

  var form = document.querySelector('[data-server-cash-form]');
  if (!form) return;

  var receivedInput = form.querySelector('[name="amount_presented"]');
  var changeNode = form.querySelector('[data-server-cash-change]');
  var remainingNode = form.querySelector('[data-server-cash-remaining]');
  var submitButton = form.querySelector('button[type="submit"]');
  var due = Number(form.getAttribute('data-amount-due'));
  var currency = form.getAttribute('data-currency') || 'FCFA';
  if (!receivedInput || !changeNode || !remainingNode || !submitButton ||
      !Number.isFinite(due) || due <= 0) return;

  var format = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 2 });
  function amount(value) {
    return format.format(value) + ' ' + currency;
  }

  function updatePreview() {
    var raw = receivedInput.value.trim();
    var received = raw ? Number(raw) : 0;
    var valid = raw !== '' && Number.isFinite(received) && received > 0 &&
      receivedInput.checkValidity();
    var change = valid ? Math.max(0, received - due) : 0;
    var remaining = valid ? Math.max(0, due - received) : due;
    changeNode.textContent = amount(change);
    remainingNode.textContent = amount(remaining);
    submitButton.disabled = !valid;
    submitButton.textContent = !valid ? 'Confirmer l’encaissement'
      : 'Enregistrer ' + amount(Math.min(received, due));
  }

  receivedInput.addEventListener('input', updatePreview);
  receivedInput.addEventListener('change', updatePreview);

  form.addEventListener('barpro:async-success', function (event) {
    var payload = event.detail && event.detail.payload;
    var destination = payload && payload.data && payload.data.refresh_url;
    // Always re-read the authoritative balance after a committed payment.
    window.location.assign(destination || window.location.pathname);
  });

  updatePreview();
})();
