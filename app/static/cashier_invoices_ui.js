/* Cashier invoices: conditional fragment snapshots and stable keyed cards. */
(function () {
  'use strict';
  const form = document.getElementById('invoiceFilters');
  const results = document.getElementById('invoiceResults');
  const stats = document.getElementById('invoiceStats');
  const status = document.getElementById('invoiceAutoRefresh');
  const statusText = document.getElementById('invoiceAutoRefreshText');
  if (!form || !results || !stats || !status || !statusText) return;

  const refreshEveryMs = 10000;
  const etags = new Map();
  let renderedKey = null;
  let controller = null;
  let generation = 0;
  let actionInProgress = false;

  function markStatus(state, label) {
    status.classList.toggle('syncing', state === 'syncing');
    status.classList.toggle('error', state === 'error');
    statusText.textContent = label;
  }

  function filterUrl() {
    const url = new URL(window.location.href);
    url.searchParams.delete('snapshot');
    for (const name of ['delivery', 'payment', 'origin']) {
      url.searchParams.set(name, form.elements[name].value);
    }
    return url;
  }

  function restoreFilters() {
    const url = new URL(window.location.href);
    for (const name of ['delivery', 'payment', 'origin']) {
      const value = url.searchParams.get(name);
      if (value && Array.from(form.elements[name].options).some(option => option.value === value)) {
        form.elements[name].value = value;
      } else {
        form.elements[name].value = name === 'payment' ? 'open' : 'all';
      }
    }
  }

  function updateStats(next) {
    const values = [next.waiting, next.delivered, next.paid, next.visible];
    stats.querySelectorAll('.invoice-stat strong').forEach((node, index) => {
      const value = String(values[index] ?? 0);
      if (node.textContent !== value) node.textContent = value;
    });
  }

  function patchCards(html) {
    const nextDocument = new DOMParser().parseFromString(html, 'text/html');
    const incoming = nextDocument.getElementById('invoiceResults');
    if (!incoming) throw new Error('INVOICE_REFRESH_INVALID_FRAGMENT');
    const oldGrid = results.querySelector('.invoice-grid');
    const newGrid = incoming.querySelector('.invoice-grid');
    const scrollX = window.scrollX, scrollY = window.scrollY;
    const focused = document.activeElement;
    const focusedCard = focused?.closest?.('[data-invoice-id]');
    const focusedId = focusedCard?.dataset.invoiceId;
    const focusedAction = focused?.matches?.('button') ? 'button' : focused?.matches?.('a') ? 'a' : null;

    if (oldGrid && newGrid) {
      const current = new Map(Array.from(oldGrid.querySelectorAll(':scope > [data-invoice-id]'))
        .map(node => [node.dataset.invoiceId, node]));
      const seen = new Set();
      Array.from(newGrid.querySelectorAll(':scope > [data-invoice-id]')).forEach((candidate, index) => {
        const id = candidate.dataset.invoiceId;
        if (seen.has(id)) throw new Error('INVOICE_DUPLICATE_ID');
        seen.add(id);
        let node = current.get(id);
        if (!node) {
          node = document.importNode(candidate, true);
        } else if (node.outerHTML !== candidate.outerHTML &&
                   !node.contains(focused) &&
                   !node.querySelector('form[aria-busy="true"], .modal.show')) {
          const replacement = document.importNode(candidate, true);
          node.replaceWith(replacement);
          node = replacement;
        }
        // An active payment is never relocated by background reconciliation.
        const protectedNode = node.contains(focused) ||
          Boolean(node.querySelector('form[aria-busy="true"], .modal.show'));
        if (!protectedNode && oldGrid.children[index] !== node) {
          oldGrid.insertBefore(node, oldGrid.children[index] || null);
        }
      });
      current.forEach((node, id) => {
        if (!seen.has(id) && !node.contains(focused) &&
            !node.querySelector('form[aria-busy="true"], .modal.show')) node.remove();
      });
      const count = incoming.querySelector('.invoice-section-title span');
      const currentCount = results.querySelector('.invoice-section-title span');
      if (count && currentCount && count.textContent !== currentCount.textContent) {
        currentCount.textContent = count.textContent;
      }
    } else if (!results.contains(focused) && !results.querySelector('form[aria-busy="true"]') &&
               results.innerHTML !== incoming.innerHTML) {
      // Empty/nonempty transition: the grid does not exist in one of the snapshots.
      results.replaceChildren(...Array.from(incoming.childNodes).map(node => document.importNode(node, true)));
    }

    if (focusedId && focusedAction && !focused?.isConnected) {
      const replacement = Array.from(results.querySelectorAll('[data-invoice-id]'))
        .find(node => node.dataset.invoiceId === focusedId);
      replacement?.querySelector('.invoice-actions ' + focusedAction)?.focus({ preventScroll: true });
    }
    if (window.scrollX !== scrollX || window.scrollY !== scrollY) window.scrollTo(scrollX, scrollY);
  }

  async function refreshInvoices(force = false) {
    if (actionInProgress || document.visibilityState !== 'visible' || navigator.onLine === false) return;
    if (controller) {
      if (!force) return;
      controller.abort();
    }
    const requestId = ++generation;
    const current = new AbortController();
    controller = current;
    const url = filterUrl();
    const snapshot = new URL(url.href);
    snapshot.searchParams.set('snapshot', '1');
    const key = snapshot.href;
    const headers = { Accept: 'application/json', 'X-Requested-With': 'invoice-auto-refresh' };
    if (renderedKey === key && etags.has(key)) headers['If-None-Match'] = etags.get(key);
    markStatus('syncing', 'Synchronisation…');
    try {
      const response = await fetch(snapshot.href, {
        method: 'GET', credentials: 'same-origin', cache: 'no-store',
        headers, signal: current.signal,
      });
      if (requestId !== generation) return;
      if (response.status === 304) {
        markStatus('ok', 'À jour · aucune modification');
        return;
      }
      if (!response.ok || !response.headers.get('Content-Type')?.includes('application/json')) {
        throw new Error('INVOICE_REFRESH_FAILED');
      }
      const payload = await response.json();
      if (requestId !== generation) return;
      if (typeof payload.html !== 'string' || !payload.stats) throw new Error('INVOICE_REFRESH_INVALID_PAYLOAD');
      patchCards(payload.html);
      updateStats(payload.stats);
      renderedKey = key;
      const tag = response.headers.get('ETag');
      if (tag) etags.set(key, tag);
      const now = new Intl.DateTimeFormat('fr-FR', {hour:'2-digit', minute:'2-digit'}).format(new Date());
      markStatus('ok', 'À jour · ' + now);
    } catch (error) {
      if (requestId === generation && error.name !== 'AbortError') {
        markStatus('error', 'Mise à jour différée · nouvelle tentative automatique');
      }
    } finally {
      if (controller === current) controller = null;
    }
  }

  function navigateFilters() {
    if (actionInProgress) return;
    const url = filterUrl();
    window.history.pushState({}, '', url.href);
    void refreshInvoices(true);
  }
  form.addEventListener('change', navigateFilters);
  form.addEventListener('submit', event => {
    event.preventDefault();
    navigateFilters();
  });
  const reset = form.querySelector('.invoice-filter-reset');
  reset?.addEventListener('click', event => {
    event.preventDefault();
    form.elements.delivery.value = 'all';
    form.elements.payment.value = 'open';
    form.elements.origin.value = 'all';
    navigateFilters();
  });
  window.addEventListener('popstate', () => {
    restoreFilters();
    void refreshInvoices(true);
  });

  document.addEventListener('submit', event => {
    const paymentForm = event.target;
    if (!(paymentForm instanceof HTMLFormElement) || !paymentForm.matches('#invoiceResults form[data-invoice-pay]')) return;
    if (actionInProgress) { event.preventDefault(); return; }
    if (!window.confirm('Confirmer le paiement espèces de ' + paymentForm.dataset.confirmAmount + ' ?')) {
      event.preventDefault();
      return;
    }
    const key = paymentForm.elements.idempotency_key;
    if (key && !key.value) {
      key.value = window.crypto?.randomUUID?.() ||
        ('invoice-' + Date.now() + '-' + Math.random().toString(36).slice(2));
    }
    actionInProgress = true;
  }, true);

  document.addEventListener('barpro:async-success', event => {
    if (!event.target?.matches?.('#invoiceResults form[data-invoice-pay]')) return;
    actionInProgress = false;
    // Payment was committed; update balances and remove cards excluded by filters.
    etags.clear();
    void refreshInvoices(true);
  });
  document.addEventListener('barpro:async-error', event => {
    if (!event.target?.matches?.('#invoiceResults form[data-invoice-pay]')) return;
    actionInProgress = false;
    // Never retry a possibly committed payment automatically.
  });

  window.refreshCashierInvoices = () => refreshInvoices(true);
  window.setInterval(() => { void refreshInvoices(); }, refreshEveryMs);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void refreshInvoices(true);
    else if (controller) controller.abort();
  });
  window.addEventListener('online', () => { void refreshInvoices(true); });
  restoreFilters();
})();
