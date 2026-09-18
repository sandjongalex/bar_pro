(function () {
  'use strict';

  const form = document.getElementById('purchaseForm');
  const container = document.getElementById('purchaseLines');
  if (!form || !container) return;

  const match = window.location.pathname.match(/\/bars\/(\d+)\/purchases/);
  if (!match) return;

  const barId = match[1];
  let currency = form.dataset.currency || 'XAF';
  const productMeta = new Map();
  const searchInput = document.getElementById('purchaseProductSearch');
  const selectedCount = document.getElementById('purchaseSelectedCount');

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
        <small>Selon le prix de vente et les unités par casier/carton</small>
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

  function ensureAnalysis(row) {
    let analysis = row.querySelector('.purchase-line-analysis');
    if (analysis) return analysis;

    analysis = document.createElement('div');
    analysis.className = 'purchase-line-analysis';
    analysis.innerHTML = `
      <label class="purchase-pack-size">
        <span>Unités / casier-carton</span>
        <input class="form-control form-control-sm pack-size-input" type="number" min="1" step="1" placeholder="Ex. 12">
      </label>
      <span>CA potentiel <strong data-line-revenue>—</strong></span>
      <span>Bénéfice brut <strong data-line-profit>—</strong></span>
    `;
    row.appendChild(analysis);
    return analysis;
  }

  function selectedMeta(row) {
    return productMeta.get(String(row.dataset.productId || '')) || null;
  }

  function packStorageKey(productId) {
    return `bar-pro:purchase-pack-size:${barId}:${productId}`;
  }

  function selectedPackSize(row) {
    const input = row.querySelector('.pack-size-input');
    const value = Number(input && input.value ? input.value : 0);
    if (Number.isInteger(value) && value > 0) return value;
    const meta = selectedMeta(row);
    const fallback = Number(meta && meta.units_per_case ? meta.units_per_case : 0);
    return Number.isInteger(fallback) && fallback > 0 ? fallback : 0;
  }

  function hydratePackSize(row) {
    const input = row.querySelector('.pack-size-input');
    const productId = String(row.dataset.productId || '');
    if (!input || !productId) return;

    const meta = selectedMeta(row);
    const stored = window.localStorage.getItem(packStorageKey(productId));
    const candidate = Number(meta && meta.units_per_case ? meta.units_per_case : stored || 0);
    input.value = Number.isInteger(candidate) && candidate > 0 ? String(candidate) : '';
  }

  function calculateRow(row) {
    const quantityInput = row.querySelector('input[name="quantity"]');
    const costInput = row.querySelector('.unit-cost');
    const lineTotal = row.querySelector('[data-line-total]');
    const analysis = ensureAnalysis(row);
    const meta = selectedMeta(row);

    const quantity = Number(quantityInput && quantityInput.value ? quantityInput.value : 0);
    const cost = Number(costInput && costInput.value ? costInput.value : 0);
    const selected = quantity > 0;
    const total = selected && cost >= 0 ? quantity * cost : 0;

    row.classList.toggle('is-selected', selected);
    if (lineTotal) lineTotal.textContent = formatMoney(total);

    let revenue = null;
    let profit = null;
    const packSize = selectedPackSize(row);
    if (selected && meta && packSize > 0) {
      revenue = quantity * packSize * Number(meta.sale_price || 0);
      profit = revenue - total;
      analysis.querySelector('[data-line-revenue]').textContent = formatMoney(revenue);
      analysis.querySelector('[data-line-profit]').textContent = formatMoney(profit);
    } else if (selected && meta) {
      analysis.querySelector('[data-line-revenue]').textContent = 'Renseigner unités/casier';
      analysis.querySelector('[data-line-profit]').textContent = 'Renseigner unités/casier';
    } else {
      analysis.querySelector('[data-line-revenue]').textContent = '—';
      analysis.querySelector('[data-line-profit]').textContent = '—';
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
    let activeRows = 0;
    let missingProfitData = false;

    rows.forEach((row) => {
      calculateRow(row);
      const quantity = Number(row.querySelector('input[name="quantity"]')?.value || 0);
      if (quantity <= 0) return;

      activeRows += 1;
      total += Number(row.dataset.purchaseTotal || 0);
      if (row.dataset.purchaseRevenue === '' || row.dataset.purchaseProfit === '') {
        missingProfitData = true;
      } else {
        revenue += Number(row.dataset.purchaseRevenue || 0);
        profit += Number(row.dataset.purchaseProfit || 0);
      }
    });

    if (selectedCount) {
      selectedCount.textContent = `${activeRows} produit${activeRows > 1 ? 's' : ''} sélectionné${activeRows > 1 ? 's' : ''}`;
      selectedCount.classList.toggle('has-selection', activeRows > 0);
    }

    const summary = ensureSummary();
    summary.querySelector('[data-purchase-total]').textContent = formatMoney(total);

    if (activeRows > 0 && !missingProfitData) {
      summary.querySelector('[data-purchase-revenue]').textContent = formatMoney(revenue);
      summary.querySelector('[data-purchase-profit]').textContent = formatMoney(profit);
    } else if (activeRows > 0) {
      summary.querySelector('[data-purchase-revenue]').textContent = 'À compléter';
      summary.querySelector('[data-purchase-profit]').textContent = 'À compléter';
    } else {
      summary.querySelector('[data-purchase-revenue]').textContent = '—';
      summary.querySelector('[data-purchase-profit]').textContent = '—';
    }
  }

  function filterRows() {
    const query = (searchInput?.value || '').trim().toLocaleLowerCase('fr');
    container.querySelectorAll('.purchase-entry-line').forEach((row) => {
      const haystack = (row.dataset.search || '').toLocaleLowerCase('fr');
      row.hidden = Boolean(query && !haystack.includes(query));
    });
  }

  function bindRow(row) {
    if (row.dataset.purchaseCalculatorBound === '1') return;
    row.dataset.purchaseCalculatorBound = '1';

    const quantity = row.querySelector('input[name="quantity"]');
    const cost = row.querySelector('.unit-cost');
    const analysis = ensureAnalysis(row);
    const packInput = analysis.querySelector('.pack-size-input');

    if (quantity) {
      quantity.min = '0.001';
      quantity.step = '0.001';
      quantity.addEventListener('input', calculateAll);
      quantity.addEventListener('change', calculateAll);
    }

    if (cost) {
      cost.addEventListener('input', calculateAll);
      cost.addEventListener('change', calculateAll);
    }

    if (packInput) {
      packInput.addEventListener('input', function () {
        const productId = String(row.dataset.productId || '');
        if (productId && Number(packInput.value) > 0) {
          window.localStorage.setItem(packStorageKey(productId), packInput.value);
        }
        calculateAll();
      });
    }

    hydratePackSize(row);
  }

  container.querySelectorAll('.purchase-entry-line').forEach(bindRow);
  ensureSummary();
  calculateAll();

  if (searchInput) {
    searchInput.addEventListener('input', filterRows);
  }

  form.addEventListener('submit', function () {
    // Keep every product visible while editing, but submit only products that
    // actually have a purchased quantity.
    container.querySelectorAll('.purchase-entry-line').forEach((row) => {
      const quantity = Number(row.querySelector('input[name="quantity"]')?.value || 0);
      const disabled = quantity <= 0;
      row.querySelectorAll('input[name="product_id"], input[name="quantity"], input[name="unit_cost"]').forEach((input) => {
        input.disabled = disabled;
      });
    });
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
      container.querySelectorAll('.purchase-entry-line').forEach(hydratePackSize);
      calculateAll();
    })
    .catch(() => {
      // Purchase totals still work without metadata; margin preview stays unavailable.
      calculateAll();
    });
})();