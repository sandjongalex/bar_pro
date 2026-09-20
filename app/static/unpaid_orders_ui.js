(function () {
  'use strict';

  const root = document.querySelector('[data-unpaid-monitor]');
  const grid = document.querySelector('[data-unpaid-grid]');
  if (!root || !grid) return;

  const liveUrl = root.dataset.liveUrl || '';
  const role = root.dataset.role || '';
  const badge = document.querySelector('[data-unpaid-live-badge]');
  const empty = document.querySelector('[data-unpaid-empty]');
  const countNode = document.querySelector('[data-unpaid-count]');
  const partialNode = document.querySelector('[data-unpaid-partial]');
  const dueNode = document.querySelector('[data-unpaid-due]');
  const money = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });

  let knownIds = new Set(Array.from(grid.querySelectorAll('[data-unpaid-order-id]')).map((node) => String(node.dataset.unpaidOrderId)));
  let firstPoll = true;
  let inFlight = false;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
  }

  function quantity(value) {
    const number = Number(value || 0);
    return Number.isInteger(number) ? String(number) : String(number).replace(/\.0+$/, '');
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

  function orderCard(order) {
    const age = ageLabel(order.posted_at);
    const partial = order.payment_status === 'PARTIAL';
    const lines = Array.isArray(order.lines) ? order.lines : [];
    const lineHtml = lines.map((line) => `
      <div><span>${escapeHtml(quantity(line.quantity))} × ${escapeHtml(line.name)}</span><strong>${money.format(Number(line.total_amount || 0))}</strong></div>
    `).join('');
    const note = order.notes ? `<p class="unpaid-note">${escapeHtml(order.notes)}</p>` : '';
    const action = order.action_url
      ? `<a class="btn btn-primary" href="${escapeHtml(order.action_url)}">Encaisser →</a>`
      : '<span class="unpaid-watch">À suivre avec la caisse</span>';

    return `
      <article class="unpaid-card ${partial ? 'is-partial' : ''} ${age.aging ? 'is-aging' : ''}" data-unpaid-order-id="${escapeHtml(order.id)}" data-posted-at="${escapeHtml(order.posted_at || '')}">
        <div class="unpaid-card-head">
          <div><small>Table</small><strong>${escapeHtml(order.table || 'Sans table')}</strong></div>
          <span class="unpaid-status">${partial ? 'Partiellement payée' : 'Impayée'}</span>
        </div>
        <div class="unpaid-card-meta"><strong>${escapeHtml(order.reference)}</strong><span>${escapeHtml(order.server_name || 'Comptoir')}</span><span data-unpaid-age>${escapeHtml(age.text)}</span></div>
        <div class="unpaid-lines">${lineHtml}</div>
        ${note}
        <div class="unpaid-card-foot">
          <div><small>Reste à encaisser</small><strong>${money.format(Number(order.amount_due || 0))} ${escapeHtml(order.currency || '')}</strong></div>
          ${action}
        </div>
      </article>`;
  }

  function showNewNotice() {
    if (!badge) return;
    badge.classList.add('is-alert');
    badge.textContent = role === 'SERVER' ? '● Nouvelle commande à suivre' : '● Nouvelle commande impayée';
    window.setTimeout(() => {
      badge.classList.remove('is-alert');
      badge.textContent = '● Mise à jour auto';
    }, 4500);
  }

  function apply(payload) {
    const orders = Array.isArray(payload?.orders) ? payload.orders : [];
    grid.innerHTML = orders.map(orderCard).join('');
    if (empty) empty.hidden = orders.length !== 0;
    if (countNode) countNode.textContent = String(payload?.stats?.count ?? orders.length);
    if (partialNode) partialNode.textContent = String(payload?.stats?.partial ?? 0);
    if (dueNode) dueNode.textContent = money.format(Number(payload?.stats?.due || 0));

    const nextIds = new Set(orders.map((order) => String(order.id)));
    if (!firstPoll && orders.some((order) => !knownIds.has(String(order.id)))) showNewNotice();
    knownIds = nextIds;
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

  async function poll() {
    if (!liveUrl || inFlight || document.hidden) return;
    inFlight = true;
    try {
      const response = await fetch(liveUrl, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) return;
      apply(await response.json());
    } catch (error) {
      if (badge) badge.textContent = '● Reconnexion…';
    } finally {
      inFlight = false;
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) poll();
  });
  window.setInterval(refreshAges, 60000);
  window.setInterval(poll, 4000);
  refreshAges();
  poll();
})();
