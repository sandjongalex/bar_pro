/* Dedicated waitress order history. SSR-first, no full-page polling or cart coupling. */
(() => {
  'use strict';
  const root = document.querySelector('[data-server-mine]');
  const results = document.getElementById('serverMineResults');
  if (!root || !results) return;

  const status = root.querySelector('[data-server-mine-status]');
  const filters = Array.from(root.querySelectorAll('[data-server-mine-filter]'));
  const refreshButton = root.querySelector('[data-server-mine-refresh]');
  const etags = new Map();
  const allowed = new Set(['active', 'waiting', 'to_pay', 'paid', 'cancelled', 'all']);
  let renderedKey = null;
  let controller = null;
  let generation = 0;
  let mutationInProgress = false;

  function currentUrl() {
    const url = new URL(window.location.href);
    const filter = url.searchParams.get('filter') || 'active';
    if (!allowed.has(filter)) url.searchParams.set('filter', 'active');
    const page = Number(url.searchParams.get('page') || '1');
    if (!Number.isSafeInteger(page) || page < 1) url.searchParams.set('page', '1');
    url.searchParams.delete('snapshot');
    return url;
  }

  function snapshotUrl() {
    const url = currentUrl();
    url.searchParams.set('snapshot', '1');
    return url;
  }

  function setStatus(message, error = false) {
    if (!status) return;
    status.textContent = message;
    status.classList.toggle('text-danger', error);
  }

  function updateCounts(stats) {
    if (!stats) return;
    root.querySelectorAll('[data-mine-stat], [data-mine-count]').forEach(node => {
      const name = node.dataset.mineStat || node.dataset.mineCount;
      const value = String(stats[name] ?? 0);
      if (node.textContent !== value) node.textContent = value;
    });
  }

  function updateActiveFilter(filter) {
    filters.forEach(link => {
      const active = link.dataset.serverMineFilter === filter;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  }

  function reconcile(html, force = false) {
    const parsed = new DOMParser().parseFromString(html, 'text/html');
    const incoming = parsed.getElementById('serverMineResults');
    const nextGrid = incoming?.querySelector('[data-server-mine-grid]');
    const grid = results.querySelector('[data-server-mine-grid]');
    if (!incoming || !nextGrid || !grid) throw new Error('SERVER_MINE_FRAGMENT_INVALID');

    const beforeX = window.scrollX, beforeY = window.scrollY;
    const focused = document.activeElement;
    const oldCards = new Map(Array.from(grid.querySelectorAll('[data-bp-key]'))
      .map(card => [card.dataset.bpKey, card]));
    const seen = new Set();
    Array.from(nextGrid.querySelectorAll('[data-bp-key]')).forEach((candidate, index) => {
      const key = candidate.dataset.bpKey;
      if (seen.has(key)) throw new Error('SERVER_MINE_DUPLICATE_ORDER');
      seen.add(key);
      let card = oldCards.get(key);
      if (!card) {
        card = document.importNode(candidate, true);
      } else if (card.outerHTML !== candidate.outerHTML &&
                 (force || !focused || !card.contains(focused))) {
        const replacement = document.importNode(candidate, true);
        if (!force) {
          const opened = Array.from(card.querySelectorAll('details')).map(node => node.open);
          replacement.querySelectorAll('details').forEach((node, i) => { node.open = !!opened[i]; });
        }
        card.replaceWith(replacement);
        card = replacement;
      }
      if (grid.children[index] !== card) grid.insertBefore(card, grid.children[index] || null);
    });
    oldCards.forEach((card, key) => {
      if (!seen.has(key) && (force || !focused || !card.contains(focused))) card.remove();
    });

    const oldEmpty = results.querySelector('[data-server-mine-empty]');
    const newEmpty = incoming.querySelector('[data-server-mine-empty]');
    if (newEmpty && !oldEmpty) {
      grid.insertAdjacentElement('afterend', document.importNode(newEmpty, true));
    } else if (!newEmpty && oldEmpty) oldEmpty.remove();

    const currentPagination = results.querySelector('[data-server-mine-pagination]');
    const nextPagination = incoming.querySelector('[data-server-mine-pagination]');
    if (currentPagination && nextPagination && currentPagination.outerHTML !== nextPagination.outerHTML) {
      currentPagination.replaceWith(document.importNode(nextPagination, true));
    }
    if (window.scrollX !== beforeX || window.scrollY !== beforeY) window.scrollTo(beforeX, beforeY);
  }

  async function refresh(force = false) {
    if (mutationInProgress || document.hidden || navigator.onLine === false) return;
    if (controller) {
      if (!force) return;
      controller.abort();
    }
    const current = new AbortController();
    controller = current;
    const seq = ++generation;
    const url = snapshotUrl();
    const key = url.href;
    const headers = {Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest'};
    if (renderedKey === key && etags.has(key)) headers['If-None-Match'] = etags.get(key);
    setStatus('Synchronisation…');
    try {
      const response = await fetch(key, {
        credentials: 'same-origin', cache: 'no-store', headers, signal: current.signal,
      });
      if (seq !== generation) return;
      if (response.status === 304) {
        setStatus('À jour · aucune modification');
        return;
      }
      if (!response.ok || !response.headers.get('Content-Type')?.includes('application/json')) {
        throw new Error('SERVER_MINE_REFRESH_FAILED');
      }
      const payload = await response.json();
      if (seq !== generation) return;
      if (typeof payload.html !== 'string' || !payload.stats || !allowed.has(payload.filter)) {
        throw new Error('SERVER_MINE_PAYLOAD_INVALID');
      }
      reconcile(payload.html, force);
      updateCounts(payload.stats);
      updateActiveFilter(payload.filter);
      renderedKey = key;
      const etag = response.headers.get('ETag');
      if (etag) etags.set(key, etag);
      setStatus('À jour · ' + new Intl.DateTimeFormat('fr-FR', {hour:'2-digit', minute:'2-digit'}).format(new Date()));
    } catch (error) {
      if (seq === generation && error.name !== 'AbortError') {
        setStatus('Connexion interrompue · nouvelle tentative automatique', true);
      }
    } finally {
      if (controller === current) controller = null;
    }
  }

  function navigate(url) {
    const target = new URL(url, window.location.href);
    if (target.origin !== window.location.origin || target.pathname !== window.location.pathname) return;
    window.history.pushState({}, '', target.href);
    void refresh(true);
  }

  root.addEventListener('click', event => {
    const link = event.target.closest?.('a[data-server-mine-filter], [data-server-mine-pagination] a[href]');
    if (!link || link.hasAttribute('aria-disabled')) return;
    event.preventDefault();
    navigate(link.href);
  });
  refreshButton?.addEventListener('click', () => { void refresh(true); });
  window.addEventListener('popstate', () => { void refresh(true); });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) controller?.abort();
    else void refresh(true);
  });
  window.addEventListener('online', () => { void refresh(true); });
  window.setInterval(() => { void refresh(); }, 8000);

  // The existing BarProAsync form handler performs CSRF-safe POST and prevents
  // duplicate submissions. The server remains authoritative for every action.
  root.addEventListener('submit', event => {
    const form = event.target;
    if (form?.matches?.('form[data-confirm-cancel]') &&
        !window.confirm('Confirmer l’annulation de cette commande ?')) {
      event.preventDefault();
    }
  }, true);
  root.addEventListener('barpro:async-success', event => {
    if (!event.target?.matches?.('.server-mine-action form')) return;
    mutationInProgress = false;
    etags.clear();
    renderedKey = null;
    event.target.closest('details')?.removeAttribute('open');
    void refresh(true);
  });
  root.addEventListener('barpro:async-error', event => {
    if (!event.target?.matches?.('.server-mine-action form')) return;
    mutationInProgress = false;
  });
  // Stop poll while a mutation is in progress, but leave native HTML fallback.
  root.addEventListener('submit', event => {
    if (event.target?.matches?.('.server-mine-action form') && !event.defaultPrevented) {
      mutationInProgress = true;
      controller?.abort();
    }
  });
  updateActiveFilter(currentUrl().searchParams.get('filter') || 'active');
})();
