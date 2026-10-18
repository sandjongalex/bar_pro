/* Selective progressive enhancement of BAR_PRO secondary cashier pages.
 * Never retry uncertain financial POSTs automatically; server owns the commit.
 * Native HTML forms remain fully functional when JavaScript is unavailable.
 */
(function (root) {
  'use strict';
  const doc = root.document;
  if (!doc) return;

  const parts = {
    expenses: ['expenses-stats', 'expenses-analysis', 'expenses-history'],
    customers: ['customers-content'],
    daily: ['daily-content'],
    vouchers: ['vouchers-content'],
    history: ['history-warning', 'history-results']
  };
  let busy = false;
  let sequence = 0;

  function selector(name) {
    return '[data-bp-secondary-section="' + name + '"]';
  }
  function warning(message) {
    let node = doc.getElementById('bp-secondary-warning');
    if (!node) {
      node = doc.createElement('div');
      node.id = 'bp-secondary-warning';
      node.className = 'alert alert-warning mt-3';
      node.setAttribute('role', 'alert');
      const anchor = doc.querySelector('[data-bp-secondary-section]');
      (anchor || doc.body).before(node);
    }
    node.textContent = message;
  }
  function patch(documentFromServer, names) {
    const patches = names.map(name => {
      const current = doc.querySelector(selector(name));
      const next = documentFromServer.querySelector(selector(name));
      if (!current || !next) throw new Error('Bloc indisponible : ' + name);
      return [current, next];
    });
    const x = root.scrollX, y = root.scrollY;
    patches.forEach(([current, next]) => {
      if (!current.isEqualNode(next)) current.replaceWith(doc.importNode(next, true));
    });
    if (root.scrollTo) root.scrollTo(x, y);
  }
  function resetAfterCommit(form, fromServer) {
    if (!form.isConnected) return;
    const next = fromServer.querySelector('form[data-bp-secondary="expenses"] [name="idempotency_key"]');
    if (!next) throw new Error('Nouvelle confirmation absente');
    form.reset();
    const key = form.querySelector('[name="idempotency_key"]');
    if (key) key.value = next.value;
  }
  function closeModal(form) {
    return new Promise(resolve => {
      const modal = form?.closest?.('.modal');
      if (!modal || !modal.classList.contains('show')) {
        resolve();
        return;
      }
      modal.addEventListener('hidden.bs.modal', () => resolve(), { once: true });
      const instance = root.bootstrap?.Modal?.getInstance(modal);
      if (instance) instance.hide();
      else resolve();
    });
  }

  async function refresh(kind, url, form, updateUrl) {
    if (busy) return;
    const current = ++sequence;
    busy = true;
    try {
      if (!root.BarProAsync?.request) throw new Error('Interface asynchrone indisponible');
      const html = await root.BarProAsync.request(url, {
        format: 'html', timeout: 15000, key: 'secondary:' + kind
      });
      if (current !== sequence) return;
      const parsed = new DOMParser().parseFromString(html, 'text/html');
      if (form && kind === 'customers') await closeModal(form);
      if (form && kind === 'expenses' && form.isConnected) resetAfterCommit(form, parsed);
      patch(parsed, parts[kind] || []);
      if (updateUrl) root.history.pushState(root.history.state, '', url);
      doc.getElementById('bp-secondary-warning')?.remove();
    } catch (error) {
      warning((form
        ? 'Opération enregistrée, mais actualisation interrompue. Ne recommencez pas sans vérifier les écritures. '
        : 'Impossible d’actualiser les résultats. Les filtres et données précédents sont conservés. ') +
        (error.message || ''));
    } finally {
      busy = false;
    }
  }

  doc.addEventListener('barpro:async-success', event => {
    const form = event.target?.closest?.('form[data-bp-secondary]');
    if (!form) return;
    const kind = form.dataset.bpSecondary;
    if (!parts[kind]) return;
    const action = form.querySelector('[name="action"]')?.value;
    const url = new URL(root.location.href);
    // Refresh from the canonical HTML read route, never re-POST for a refresh.
    if (kind === 'expenses' && action === 'expense_create') {
      // Preserve existing filters, including the chosen date window.
    }
    void refresh(kind, url.href, form, false);
  });

  doc.addEventListener('submit', event => {
    const form = event.target;
    if (!form?.matches?.('form[data-bp-secondary-filter]') || event.defaultPrevented) return;
    if (!root.BarProAsync?.request) return;
    event.preventDefault();
    const kind = form.dataset.bpSecondaryFilter;
    if (!parts[kind]) return;
    const target = new URL(form.action || root.location.href);
    target.search = new URLSearchParams(new FormData(form)).toString();
    void refresh(kind, target.href, null, true);
  });
  root.addEventListener('popstate', () => {
    const filter = doc.querySelector('form[data-bp-secondary-filter]');
    if (filter) root.location.reload();  // Preserve proper back/forward server state.
  });
})(window);
