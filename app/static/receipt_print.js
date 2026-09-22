/* Keep the app launch synchronous with the user's tap (Android browser policy). */
(function () {
  'use strict';
  const button = document.getElementById('print-receipt');
  const help = document.getElementById('print-help');
  const android = /Android/i.test(navigator.userAgent);
  document.getElementById('print-browser').addEventListener('click', function () {
    window.print();
  });
  if (android) button.textContent = 'Imprimer avec RawBT';
  button.addEventListener('click', function () {
    if (!android) {
      window.print();
      return;
    }
    help.textContent = 'Ouverture de RawBT demandée. Vérifiez le ticket sur l’imprimante avant de réessayer. Si rien ne se passe, ouvrez RawBT et vérifiez sa connexion Bluetooth.';
    window.location.href = button.dataset.rawbtIntent;
  });
})();
