(function () {
  'use strict';

  const preview = document.querySelector('[data-server-invoice-preview]');
  if (!preview) return;

  const openButton = document.querySelector('[data-server-invoice-open]');
  const closeButton = preview.querySelector('[data-server-invoice-close]');
  const modeButtons = Array.from(preview.querySelectorAll('[data-server-invoice-mode]'));
  const tables = Array.from(preview.querySelectorAll('[data-server-invoice-table]'));
  const modeLabel = preview.querySelector('[data-server-invoice-mode-label]');
  const detailedLines = Array.from(preview.querySelectorAll('[data-server-invoice-line]'));
  const cumulativeBody = preview.querySelector('[data-server-invoice-cumulative-body]');
  const currency = preview.dataset.currency || '';

  function decimal(value) {
    const parsed = Number.parseFloat(String(value || '0').replace(',', '.'));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function formatNumber(value) {
    return new Intl.NumberFormat('fr-FR', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2
    }).format(value);
  }

  function cell(text, className) {
    const td = document.createElement('td');
    td.textContent = text;
    if (className) td.className = className;
    return td;
  }

  function buildCumulativeTable() {
    if (!cumulativeBody || cumulativeBody.dataset.ready === '1') return;

    const grouped = new Map();
    detailedLines.forEach(function (row) {
      const key = JSON.stringify([
        row.dataset.productId || '',
        row.dataset.productName || '',
        row.dataset.unit || '',
        row.dataset.unitPrice || '0'
      ]);
      if (!grouped.has(key)) {
        grouped.set(key, {
          name: row.dataset.productName || '',
          unit: row.dataset.unit || '',
          unitPrice: decimal(row.dataset.unitPrice),
          quantity: 0,
          total: 0
        });
      }
      const item = grouped.get(key);
      item.quantity += decimal(row.dataset.quantity);
      item.total += decimal(row.dataset.total);
    });

    grouped.forEach(function (item) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(item.name));
      tr.appendChild(cell(formatNumber(item.quantity), 'text-center'));
      tr.appendChild(cell(formatNumber(item.unitPrice), 'text-end'));
      tr.appendChild(cell(formatNumber(item.total), 'text-end'));
      cumulativeBody.appendChild(tr);
    });

    if (!grouped.size) {
      const tr = document.createElement('tr');
      const td = cell('Aucun article à afficher.');
      td.colSpan = 4;
      td.className = 'server-invoice-empty-row';
      tr.appendChild(td);
      cumulativeBody.appendChild(tr);
    }

    cumulativeBody.dataset.ready = '1';
  }

  function setMode(mode) {
    const activeMode = mode === 'cumulative' ? 'cumulative' : 'detailed';
    if (activeMode === 'cumulative') buildCumulativeTable();

    tables.forEach(function (table) {
      table.hidden = table.dataset.serverInvoiceTable !== activeMode;
    });
    modeButtons.forEach(function (button) {
      const active = button.dataset.serverInvoiceMode === activeMode;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    if (modeLabel) {
      modeLabel.textContent = activeMode === 'cumulative' ? 'Facture cumulée' : 'Facture détaillée';
    }
  }

  function openPreview() {
    preview.hidden = false;
    if (openButton) openButton.setAttribute('aria-expanded', 'true');
    setMode('detailed');
    preview.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function closePreview() {
    preview.hidden = true;
    if (openButton) {
      openButton.setAttribute('aria-expanded', 'false');
      openButton.focus();
    }
  }

  if (openButton) openButton.addEventListener('click', openPreview);
  if (closeButton) closeButton.addEventListener('click', closePreview);
  modeButtons.forEach(function (button) {
    button.addEventListener('click', function () {
      setMode(button.dataset.serverInvoiceMode);
    });
  });

  // Keep this screen strictly as a consultation view: no print action or RawBT
  // intent is created here. Printing remains a cashier-only workflow.
  preview.dataset.currencyLabel = currency;
  setMode('detailed');
})();
