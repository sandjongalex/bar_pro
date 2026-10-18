/* BAR_PRO suborders: live, role-scoped snapshots and progressive AJAX mutations.
   The Flask services alone decide stock, totals, delivery and validation. */
(() => {
  'use strict';
  const api = window.BarProAsync;
  if (!api) return; // SSR forms remain the no-JS fallback.
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
  const money = value => new Intl.NumberFormat('fr-FR', {maximumFractionDigits: 0})
    .format(Number(value || 0));
  const quantity = value => {
    const n = Number(value || 0);
    return Number.isFinite(n) ? String(n) : esc(value);
  };
  const rows = item => (item.lines || []).map(line =>
    `<div class="cash-sub-line"><span><strong>${esc(quantity(line.quantity))}×</strong> ${esc(line.name)}</span><span>${money(line.total)}</span></div>`
  ).join('');

  // Keep unchanged cards, DOM listeners, focus, and scroll when reconciling live data.
  function reconcile(container, items, attribute, render) {
    if (!container) return;
    const previousX = window.scrollX, previousY = window.scrollY;
    const focused = document.activeElement;
    const existing = new Map(Array.from(container.children)
      .filter(node => node.hasAttribute(attribute))
      .map(node => [node.getAttribute(attribute), node]));
    const ids = new Set();
    (items || []).forEach((item, index) => {
      const id = String(item.id);
      ids.add(id);
      const signature = JSON.stringify(item);
      let card = existing.get(id);
      if (!card || card.dataset.liveSignature !== signature) {
        if (card && card.contains(focused) && card.getAttribute('aria-busy') === 'true') return;
        const holder = document.createElement('div');
        holder.innerHTML = render(item);
        const replacement = holder.firstElementChild;
        if (!replacement) return;
        replacement.dataset.liveSignature = signature;
        if (card) card.replaceWith(replacement);
        card = replacement;
      }
      if (container.children[index] !== card) container.insertBefore(card, container.children[index] || null);
    });
    existing.forEach((card, id) => {
      if (!ids.has(id) && card.getAttribute('aria-busy') !== 'true') card.remove();
    });
    if (window.scrollX !== previousX || window.scrollY !== previousY) {
      window.scrollTo(previousX, previousY);
    }
  }

  // ETag conditional reads avoid downloading or re-rendering unchanged snapshots.
  function live(url, apply, interval, statusNode) {
    if (!url) return { refresh: async () => {} };
    let etag = '', controller = null, generation = 0;
    async function refresh(force = false) {
      if (document.hidden || navigator.onLine === false) return;
      if (controller) {
        if (!force) return;
        controller.abort();
      }
      const current = new AbortController();
      controller = current;
      const seq = ++generation;
      try {
        const headers = {Accept: 'application/json'};
        if (etag) headers['If-None-Match'] = etag;
        const response = await fetch(url, {
          credentials: 'same-origin', cache: 'no-store', headers, signal: current.signal
        });
        if (seq !== generation) return;
        if (response.status === 304) return;
        if (!response.ok || !response.headers.get('Content-Type')?.includes('application/json')) {
          throw new Error('SUBORDER_SNAPSHOT_UNAVAILABLE');
        }
        const payload = await response.json();
        if (seq !== generation) return;
        apply(payload);
        etag = response.headers.get('ETag') || '';
        if (statusNode) {
          statusNode.textContent = 'À jour';
          statusNode.classList.remove('text-danger');
        }
      } catch (error) {
        if (error.name !== 'AbortError' && statusNode) {
          statusNode.textContent = 'Hors ligne · nouvelle tentative';
          statusNode.classList.add('text-danger');
        }
      } finally {
        if (controller === current) controller = null;
      }
    }
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) controller?.abort();
      else void refresh(true);
    });
    window.addEventListener('online', () => void refresh(true));
    window.setInterval(() => void refresh(), interval);
    void refresh();
    return { refresh };
  }

  const builder = document.querySelector('[data-suborder-builder]');
  const follow = document.querySelector('[data-suborder-follow]');
  if (builder && follow) {
    const followList = follow.querySelector('[data-suborder-follow-items]');
    const followEmpty = follow.querySelector('[data-suborder-follow-empty]');
    const total = document.querySelector('.suborder-context article:nth-child(3) strong');
    const currency = document.querySelector('.suborder-context article:nth-child(3) strong')
      ?.textContent.trim().split(' ').slice(-1).join(' ') || '';
    const track = live(follow.dataset.liveUrl, payload => {
      reconcile(followList, payload.suborders, 'data-follow-suborder-id', item => {
        const label = item.delivery_status === 'PENDING'
          ? 'À livrer par la caisse' : item.status === 'PENDING_VALIDATION'
            ? 'Déjà servie · à valider par la serveuse' : 'Livrée · validée';
        return `<article class="border rounded-3 p-2 my-2" data-follow-suborder-id="${esc(item.id)}">
          <div class="d-flex justify-content-between gap-2"><strong>Sous-commande ${esc(item.sequence_no)}</strong>
          <strong>${money(item.total)} ${esc(item.currency)}</strong></div>
          <small class="text-secondary">${esc(label)}</small>
          <div class="small mt-1">${(item.lines || []).map(line =>
            `<div>${esc(quantity(line.quantity))} × ${esc(line.name)}</div>`).join('')}</div>
        </article>`;
      });
      if (followEmpty) followEmpty.hidden = (payload.suborders || []).length > 0;
      if (total && typeof payload.order_total === 'string') {
        total.textContent = money(payload.order_total) + ' ' + currency;
      }
    }, 5000, follow.querySelector('[data-suborder-live-status]'));

    let saving = false;
    builder.addEventListener('submit', event => {
      if (!window.BarProSuborderCart?.hasItems?.()) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
      if (saving) return;
      saving = true;
      const mobile = document.getElementById('suborderMobileSubmit');
      if (mobile) mobile.disabled = true;
      void (async () => {
        try {
          const result = await api.submit(builder, event.submitter);
          if (result?.skipped) return;
          if (!result?.success || !result.data?.id) {
            api.notify('Réponse incomplète. Vérifiez le suivi avant un nouvel envoi.', 'error');
            return;
          }
          window.BarProSuborderCart?.reset?.(); // Only after confirmed DB commit.
          await track.refresh(true);
        } catch (error) {
          // Keep selected lines and the note on all errors, including network ambiguity.
          if (['NETWORK', 'TIMEOUT', 'FORMAT', 'REDIRECT'].includes(error.code)) {
            api.notify('Résultat incertain : vérifiez le suivi avant de renvoyer cet ajout.', 'error');
            await track.refresh(true);
          }
        } finally {
          saving = false;
          if (mobile) mobile.disabled = !window.BarProSuborderCart?.hasItems?.();
        }
      })();
    }, true);
  }

  const cashier = document.querySelector('[data-cash-suborders]');
  if (cashier) {
    const pendingList = cashier.querySelector('[data-cash-sub-pending]');
    const orderList = cashier.querySelector('[data-cash-sub-orders]');
    const emptyPending = cashier.querySelector('[data-cash-sub-pending-empty]');
    const emptyOrders = cashier.querySelector('[data-cash-sub-orders-empty]');
    const counter = cashier.querySelector('[data-cash-sub-count]');
    const badge = cashier.querySelector('[data-cash-sub-badge]');
    const csrf = cashier.dataset.csrfToken;
    const endpoint = window.location.pathname;
    const busy = new Set();

    const poll = live(cashier.dataset.liveUrl, payload => {
      const pending = payload.pending || [];
      const orders = payload.active_orders || [];
      reconcile(pendingList, pending, 'data-suborder-id', item => `
        <article class="cash-sub-card" data-suborder-id="${esc(item.id)}">
          <div class="cash-sub-card-head">
            <div><strong class="cash-sub-card-title">${esc(item.display_name)} · Sous-commande ${esc(item.sequence_no)}</strong>
              <small class="cash-sub-meta">${esc(item.server_name)}</small>
              <small class="cash-sub-system-ref">Réf. système : ${esc(item.reference)}</small>
              <span class="cash-sub-status">Validée par la serveuse · à livrer</span></div>
            <div class="cash-sub-amount">${money(item.total)} ${esc(item.currency)}</div>
          </div>
          <div class="cash-sub-lines">${rows(item)}</div>
          ${item.note ? `<p class="cash-sub-note">Note : ${esc(item.note)}</p>` : ''}
          <form method="post" action="${esc(endpoint)}">
            <input type="hidden" name="csrf_token" value="${esc(csrf)}">
            <input type="hidden" name="order_id" value="${esc(item.order_id)}">
            <input type="hidden" name="suborder_id" value="${esc(item.id)}">
            <button class="btn btn-primary w-100 cash-sub-deliver" type="submit">✓ Livrer cette sous-commande</button>
          </form>
        </article>`);
      reconcile(orderList, orders, 'data-cash-sub-order-id', item => `
        <a class="cash-sub-order" data-cash-sub-order-id="${esc(item.id)}" href="${esc(item.add_url)}">
          <div><strong>${esc(item.name)}</strong><small>Réf. système : ${esc(item.reference)}</small>
          <small>${money(item.total)} ${esc(item.currency)} · ${item.payment_status === 'PARTIAL' ? 'Partiel' : 'Non payé'}</small></div>
          <span class="cash-sub-add">＋ Ajouter</span>
        </a>`);
      if (emptyPending) emptyPending.hidden = pending.length > 0;
      if (emptyOrders) emptyOrders.hidden = orders.length > 0;
      if (counter) counter.textContent = String(pending.length);
      if (badge) badge.textContent = pending.length + ' à livrer';
    }, 4000);

    cashier.addEventListener('submit', event => {
      const form = event.target.closest?.('[data-cash-sub-pending] form');
      if (!form) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const card = form.closest('[data-suborder-id]');
      const id = card?.dataset.suborderId;
      if (!id || busy.has(id)) return;
      busy.add(id);
      card.setAttribute('aria-busy', 'true');
      void (async () => {
        try {
          const result = await api.submit(form, event.submitter);
          if (result?.success && result.data?.delivery_status === 'DELIVERED') {
            card.remove();
            await poll.refresh(true);
          }
        } catch (error) {
          await poll.refresh(true);
        } finally {
          busy.delete(id);
          if (card.isConnected) card.removeAttribute('aria-busy');
        }
      })();
    }, true);
  }

  const server = document.querySelector('[data-server-pending-root]');
  if (server) {
    const list = server.querySelector('[data-server-pending-items]');
    const count = server.querySelector('[data-server-pending-count]');
    const empty = server.querySelector('[data-server-pending-empty]');
    const csrf = server.dataset.csrfToken;
    const action = window.location.pathname; // existing orders/new POST validates and audits
    const poll = live(server.dataset.liveUrl, payload => {
      const pending = payload.pending || [];
      reconcile(list, pending, 'data-pending-suborder', item => `
        <article class="server-pending-card" data-pending-suborder="${esc(item.id)}">
          <div class="server-pending-top">
            <div><strong>${esc(item.display_name)} · Sous-commande ${esc(item.sequence_no)}</strong>
              <div class="server-pending-system-ref">Réf. système : ${esc(item.reference)}</div>
              <span class="server-pending-status">Déjà servie · validation requise</span></div>
            <div class="text-end"><strong>${money(item.total)} ${esc(item.currency)}</strong></div>
          </div>
          <div class="server-pending-lines">${(item.lines || []).map(line =>
            `<div class="server-pending-line"><span><strong>${esc(quantity(line.quantity))}×</strong> ${esc(line.name)}</span><span>${money(line.total)}</span></div>`
          ).join('')}</div>
          ${item.note ? `<p class="small text-secondary mt-2 mb-0">Note caisse : ${esc(item.note)}</p>` : ''}
          <form method="post" class="mt-3" action="${esc(action)}">
            <input type="hidden" name="csrf_token" value="${esc(csrf)}">
            <input type="hidden" name="action" value="validate_suborder">
            <input type="hidden" name="order_id" value="${esc(item.order_id)}">
            <input type="hidden" name="suborder_id" value="${esc(item.id)}">
            <button class="btn btn-success w-100" type="submit">✓ Valider cet ajout</button>
          </form>
        </article>`);
      if (count) count.textContent = String(pending.length);
      if (empty) empty.hidden = pending.length > 0;
    }, 5000);

    document.addEventListener('barpro:async-success', event => {
      if (event.target?.closest?.('[data-server-pending-items]') &&
          event.target.querySelector?.('[name="action"]')?.value === 'validate_suborder') {
        void poll.refresh(true);
      }
    });
  }
})();
