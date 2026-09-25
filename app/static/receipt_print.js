/* Keep the app launch synchronous with the user's tap (Android browser policy). */
(function () {
  'use strict';

  const buttons = Array.from(document.querySelectorAll('.print-receipt'));
  const tables = Array.from(document.querySelectorAll('.receipt-lines'));
  const help = document.getElementById('print-help');
  const modeLabel = document.getElementById('receipt-mode-label');
  const browserButton = document.getElementById('print-browser');
  const android = /Android/i.test(navigator.userAgent);
  let activeMode = 'detailed';

  function setMode(mode) {
    activeMode = mode === 'cumulative' ? 'cumulative' : 'detailed';
    tables.forEach(function (table) {
      table.hidden = table.dataset.receiptMode !== activeMode;
    });
    if (modeLabel) {
      modeLabel.textContent = activeMode === 'cumulative' ? 'Facture cumulée' : 'Facture détaillée';
    }
  }

  setMode(activeMode);

  buttons.forEach(function (button) {
    if (android) {
      button.textContent = button.dataset.printMode === 'cumulative'
        ? 'RawBT · Facture cumulée'
        : 'RawBT · Facture détaillée';
    }

    button.addEventListener('click', function () {
      setMode(button.dataset.printMode);
      if (!android) {
        window.print();
        return;
      }

      help.textContent = activeMode === 'cumulative'
        ? 'Ouverture de RawBT avec la facture cumulée. Les mêmes produits au même prix sont regroupés.'
        : 'Ouverture de RawBT avec la facture détaillée. Chaque ligne de commande reste visible.';
      window.location.href = button.dataset.rawbtIntent;
    });
  });

  if (browserButton) {
    browserButton.addEventListener('click', function () {
      window.print();
    });
  }
})();
