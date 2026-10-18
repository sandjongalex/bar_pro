/* Skeleton templates for genuine async BAR_PRO UI requests.
   HTML is static: never insert API values or user input into these placeholders. */
(function () {
  'use strict';

  const line = (width, extra = '') =>
    '<span class="bp-skeleton-line ' + extra + '" style="--bp-skeleton-width:' + width + '" aria-hidden="true"></span>';

  function editor() {
    return '<div class="bp-skeleton-editor" data-skeleton-placeholder role="status" aria-label="Chargement de la commande">' +
      '<div class="bp-skeleton-editor-head">' + line('48%') + line('75%', 'bp-skeleton-line-small') + '</div>' +
      '<div class="bp-skeleton-editor-row">' + line('55%') + line('22%') + '</div>' +
      '<div class="bp-skeleton-editor-row">' + line('64%') + line('22%') + '</div>' +
      '<div class="bp-skeleton-editor-footer">' + line('58%') + line('31%') + '</div>' +
      '<span class="visually-hidden">Chargement de la commande…</span></div>';
  }

  function queue() {
    return '<div class="bp-skeleton-queue" data-skeleton-placeholder role="status" aria-label="Actualisation de la file de caisse">' +
      '<div>' + line('64%') + line('38%', 'bp-skeleton-line-small') + '</div>' +
      line('22%') +
      '<span class="visually-hidden">Chargement de la file de caisse…</span></div>';
  }

  function unpaid() {
    const card = '<article class="bp-skeleton-unpaid-card" aria-hidden="true">' +
      '<div class="bp-skeleton-unpaid-head">' + line('50%') + line('28%') + '</div>' +
      line('35%', 'bp-skeleton-line-small') +
      '<div class="bp-skeleton-unpaid-lines">' + line('85%') + line('66%') + '</div>' +
      '<div class="bp-skeleton-unpaid-footer">' + line('39%') + line('26%') + '</div>' +
      '</article>';
    return '<div class="bp-skeleton-unpaid-grid" data-skeleton-placeholder role="status" aria-label="Chargement des commandes impayées">' +
      card + card + '<span class="visually-hidden">Chargement des commandes impayées…</span></div>';
  }

  window.BarProSkeleton = Object.freeze({ editor, queue, unpaid });
})();
