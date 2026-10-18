/* Incremental cashier queue: keyed cards, stable focus and no full-page reload. */
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
  const liveBadge = document.createElement('span');
  liveBadge.className = 'badge text-bg-success mt-1';
  liveBadge.textContent = '● Mise à jour auto';
  queue.querySelector('.cashier-queue-top > div')?.appendChild(liveBadge);

  const initialIds = Array.from(queue.querySelectorAll('.cashier-order-link')).map(link => {
    try { return new URL(link.href).searchParams.get('order_id'); }
    catch (_) { return null; }
  }).filter(Boolean);
  let knownOrderIds = new Set(initialIds);
  let firstSuccessfulPoll = true;
  let inFlight = false;
  let refreshAgain = false;
  let initialLoadAttempted = false;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  function selectedOrderId() {
    const input = document.querySelector('#paymentPanel input[name="order_id"]');
    if (input?.value) return String(input.value);
    const active = queue.querySelector('.cashier-order-link.active');
    if (!active) return '';
    try { return new URL(active.href).searchParams.get('order_id') || ''; }
    catch (_) { return ''; }
  }

  function orderMarkup(order, selectedId) {
    const waiting = order.state === 'waiting';
    const amount = Number(waiting ? order.net_sale : order.amount_due) || 0;
    const product = order.first_product
      ? ` · ${escapeHtml(order.first_product)}${Number(order.extra_lines || 0) > 0 ? ` +${Number(order.extra_lines)}` : ''}`
      : '';
    const href = `/bars/${barId}/cashier/workspace?order_id=${encodeURIComponent(order.id)}#paymentPanel`;
    const status = waiting ? 'À livrer' : (order.payment_status === 'PARTIAL' ? 'Partiel' : 'À payer');
    return `<a class="cashier-order-link ${String(order.id) === selectedId ? 'active' : ''}"
      href="${href}" data-live-order-id="${escapeHtml(order.id)}">
      <div class="cashier-order-copy">
        <strong class="cashier-human-order-title">${escapeHtml(order.display_name || 'COMPTOIR')}</strong>
        <small class="cashier-human-tech-ref">Réf. système : ${escapeHtml(order.reference)}</small>
        <small>${escapeHtml(order.server_name || 'Comptoir')} · ${escapeHtml(order.table || 'Sans table')}${product}</small>
      </div>
      <div class="cashier-order-money"><strong>${money.format(amount)} ${escapeHtml(order.currency || '')}</strong><small>${status}</small></div>
    </a>`;
  }

  function findLink(section, id) {
    return Array.from(section.querySelectorAll('.cashier-order-link')).find(link => {
      if (link.dataset.liveOrderId) return link.dataset.liveOrderId === id;
      try { return new URL(link.href).searchParams.get('order_id') === id; }
      catch (_) { return false; }
    });
  }

  function isEditing(node) {
    const active = document.activeElement;
    return !!(active && node && (node === active || node.contains(active))) ||
      !!node?.querySelector('form[aria-busy="true"]');
  }

  function reconcileSection(section, orders, selectedId, emptyText) {
    if (!section) return;
    const scrollX = window.scrollX, scrollY = window.scrollY;
    const sectionScroll = section.scrollTop;
    const wanted = new Set(orders.map(order => String(order.id)));
    const label = section.querySelector('.cashier-queue-label');
    let cursor = label?.nextElementSibling || section.firstElementChild;
    let changed = false;

    // Move or replace only the cards whose actual data changed.
    orders.forEach(order => {
      const id = String(order.id);
      let link = findLink(section, id);
      const other = section === waitingSection ? payableSection : waitingSection;
      if (!link && other) link = findLink(other, id);
      const oldActions = link?.nextElementSibling?.matches('[data-order-actions]') ? link.nextElementSibling : null;
      const html = orderMarkup(order, selectedId);
      const fingerprint = JSON.stringify([order.reference, order.display_name, order.state,
        order.payment_status, order.amount_due, order.net_sale, order.first_product,
        order.extra_lines, order.server_name, order.table, order.currency, order.actions_html]);
      const dirty = isEditing(link) || isEditing(oldActions);

      if (!link) {
        const template = document.createElement('template');
        template.innerHTML = html + (order.actions_html || '');
        link = template.content.querySelector('.cashier-order-link');
        const actions = template.content.querySelector('[data-order-actions]');
        link.dataset.liveFingerprint = fingerprint;
        section.insertBefore(link, cursor);
        if (actions) section.insertBefore(actions, cursor);
        changed = true;
      } else {
        link.dataset.liveOrderId = id;
        if (!dirty && link.dataset.liveFingerprint !== fingerprint) {
          const template = document.createElement('template');
          template.innerHTML = html + (order.actions_html || '');
          const nextLink = template.content.querySelector('.cashier-order-link');
          const nextActions = template.content.querySelector('[data-order-actions]');
          if (nextActions && oldActions) oldActions.replaceWith(nextActions);
          else if (nextActions) link.insertAdjacentElement('afterend', nextActions);
          else oldActions?.remove();
          link.replaceWith(nextLink);
          link = nextLink;
          changed = true;
        }
        if (!dirty) link.dataset.liveFingerprint = fingerprint;
        link.classList.toggle('active', id === selectedId);
        const actions = link.nextElementSibling?.matches('[data-order-actions]') ? link.nextElementSibling : null;
        if (!dirty && link !== cursor) {
          section.insertBefore(link, cursor);
          if (actions) link.insertAdjacentElement('afterend', actions);
          changed = true;
        }
      }
      const actions = link.nextElementSibling?.matches('[data-order-actions]') ? link.nextElementSibling : null;
      cursor = actions?.nextElementSibling || link.nextElementSibling;
    });

    for (const link of Array.from(section.querySelectorAll('.cashier-order-link'))) {
      const id = link.dataset.liveOrderId;
      if (!wanted.has(id) && !isEditing(link) && !isEditing(link.nextElementSibling)) {
        if (link.nextElementSibling?.matches('[data-order-actions]')) link.nextElementSibling.remove();
        link.remove();
        changed = true;
      }
    }
    section.querySelectorAll('[data-cashier-queue-empty]').forEach(el => el.remove());
    if (!orders.length && !section.querySelector('.cashier-order-link')) {
      const empty = document.createElement('div');
      empty.dataset.cashierQueueEmpty = '1';
      empty.className = 'p-3 text-secondary small';
      empty.textContent = emptyText;
      section.appendChild(empty);
    } else if (orders.length) {
      // Remove the initial server-rendered empty state, but not order cards.
      Array.from(section.children).forEach(el => {
        if (el !== label && !el.matches('.cashier-order-link, [data-order-actions]')) el.remove();
      });
    }
    if (changed) {
      section.scrollTop = sectionScroll;
      if (window.scrollX !== scrollX || window.scrollY !== scrollY) window.scrollTo(scrollX, scrollY);
    }
  }

  function syncStock(stock) {
    if (!productGrid) return;
    const activeCategory = document.querySelector('#cashierCategoryTabs .pos-category.active')?.dataset.category || 'all';
    const query = (productSearch?.value || '').trim().toLocaleLowerCase('fr');
    productGrid.querySelectorAll('.pos-product[data-product-id]').forEach(card => {
      const quantity = Number(stock?.[String(card.dataset.productId)] || 0);
      if (card.dataset.stock !== String(quantity)) {
        card.dataset.stock = String(quantity);
        const badge = card.querySelector('.pos-stock');
        if (badge) badge.textContent = quantity > 0 ? money.format(quantity) : 'Rupture';
        badge?.classList.toggle('danger', quantity <= 0);
      }
      const available = Number.isFinite(quantity) && quantity > 0;
      card.classList.toggle('out-of-stock', !available);
      card.disabled = !available;
      card.hidden = !(available && (activeCategory === 'all' || card.dataset.category === activeCategory) &&
        (!query || (card.dataset.name || '').toLocaleLowerCase('fr').includes(query)));
    });
  }

  function apply(payload) {
    if (!payload || (payload.mode !== 'CASHIER' && payload.mode !== 'ADMIN')) return;
    const orders = Array.isArray(payload.orders) ? payload.orders : [];
    const selectedId = selectedOrderId();
    const waiting = orders.filter(order => order.state === 'waiting');
    const payable = orders.filter(order => order.state === 'to_pay');
    reconcileSection(payableSection, payable, selectedId, 'Aucune commande à encaisser.');
    reconcileSection(waitingSection, waiting, selectedId, 'Aucune commande à livrer.');
    const waitingLabel = waitingSection?.querySelector('.cashier-queue-label span:last-child');
    const payableLabel = payableSection?.querySelector('.cashier-queue-label span:last-child');
    if (waitingLabel) waitingLabel.textContent = String(waiting.length);
    if (payableLabel) payableLabel.textContent = String(payable.length);
    if (waitingKpi) waitingKpi.textContent = String(payload.stats?.waiting ?? waiting.length);
    if (payableKpi) payableKpi.textContent = String(payload.stats?.to_pay ?? payable.length);
    if (dueKpi) dueKpi.textContent = money.format(Number(payload.stats?.due || 0));
    syncStock(payload.stock || {});

    const nextIds = new Set(orders.map(order => String(order.id)));
    if (!firstSuccessfulPoll && orders.some(order => !knownOrderIds.has(String(order.id)))) {
      liveBadge.className = 'badge text-bg-warning mt-1';
      liveBadge.textContent = '● Nouvelle commande reçue';
      window.setTimeout(() => {
        liveBadge.className = 'badge text-bg-success mt-1';
        liveBadge.textContent = '● Mise à jour auto';
      }, 4500);
    }
    knownOrderIds = nextIds;
    firstSuccessfulPoll = false;
  }

  async function poll() {
    if (inFlight) { refreshAgain = true; return; }
    if (document.hidden || window.navigator.onLine === false) return;
    inFlight = true;
    const sections = [payableSection, waitingSection].filter(Boolean);
    const skeleton = !initialLoadAttempted && !queue.querySelector('.cashier-order-link') && !!window.BarProSkeleton;
    initialLoadAttempted = true;
    if (skeleton) sections.forEach(section => {
      section.classList.add('bp-skeleton-loading');
      section.setAttribute('aria-busy', 'true');
      section.insertAdjacentHTML('beforeend', window.BarProSkeleton.queue());
    });
    try {
      const response = await fetch(endpoint, {
        credentials: 'same-origin', headers: { Accept: 'application/json' }, cache: 'no-store'
      });
      if (response.ok) apply(await response.json());
    } catch (_) {
      // Keep the existing queue usable on network failure.
    } finally {
      if (skeleton) sections.forEach(section => {
        section.querySelector('[data-skeleton-placeholder]')?.remove();
        section.classList.remove('bp-skeleton-loading');
        section.removeAttribute('aria-busy');
      });
      inFlight = false;
      if (refreshAgain) { refreshAgain = false; void poll(); }
    }
  }

  queue.addEventListener('click', event => {
    const link = event.target.closest('.cashier-order-link, [data-order-actions] a[href*="/cashier/workspace?"]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
        event.shiftKey || event.altKey) return;
    if (!window.CashierWorkspace?.selectOrder) return;
    event.preventDefault();
    void window.CashierWorkspace.selectOrder(link.href);
  });
  document.addEventListener('cashier:refresh', () => void poll());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void poll(); });
  window.addEventListener('online', () => void poll());
  window.CashierLive = Object.freeze({ refresh: poll });
  void poll();
  window.setInterval(poll, 4000);
})();
