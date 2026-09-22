(function () {
  'use strict';

  const queue = document.querySelector('.cashier-queue');
  if (!queue) return;

  const match = window.location.pathname.match(/\/bars\/(\d+)\//);
  if (!match) return;
  const barId = match[1];
  const endpoint = `/bars/${barId}/live/orders`;
  const unpaidUrl = `/bars/${barId}/unpaid-orders`;
  const waitingSection = document.getElementById('queueWaiting');
  const payableSection = document.getElementById('queuePayable');
  const productGrid = document.getElementById('cashierProductGrid');
  const productSearch = document.getElementById('cashierProductSearch');
  const money = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });

  const mobileMore = document.querySelector('.cashier-mobile-bar a:last-child');
  if (mobileMore) {
    mobileMore.href = unpaidUrl;
    mobileMore.innerHTML = '!<br>Impayées';
    mobileMore.setAttribute('aria-label', 'Commandes impayées');
  }
  const headActions = document.querySelector('.cashier-head-actions');
  if (headActions && !headActions.querySelector('[data-unpaid-shortcut]')) {
    const link = document.createElement('a');
    link.className = 'btn btn-warning';
    link.href = unpaidUrl;
    link.dataset.unpaidShortcut = '';
    link.textContent = '! Impayées';
    headActions.prepend(link);
  }

  const kpiLinks = Array.from(document.querySelectorAll('.cashier-kpis > a'));
  const waitingKpi = kpiLinks[0]?.querySelector('strong');
  const payableKpi = kpiLinks[1]?.querySelector('strong');
  const dueKpi = document.querySelector('.cashier-kpis > article strong');

  let knownOrderIds = new Set(
    Array.from(queue.querySelectorAll('.cashier-order-link')).map((link) => {
      try { return new URL(link.href, window.location.origin).searchParams.get('order_id'); }
      catch (error) { return null; }
    }).filter(Boolean)
  );
  let firstSuccessfulPoll = true;
  let timer = null;
  let inFlight = false;

  const top = queue.querySelector('.cashier-queue-top > div');
  const liveBadge = document.createElement('span');
  liveBadge.className = 'badge text-bg-success mt-1';
  liveBadge.textContent = '● Mise à jour auto';
  top?.appendChild(liveBadge);

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
  }

  function selectedOrderId() {
    const input = document.querySelector('#paymentPanel input[name="order_id"]');
    if (input?.value) return String(input.value);
    const active = queue.querySelector('.cashier-order-link.active');
    if (!active) return '';
    try { return new URL(active.href, window.location.origin).searchParams.get('order_id') || ''; }
    catch (error) { return ''; }
  }

  function sectionLabel(section, count) {
    const node = section?.querySelector('.cashier-queue-label span:last-child');
    if (node) node.textContent = String(count);
  }

  function orderLink(order, selectedId) {
    const isWaiting = order.state === 'waiting';
    const amount = Number(isWaiting ? order.net_sale : order.amount_due) || 0;
    const product = order.first_product
      ? ` · ${escapeHtml(order.first_product)}${Number(order.extra_lines || 0) > 0 ? ` +${Number(order.extra_lines)}` : ''}`
      : '';
    const href = `/bars/${barId}/cashier/workspace?order_id=${encodeURIComponent(order.id)}#paymentPanel`;
    const status = isWaiting ? 'À livrer' : (order.payment_status === 'PARTIAL' ? 'Partiel' : 'À payer');
    return `
      <a class="cashier-order-link ${String(order.id) === selectedId ? 'active' : ''}" href="${href}" data-live-order-id="${escapeHtml(order.id)}">
        <div class="cashier-order-copy">
          <strong class="cashier-human-order-title">${escapeHtml(order.display_name || (order.table && order.table !== 'Sans table' ? order.table : 'COMPTOIR'))}</strong>
          <small class="cashier-human-tech-ref">Réf. système : ${escapeHtml(order.reference)}</small>
          <small>${escapeHtml(order.server_name || 'Comptoir')} · ${escapeHtml(order.table || 'Sans table')}${product}</small>
        </div>
        <div class="cashier-order-money">
          <strong>${money.format(amount)} ${escapeHtml(order.currency || '')}</strong>
          <small>${status}</small>
        </div>
      </a>`;
  }

  function replaceSection(section, orders, selectedId, emptyText) {
    if (!section) return;
    Array.from(section.children).forEach((child) => {
      if (!child.classList.contains('cashier-queue-label')) child.remove();
    });
    if (!orders.length) {
      const empty = document.createElement('div');
      empty.className = 'p-3 text-secondary small';
      empty.textContent = emptyText;
      section.appendChild(empty);
      return;
    }
    section.insertAdjacentHTML('beforeend', orders.map((order) => orderLink(order, selectedId)).join(''));
  }

  function syncStock(stock) {
    if (!productGrid) return;
    const activeCategory = document.querySelector('#cashierCategoryTabs .pos-category.active')?.dataset.category || 'all';
    const query = (productSearch?.value || '').trim().toLocaleLowerCase('fr');

    productGrid.querySelectorAll('.pos-product[data-product-id]').forEach((card) => {
      const quantity = Number(stock?.[String(card.dataset.productId)] || 0);
      card.dataset.stock = String(quantity);
      const available = Number.isFinite(quantity) && quantity > 0;
      card.classList.toggle('out-of-stock', !available);
      card.disabled = !available;
      const badge = card.querySelector('.pos-stock');
      if (badge) {
        badge.textContent = available ? money.format(quantity) : 'Rupture';
        badge.classList.toggle('danger', !available);
      }
      const categoryOk = activeCategory === 'all' || card.dataset.category === activeCategory;
      const searchOk = !query || (card.dataset.name || '').toLocaleLowerCase('fr').includes(query);
      card.hidden = !(available && categoryOk && searchOk);
    });
  }

  function showNewOrderNotice() {
    liveBadge.className = 'badge text-bg-warning mt-1';
    liveBadge.textContent = '● Nouvelle commande reçue';
    window.setTimeout(() => {
      liveBadge.className = 'badge text-bg-success mt-1';
      liveBadge.textContent = '● Mise à jour auto';
    }, 4500);
  }

  function apply(payload) {
    if (!payload || (payload.mode !== 'CASHIER' && payload.mode !== 'ADMIN')) return;
    const orders = Array.isArray(payload.orders) ? payload.orders : [];
    const selectedId = selectedOrderId();
    const waiting = orders.filter((order) => order.state === 'waiting');
    const payable = orders.filter((order) => order.state === 'to_pay');

    replaceSection(payableSection, payable, selectedId, 'Aucune commande à encaisser.');
    replaceSection(waitingSection, waiting, selectedId, 'Aucune commande à livrer.');
    sectionLabel(payableSection, payable.length);
    sectionLabel(waitingSection, waiting.length);

    if (waitingKpi) waitingKpi.textContent = String(payload.stats?.waiting ?? waiting.length);
    if (payableKpi) payableKpi.textContent = String(payload.stats?.to_pay ?? payable.length);
    if (dueKpi) dueKpi.textContent = money.format(Number(payload.stats?.due || 0));

    syncStock(payload.stock || {});

    const nextIds = new Set(orders.map((order) => String(order.id)));
    if (!firstSuccessfulPoll) {
      const hasNewOrder = orders.some((order) => !knownOrderIds.has(String(order.id)));
      if (hasNewOrder) showNewOrderNotice();
    }
    knownOrderIds = nextIds;
    firstSuccessfulPoll = false;
  }

  async function poll() {
    if (inFlight || document.hidden) return;
    inFlight = true;
    try {
      const response = await fetch(endpoint, {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) return;
      apply(await response.json());
    } catch (error) {
      // A temporary network error must not interrupt the cashier workflow.
    } finally {
      inFlight = false;
    }
  }

  function schedule() {
    window.clearInterval(timer);
    timer = window.setInterval(poll, 4000);
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) poll();
  });

  poll();
  schedule();
})();
