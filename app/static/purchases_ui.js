(function () {
  'use strict';

  const forms = Array.from(document.querySelectorAll('.purchase-entry-form'));
  if (!forms.length) return;

  const match = window.location.pathname.match(/\/bars\/(\d+)\/purchases/);
  if (!match) return;

  const barId = match[1];
  const productMeta = new Map();
  const refreshers = [];
  const money = new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 0, maximumFractionDigits: 0 });

  function setupForm(form) {
    const container = form.querySelector('.purchase-entry-lines');
    if (!container) return null;

    const currency = form.dataset.currency || 'XAF';
    const searchInput = form.querySelector('.purchase-product-search');
    const selectedCount = form.querySelector('.purchase-selected-count');
    const paymentAmount = form.querySelector('.initial-payment-amount');
    const paymentMethod = form.querySelector('.initial-payment-method');
    const cashBlock = form.querySelector('.purchase-cash-session');
    const cashSelect = cashBlock ? cashBlock.querySelector('select') : null;
    const providerBlocks = Array.from(form.querySelectorAll('.purchase-provider-field'));

    function formatMoney(value) {
      const number = Number(value || 0);
      return `${money.format(Number.isFinite(number) ? number : 0)} ${currency}`;
    }

    function metaFor(row) {
      return productMeta.get(String(row.dataset.productId || '')) || null;
    }

    function packStorageKey(productId) {
      return `bar-pro:purchase-pack-size:${barId}:${productId}`;
    }

    function ensureSummary() {
      let summary = form.querySelector('.purchase-live-summary');
      if (summary) return summary;
      summary = document.createElement('div');
      summary.className = 'purchase-live-summary';
      summary.innerHTML = `
        <div class="purchase-summary-card">
          <span>Montant total achat</span>
          <strong data-purchase-total>0 ${currency}</strong>
        </div>
        <div class="purchase-summary-card">
          <span>Chiffre d'affaires potentiel</span>
          <strong data-purchase-revenue>—</strong>
          <small>Stock reçu × prix de vente</small>
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

    function hydratePackSize(row) {
      const input = row.querySelector('.pack-size-input');
      if (!input || input.value) return;
      const productId = String(row.dataset.productId || '');
      const meta = metaFor(row);
      let stored = '';
      try {
        stored = window.localStorage.getItem(packStorageKey(productId)) || '';
      } catch (error) {
        stored = '';
      }
      const candidate = Number(meta && meta.units_per_case ? meta.units_per_case : stored || 0);
      if (Number.isInteger(candidate) && candidate > 0) input.value = String(candidate);
    }

    function syncUnit(row) {
      const unit = row.querySelector('.purchase-unit');
      const pack = row.querySelector('.pack-size-input');
      const quantity = Number(row.querySelector('.purchase-quantity')?.value || 0);
      if (!unit || !pack) return;
      const isCase = unit.value === 'CASE';
      pack.readOnly = !isCase;
      pack.classList.toggle('purchase-pack-disabled', !isCase);
      pack.required = isCase && quantity > 0;
      if (isCase) hydratePackSize(row);
    }

    function calculateRow(row) {
      const quantityInput = row.querySelector('.purchase-quantity');
      const priceInput = row.querySelector('.unit-cost');
      const unitInput = row.querySelector('.purchase-unit');
      const packInput = row.querySelector('.pack-size-input');
      const totalNode = row.querySelector('[data-line-total]');
      const conversionNode = row.querySelector('[data-stock-conversion]');
      const revenueNode = row.querySelector('[data-line-revenue]');
      const profitNode = row.querySelector('[data-line-profit]');
      const meta = metaFor(row);

      const quantity = Number(quantityInput?.value || 0);
      const price = Number(priceInput?.value || 0);
      const packSize = Number(packInput?.value || 0);
      const isCase = unitInput?.value === 'CASE';
      const selected = quantity > 0;
      const total = selected && price >= 0 ? quantity * price : 0;
      let stockUnits = null;

      if (selected) {
        if (isCase && Number.isInteger(packSize) && packSize > 0) stockUnits = quantity * packSize;
        if (!isCase) stockUnits = quantity;
      }

      row.classList.toggle('is-selected', selected);
      if (totalNode) totalNode.textContent = formatMoney(total);
      if (conversionNode) {
        if (!selected) conversionNode.textContent = 'Stock : —';
        else if (stockUnits === null) conversionNode.textContent = 'Stock : renseigner bouteilles/casier';
        else conversionNode.textContent = `Stock : +${stockUnits} bouteille${stockUnits > 1 ? 's' : ''}`;
      }

      let revenue = null;
      let profit = null;
      if (selected && stockUnits !== null && meta) {
        revenue = stockUnits * Number(meta.sale_price || 0);
        profit = revenue - total;
        if (revenueNode) revenueNode.textContent = formatMoney(revenue);
        if (profitNode) profitNode.textContent = formatMoney(profit);
      } else if (selected) {
        if (revenueNode) revenueNode.textContent = 'À compléter';
        if (profitNode) profitNode.textContent = 'À compléter';
      } else {
        if (revenueNode) revenueNode.textContent = '—';
        if (profitNode) profitNode.textContent = '—';
      }

      row.dataset.purchaseTotal = String(total);
      row.dataset.purchaseRevenue = revenue === null ? '' : String(revenue);
      row.dataset.purchaseProfit = profit === null ? '' : String(profit);
      syncUnit(row);
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
        const quantity = Number(row.querySelector('.purchase-quantity')?.value || 0);
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
      const quantity = row.querySelector('.purchase-quantity');
      const price = row.querySelector('.unit-cost');
      const unit = row.querySelector('.purchase-unit');
      const pack = row.querySelector('.pack-size-input');

      [quantity, price].forEach((input) => {
        if (!input) return;
        input.addEventListener('input', calculateAll);
        input.addEventListener('change', calculateAll);
      });
      if (unit) unit.addEventListener('change', calculateAll);
      if (pack) {
        pack.addEventListener('input', function () {
          const productId = String(row.dataset.productId || '');
          if (productId && Number(pack.value) > 0) {
            try { window.localStorage.setItem(packStorageKey(productId), pack.value); } catch (error) {}
          }
          calculateAll();
        });
      }
      hydratePackSize(row);
      syncUnit(row);
    }

    function syncPaymentFields() {
      const amount = Number(paymentAmount?.value || 0);
      const enabled = amount > 0;
      if (paymentMethod) paymentMethod.required = enabled;
      const cash = enabled && paymentMethod?.value === 'CASH';
      if (cashBlock) cashBlock.classList.toggle('d-none', !cash);
      if (cashSelect) cashSelect.required = cash;
      const provider = enabled && paymentMethod?.value && paymentMethod.value !== 'CASH';
      providerBlocks.forEach((block) => block.classList.toggle('d-none', !provider));
    }

    container.querySelectorAll('.purchase-entry-line').forEach(bindRow);
    ensureSummary();
    calculateAll();
    syncPaymentFields();

    if (searchInput) searchInput.addEventListener('input', filterRows);
    if (paymentAmount) paymentAmount.addEventListener('input', syncPaymentFields);
    if (paymentMethod) paymentMethod.addEventListener('change', syncPaymentFields);

    form.addEventListener('submit', function (event) {
      const mode = event.submitter?.value || 'draft';
      const amount = Number(paymentAmount?.value || 0);
      if (amount > 0 && mode !== 'receive') {
        event.preventDefault();
        paymentAmount.setCustomValidity('Le paiement initial nécessite « Enregistrer & réceptionner ».');
        paymentAmount.reportValidity();
        window.setTimeout(() => paymentAmount.setCustomValidity(''), 0);
        return;
      }

      container.querySelectorAll('.purchase-entry-line').forEach((row) => {
        const quantity = Number(row.querySelector('.purchase-quantity')?.value || 0);
        const disabled = quantity <= 0;
        row.querySelectorAll(
          'input[name="product_id"], select[name="purchase_unit"], input[name="purchase_quantity"], input[name="units_per_case"], input[name="purchase_unit_price"]'
        ).forEach((input) => { input.disabled = disabled; });
      });
    });

    return function refresh() {
      container.querySelectorAll('.purchase-entry-line').forEach((row) => {
        hydratePackSize(row);
        syncUnit(row);
      });
      calculateAll();
    };
  }

  forms.forEach((form) => {
    const refresh = setupForm(form);
    if (refresh) refreshers.push(refresh);
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
      (payload.products || []).forEach((product) => productMeta.set(String(product.id), product));
      refreshers.forEach((refresh) => refresh());
    })
    .catch(() => refreshers.forEach((refresh) => refresh()));
})();
