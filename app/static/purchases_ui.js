(function () {
  'use strict';

  const form = document.getElementById('purchaseForm');
  const container = document.getElementById('purchaseLines');
  if (!form || !container) return;

  const match = window.location.pathname.match(/\/bars\/(\d+)\/purchases/);
  if (!match) return;

  const barId = match[1];
  let currency = 'XAF';
  const productMeta = new Map();

  const money = new Intl.NumberFormat('fr-FR', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });

  function formatMoney(value) {
    const number = Number(value || 0);
    return `${money.format(Number.isFinite(number) ? number : 0)} ${currency}`;
  }

  function ensureSummary() {
    let summary = document.getElementById('purchaseLiveSummary');
    if (summary) return summary;

    summary = document.createElement('div');
    summary.id = 'purchaseLiveSummary';
    summary.className = 'purchase-live-summary';
    summary.innerHTML = `
      <div class="purchase-summary-card">
        <span>Montant total achat</span>
        <strong data-purchase-total>0 ${currency}</strong>
      </div>
      <div class="purchase-summary-card">
        <span>Chiffre d'affaires potentiel</span>
        <strong data-purchase-revenue>—</strong>
        <small>Calculé si unités/casier renseignées</small>
      </div>
      <div class="purchase-summary-card purchase-summary-profit">
        <span>Bénéfice brut estimé</span>
        <strong data-purchase-profit>—</strong>
        <small>CA potentiel − coût d'achat</small>
      </div>
    `;
    container.insertAdjacentElement('afterend', summary);
    return summary;
  }

  function ensureMetrics(row) {
    let metrics = row.querySelector('.purchase-line-metrics');
    if (metrics) return metrics;

    metrics = document.createElement('div');
    metrics.className = 'purchase-line-metrics';
    metrics.innerHTML = `
      <span>Total ligne <strong data-line-total>0 ${currency}</strong></span>
      <span>CA potentiel <strong data-line-revenue>—</strong></span>
      <span>Bénéfice brut <strong data-line-profit>—</strong></span>
    `;
    row.appendChild(metrics);
    return metrics;
  }

  function selectedMeta(row) {
    const select = row.querySelector('.product-select');
    if (!select || !select.value) return null;
    return productMeta.get(String(select.value)) || null;
  }

  function calculateRow(row) {
    const quantityInput = row.querySelector('input[name="quantity"]');
    const costInput = row.querySelector('.unit-cost');
    const metrics = ensureMetrics(row);
    const meta = selectedMeta(row);

    const quantity = Number(quantityInput && quantityInput.value ? quantityInput.value : 0);
    const cost = Number(costInput && costInput.value ? costInput.value : 0);
    const total = quantity > 0 && cost >= 0 ? quantity * cost : 0;

    metrics.querySelector('[data-line-total]').textContent = formatMoney(total);

    let revenue = null;
    let profit = null;
    if (meta && Number(meta.units_per_case) > 0 && quantity > 0) {
      revenue = quantity * Number(meta.units_per_case) * Number(meta.sale_price || 0);
      profit = revenue - total;
      metrics.querySelector('[data-line-revenue]').textContent = formatMoney(revenue);
      metrics.querySelector('[data-line-profit]').textContent = formatMoney(profit);
    } else if (meta && quantity > 0) {
      metrics.querySelector('[data-line-revenue]').textContent = 'À configurer';
      metrics.querySelector('[data-line-profit]').textContent = 'À configurer';
    } else {
      metrics.querySelector('[data-line-revenue]').textContent = '—';
      metrics.querySelector('[data-line-profit]').textContent = '—';
    }

    row.dataset.purchaseTotal = String(total);
    row.dataset.purchaseRevenue = revenue === null ? '' : String(revenue);
    row.dataset.purchaseProfit = profit === null ? '' : String(profit);
  }

  function calculateAll() {
    const rows = Array.from(container.querySelectorAll('.purchase-entry-line'));
    let total = 0;
    let revenue = 0;
    let profit = 0;
    let selectedRows = 0;
    let missingProfitData = false;

    rows.forEach((row) => {
      calculateRow(row);
      const select = row.querySelector('.product-select');
      const quantity = Number(row.querySelector('input[name="quantity"]')?.value || 0);
      if (!select || !select.value || quantity <= 0) return;

      selectedRows += 1;
      total += Number(row.dataset.purchaseTotal || 0);
      if (row.dataset.purchaseRevenue === '' || row.dataset.purchaseProfit === '') {
        missingProfitData = true;
      } else {
        revenue += Number(row.dataset.purchaseRevenue || 0);
        profit += Number(row.dataset.purchaseProfit || 0);
      }
    });

    const summary = ensureSummary();
    summary.querySelector('[data-purchase-total]').textContent = formatMoney(total);

    if (selectedRows > 0 && !missingProfitData) {
      summary.querySelector('[data-purchase-revenue]').textContent = formatMoney(revenue);
      summary.querySelector('[data-purchase-profit]').textContent = formatMoney(profit);
    } else if (selectedRows > 0) {
      summary.querySelector('[data-purchase-revenue]').textContent = 'À configurer';
      summary.querySelector('[data-purchase-profit]').textContent = 'À configurer';
    } else {
      summary.querySelector('[data-purchase-revenue]').textContent = '—';
      summary.querySelector('[data-purchase-profit]').textContent = '—';
    }
  }

  function bindRow(row) {
    if (row.dataset.purchaseCalculatorBound === '1') return;
    row.dataset.purchaseCalculatorBound = '1';

    const quantity = row.querySelector('input[name="quantity"]');
    const cost = row.querySelector('.unit-cost');
    const select = row.querySelector('.product-select');

    if (quantity) {
      // The previous min=0.000001 + step=0.001 made values such as 2 invalid
      // in HTML5 validation because the step base did not align with integers.
      quantity.min = '0.001';
      quantity.step = '0.001';
      quantity.addEventListener('input', calculateAll);
      quantity.addEventListener('change', calculateAll);
    }

    if (cost) {
      cost.addEventListener('input', calculateAll);
      cost.addEventListener('change', calculateAll);
    }

    if (select) {
      select.addEventListener('change', function () {
        const meta = selectedMeta(row);
        if (meta && cost && (!cost.value || Number(cost.value) === 0)) {
          cost.value = meta.default_purchase_price;
        }
        calculateAll();
      });
    }

    ensureMetrics(row);
  }

  function bindAllRows() {
    container.querySelectorAll('.purchase-entry-line').forEach(bindRow);
    calculateAll();
  }

  ensureSummary();
  bindAllRows();

  const observer = new MutationObserver(function () {
    bindAllRows();
  });
  observer.observe(container, { childList: true });

  container.addEventListener('click', function (event) {
    if (event.target.closest('.remove-purchase-line')) {
      window.setTimeout(calculateAll, 0);
    }
  });

  fetch(`/bars/${barId}/purchases/product-meta`, {
    credentials: 'same-origin',
    headers: { 'Accept': 'application/json' },
  })
    .then((response) => {
      if (!response.ok) throw new Error('Unable to load product metadata');
      return response.json();
    })
    .then((payload) => {
      currency = payload.currency || currency;
      (payload.products || []).forEach((product) => {
        productMeta.set(String(product.id), product);
      });
      calculateAll();
    })
    .catch(() => {
      // Purchase totals still work without metadata; margin preview stays unavailable.
      calculateAll();
    });
})();
