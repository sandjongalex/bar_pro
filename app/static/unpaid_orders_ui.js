(function () {
  'use strict';

  const root = document.querySelector('[data-unpaid-monitor]');
  const grid = document.querySelector('[data-unpaid-grid]');
  if (!root || !grid) return;

  const baseLiveUrl = root.dataset.liveUrl || '';
  const role = root.dataset.role || '';
  const csrfToken = root.dataset.csrfToken || '';
  let staffFilter = root.dataset.staffFilter || 'all';
  const staffSelect = document.getElementById('staff');
  const badge = document.querySelector('[data-unpaid-live-badge]');
  const empty = document.querySelector('[data-unpaid-empty]');
  const countNode = document.querySelector('[data-unpaid-count]');
  const pendingNode = document.querySelector('[data-unpaid-pending-delivery]');
  const partialNode = document.querySelector('[data-unpaid-partial]');
  const dueNode = document.querySelector('[data-unpaid-due]');
  const mergeForm = document.querySelector('[data-merge-form]');
  const mergeSubmit = document.querySelector('[data-merge-submit]');
  const mergeWarning = document.querySelector('[data-unpaid-merge-warning]');
  const money = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });

  let knownIds = new Set(Array.from(grid.querySelectorAll('[data-unpaid-order-id]')).map((node) => String(node.dataset.unpaidOrderId)));
  let knownPendingIds = new Set();
  let firstPoll = true;
  let currentRequest = null;
  let requestGeneration = 0;
  let initialLoadAttempted = false;
  let mergeConflict = '';
  const etags = new Map();
  let renderedKey = null;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
  }

  function quantity(value) {
    const number = Number(value || 0);
    return Number.isInteger(number) ? String(number) : String(number).replace(/\.0+$/, '');
  }

  function selectedMergeIds() {
    return new Set(Array.from(grid.querySelectorAll('[data-merge-checkbox]:checked')).map((node) => String(node.value)));
  }

  function refreshMergeButton() {
    if (!mergeSubmit) return;
    const checked = Array.from(grid.querySelectorAll('[data-merge-checkbox]:checked'));
    const count = checked.length;
    const currencies = new Set(checked.map(input => input.closest('[data-unpaid-order-id]')?.dataset.unpaidCurrency));
    const mismatch = currencies.size > 1;
    mergeSubmit.disabled = count < 2 || mismatch;
    mergeSubmit.textContent = count >= 2 ? `Fusionner ${count} commandes` : 'Fusionner la sélection';
    if (mergeWarning) {
      const message = mismatch ? 'Les commandes sélectionnées n’utilisent pas la même devise.' : mergeConflict;
      mergeWarning.hidden = !message;
      mergeWarning.textContent = message;
    }
  }

  function ageLabel(value) {
    const time = Date.parse(value || '');
    if (!Number.isFinite(time)) return { text: 'Livrée récemment', aging: false };
    const minutes = Math.max(0, Math.floor((Date.now() - time) / 60000));
    if (minutes < 2) return { text: 'Livrée à l’instant', aging: false };
    if (minutes < 60) return { text: `Livrée depuis ${minutes} min`, aging: minutes >= 30 };
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return { text: `Livrée depuis ${hours} h${rest ? ` ${rest} min` : ''}`, aging: true };
  }

  function pendingDelivery(delivery) {
    const lines = Array.isArray(delivery.lines) ? delivery.lines : [];
    const lineHtml = lines.map((line) => `
      <div><span>${escapeHtml(quantity(line.quantity))} × ${escapeHtml(line.name)}</span><strong>${money.format(Number(line.total_amount || 0))}</strong></div>
    `).join('');
    const note = delivery.note ? `<p class="unpaid-pending-note">Note : ${escapeHtml(delivery.note)}</p>` : '';
    const confirmation = delivery.confirm_url
      ? `<form class="unpaid-delivery-form" data-bp-ajax="json" method="post" action="${escapeHtml(delivery.confirm_url)}">
          <input type="hidden" name="csrf_token" value="${escapeHtml(csrfToken)}">
          <input type="hidden" name="staff" value="${escapeHtml(staffFilter)}">
          <button class="btn btn-primary" type="submit">✓ Confirmer que j'ai livré</button>
        </form>`
      : '<span class="unpaid-delivery-wait">En attente de confirmation par la caissière</span>';

    return `
      <section class="unpaid-pending" data-pending-delivery-id="${escapeHtml(delivery.id)}">
        <div class="unpaid-pending-head">
          <div><strong>NOUVEL AJOUT DE LA SERVEUSE</strong><small>Sous-commande ${escapeHtml(delivery.sequence_no)} · à confirmer par la caisse</small></div>
          <span class="unpaid-pending-amount">${money.format(Number(delivery.total_amount || 0))} ${escapeHtml(delivery.currency || '')}</span>
        </div>
        <div class="unpaid-pending-lines">${lineHtml}</div>
        ${note}
        ${confirmation}
      </section>`;
  }

  function orderCard(order, selectedIds) {
    const age = ageLabel(order.posted_at);
    const partial = order.payment_status === 'PARTIAL';
    const blocked = Boolean(order.payment_blocked);
    const lines = Array.isArray(order.lines) ? order.lines : [];
    const pending = Array.isArray(order.pending_deliveries) ? order.pending_deliveries : [];
    const invoiceName = String(order.invoice_name || '').trim();
    const displayName = order.display_name || invoiceName || order.table || 'COMPTOIR';
    const displayLabel = invoiceName ? 'Facture / repère' : 'Table';
    const lineHtml = lines.map((line) => `
      <div><span>${escapeHtml(quantity(line.quantity))} × ${escapeHtml(line.name)}</span><strong>${money.format(Number(line.total_amount || 0))}</strong></div>
    `).join('');
    const pendingHtml = pending.length ? `<div class="unpaid-pending-list">${pending.map(pendingDelivery).join('')}</div>` : '';
    const note = order.notes ? `<p class="unpaid-note">${escapeHtml(order.notes)}</p>` : '';
    const status = blocked ? 'Livraison à confirmer' : (partial ? 'Partiellement payée' : 'Impayée');
    const action = blocked
      ? '<span class="unpaid-watch is-blocked">Confirmez d’abord la livraison avant d’encaisser</span>'
      : (order.action_url
        ? `<a class="btn btn-primary" href="${escapeHtml(order.action_url)}">Encaisser →</a>`
        : '<span class="unpaid-watch">À suivre avec la caisse</span>');
    const mergePick = role !== 'SERVER'
      ? `<label class="merge-pick"><input type="checkbox" form="mergeForm" name="order_ids" value="${escapeHtml(order.id)}" data-merge-checkbox ${blocked ? 'disabled' : ''} ${selectedIds.has(String(order.id)) && !blocked ? 'checked' : ''}> Ajouter à la fusion</label>`
      : '';

    return `
      <article class="unpaid-card ${partial ? 'is-partial' : ''} ${blocked ? 'is-pending-delivery' : ''} ${age.aging ? 'is-aging' : ''}" data-unpaid-order-id="${escapeHtml(order.id)}" data-unpaid-signature="${escapeHtml(order.signature || '')}" data-unpaid-currency="${escapeHtml(order.currency || '')}" data-posted-at="${escapeHtml(order.posted_at || '')}">
        <div class="unpaid-card-head">
          <div><small>${escapeHtml(displayLabel)}</small><strong>${escapeHtml(displayName)}</strong></div>
          <span class="unpaid-status">${escapeHtml(status)}</span>
        </div>
        ${mergePick}
        <div class="unpaid-card-meta"><strong>${escapeHtml(order.reference)}</strong><span>${escapeHtml(order.server_name || 'Comptoir')}</span><span data-unpaid-age>${escapeHtml(age.text)}</span></div>
        <div class="unpaid-lines">${lineHtml}</div>
        ${pendingHtml}
        ${note}
        <div class="unpaid-card-foot">
          <div><small>Reste à encaisser</small><strong>${money.format(Number(order.amount_due || 0))} ${escapeHtml(order.currency || '')}</strong></div>
          ${action}
        </div>
      </article>`;
  }

  function showNewNotice(kind) {
    if (!badge) return;
    badge.classList.add('is-alert');
    if (kind === 'delivery') {
      badge.textContent = role === 'CASHIER' ? '● Nouvel ajout à confirmer' : '● Nouvel ajout en attente de caisse';
    } else {
      badge.textContent = role === 'SERVER' ? '● Nouvelle commande à suivre' : '● Nouvelle commande impayée';
    }
    window.setTimeout(() => {
      badge.classList.remove('is-alert');
      badge.textContent = '● Mise à jour auto';
    }, 4500);
  }

  function apply(payload) {
    const orders = Array.isArray(payload?.orders) ? payload.orders : [];
    const selectedIds = selectedMergeIds();
    const nextPendingIds = new Set();
    orders.forEach((order) => {
      (Array.isArray(order.pending_deliveries) ? order.pending_deliveries : []).forEach((delivery) => {
        nextPendingIds.add(String(delivery.id));
      });
    });

    // Reconcile by stable order ID and server-provided signature. Unchanged cards,
    // focused controls and checked merge boxes keep their original DOM identity.
    const scrollX = window.scrollX, scrollY = window.scrollY;
    const focused = document.activeElement;
    const focusedCard = focused?.closest?.('[data-unpaid-order-id]');
    const focusedId = focusedCard?.dataset.unpaidOrderId;
    const existing = new Map(Array.from(grid.querySelectorAll('[data-unpaid-order-id]'))
      .map(node => [String(node.dataset.unpaidOrderId), node]));
    const nextIds = new Set(orders.map(order => String(order.id)));
    const eligible = new Map(orders.map(order => [String(order.id), order]));
    const lost = Array.from(selectedIds).filter(id => !eligible.has(id) || eligible.get(id).payment_blocked);
    if (lost.length) {
      mergeConflict = 'Sélection modifiée : ' + lost.length +
        ' commande(s) ont été payées, retirées ou ne sont plus éligibles à la fusion.';
    }
    if (grid.querySelector('[data-skeleton-placeholder]')) {
      grid.replaceChildren();
      grid.removeAttribute('aria-busy');
    }

    orders.forEach((order, index) => {
      const id = String(order.id);
      let node = existing.get(id);
      if (!node || node.dataset.unpaidSignature !== order.signature) {
        const wrapper = document.createElement('div');
        wrapper.innerHTML = orderCard(order, selectedIds);
        const replacement = wrapper.firstElementChild;
        if (!replacement) throw new Error('UNPAID_CARD_RENDER_FAILED');
        if (node) node.replaceWith(replacement);
        node = replacement;
      }
      if (grid.children[index] !== node) grid.insertBefore(node, grid.children[index] || null);
    });
    existing.forEach((node, id) => {
      if (!nextIds.has(id)) node.remove();
    });
    refreshMergeButton();
    if (empty) empty.hidden = orders.length !== 0;
    if (countNode) countNode.textContent = String(payload?.stats?.count ?? orders.length);
    if (pendingNode) pendingNode.textContent = String(payload?.stats?.pending_delivery ?? nextPendingIds.size);
    if (partialNode) partialNode.textContent = String(payload?.stats?.partial ?? 0);
    if (dueNode) dueNode.textContent = money.format(Number(payload?.stats?.due || 0));

    if (focusedId && !focused?.isConnected) {
      const replacement = Array.from(grid.querySelectorAll('[data-unpaid-order-id]'))
        .find(node => node.dataset.unpaidOrderId === focusedId);
      const control = focused?.matches?.('[data-merge-checkbox]') ? '[data-merge-checkbox]' :
        focused?.matches?.('a') ? '.unpaid-card-foot a' : null;
      if (control) replacement?.querySelector(control)?.focus({ preventScroll: true });
    }
    if (window.scrollX !== scrollX || window.scrollY !== scrollY) window.scrollTo(scrollX, scrollY);
    if (!firstPoll && Array.from(nextPendingIds).some(id => !knownPendingIds.has(id))) {
      showNewNotice('delivery');
    } else if (!firstPoll && orders.some(order => !knownIds.has(String(order.id)))) {
      showNewNotice('order');
    }
    knownIds = nextIds;
    knownPendingIds = nextPendingIds;
    firstPoll = false;
  }

  function refreshAges() {
    grid.querySelectorAll('[data-unpaid-order-id]').forEach((card) => {
      const age = ageLabel(card.dataset.postedAt || '');
      const node = card.querySelector('[data-unpaid-age]');
      if (node) node.textContent = age.text;
      card.classList.toggle('is-aging', age.aging);
    });
  }

  async function poll(force = false) {
    if (!baseLiveUrl || document.hidden || navigator.onLine === false) return;
    if (currentRequest) {
      if (!force) return;
      currentRequest.abort();
    }
    const controller = new AbortController();
    currentRequest = controller;
    const generation = ++requestGeneration;
    const url = new URL(baseLiveUrl, window.location.href);
    url.searchParams.set('staff', staffFilter);
    const key = url.href;
    const headers = { Accept: 'application/json' };
    if (renderedKey === key && etags.has(key)) headers['If-None-Match'] = etags.get(key);

    // Skeleton only for the first load when the server has no rendered cards.
    const showSkeleton = !initialLoadAttempted && !grid.querySelector('[data-unpaid-order-id]')
      && !!window.BarProSkeleton;
    initialLoadAttempted = true;
    if (showSkeleton) {
      grid.innerHTML = window.BarProSkeleton.unpaid();
      grid.setAttribute('aria-busy', 'true');
      if (empty) empty.hidden = true;
    }
    try {
      const response = await fetch(key, {
        credentials: 'same-origin', headers, cache: 'no-store', signal: controller.signal,
      });
      if (generation !== requestGeneration) return;
      if (response.status === 304) {
        if (badge && badge.textContent === '● Reconnexion…') badge.textContent = '● Mise à jour auto';
        return;
      }
      if (!response.ok) throw new Error('UNPAID_POLL_FAILED');
      const payload = await response.json();
      if (generation !== requestGeneration) return;
      apply(payload);
      renderedKey = key;
      const tag = response.headers.get('ETag');
      if (tag) etags.set(key, tag);
      if (badge && badge.textContent === '● Reconnexion…') badge.textContent = '● Mise à jour auto';
    } catch (error) {
      if (generation === requestGeneration && error.name !== 'AbortError' && badge) {
        badge.textContent = '● Reconnexion…';
      }
    } finally {
      if (currentRequest === controller) currentRequest = null;
      if (showSkeleton && generation === requestGeneration) {
        if (grid.querySelector('[data-skeleton-placeholder]')) {
          grid.innerHTML = '';
          if (empty) empty.hidden = false;
        }
        grid.removeAttribute('aria-busy');
      }
    }
  }

  grid.addEventListener('barpro:async-success', event => {
    if (!event.target?.matches?.('.unpaid-delivery-form')) return;
    void poll(true);
  });
  grid.addEventListener('change', (event) => {
    if (event.target && event.target.matches('[data-merge-checkbox]')) {
      mergeConflict = '';
      refreshMergeButton();
    }
  });
  if (mergeForm) {
    mergeForm.addEventListener('submit', (event) => {
      refreshMergeButton();
      if (mergeSubmit.disabled) event.preventDefault();
    });
  }

  if (staffSelect) {
    staffSelect.addEventListener('change', () => {
      staffFilter = staffSelect.value;
      mergeConflict = '';
      const url = new URL(window.location.href);
      url.searchParams.set('staff', staffFilter);
      window.history.pushState({}, '', url.href);
      void poll(true);
    });
    staffSelect.form?.addEventListener('submit', event => {
      event.preventDefault();
      staffSelect.dispatchEvent(new Event('change', { bubbles: true }));
    });
    staffSelect.form?.querySelector('a[href]')?.addEventListener('click', event => {
      event.preventDefault();
      staffSelect.value = 'all';
      staffSelect.dispatchEvent(new Event('change', { bubbles: true }));
    });
    window.addEventListener('popstate', () => {
      const value = new URL(window.location.href).searchParams.get('staff') || 'all';
      staffFilter = Array.from(staffSelect.options).some(option => option.value === value) ? value : 'all';
      staffSelect.value = staffFilter;
      void poll(true);
    });
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) void poll(true);
    else currentRequest?.abort();
  });
  window.addEventListener('online', () => { void poll(true); });
  window.setInterval(refreshAges, 60000);
  window.setInterval(() => { void poll(); }, 4000);
  refreshAges();
  refreshMergeButton();
  void poll();
})();