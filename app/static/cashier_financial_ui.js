/* Cashier financial pages: opt-in progressive enhancement. Never infer
   financial success from a click; only a committed JSON response is success.
   Failed requests retain every field and the same durable intent key. */
(function (root) {
  'use strict';
  const doc = root.document;
  if (!doc) return;

  const sections = {
    returns: ['return-orders', 'return-history'],
    handovers: ['handover-stats', 'handover-servers', 'handover-pending', 'handover-history'],
    exchanges: ['exchange-history']
  };
  let refreshing = false;

  function keySection(name) {
    return '[data-bp-financial-section="' + name + '"]';
  }

  function updateSections(parsed, names) {
    const patches = names.map(name => {
      const oldNode = doc.querySelector(keySection(name));
      const newNode = parsed.querySelector(keySection(name));
      if (!oldNode || !newNode) throw new Error('Actualisation incomplète : ' + name);
      return [oldNode, newNode];
    });
    const scrollX = root.scrollX, scrollY = root.scrollY;
    patches.forEach(([oldNode, newNode]) => {
      if (oldNode.isEqualNode(newNode)) return;
      oldNode.replaceWith(doc.importNode(newNode, true));
    });
    if (typeof root.scrollTo === 'function') root.scrollTo(scrollX, scrollY);
  }

  function renewExchange(form, parsed) {
    const source = parsed.getElementById(form.id);
    if (!source) throw new Error('Formulaire actualisé absent.');
    for (const selectId of ['returnedProduct', 'replacementProduct', 'refundProduct']) {
      const current = doc.getElementById(selectId);
      const fresh = parsed.getElementById(selectId);
      if (!current || !fresh) continue;
      // Update stock, price and availability without replacing live event listeners.
      for (const opt of current.options) {
        const update = Array.from(fresh.options).find(candidate => candidate.value === opt.value);
        if (!update) continue;
        opt.textContent = update.textContent;
        opt.disabled = update.disabled;
        opt.dataset.price = update.dataset.price || '';
      }
    }
    form.reset();
    const oldKey = form.querySelector('[name="idempotency_key"]');
    const nextKey = source.querySelector('[name="idempotency_key"]');
    if (!oldKey || !nextKey) throw new Error('Clé de confirmation introuvable.');
    oldKey.value = nextKey.value;
    for (const node of form.querySelectorAll('select')) node.dispatchEvent(new Event('input', { bubbles: true }));
    const modal = form.closest('.modal');
    root.bootstrap?.Modal?.getInstance(modal)?.hide();
  }

  async function refreshAfterCommit(form, payload) {
    if (refreshing) return;
    refreshing = true;
    const kind = form.dataset.bpFinancial;
    try {
      const url = new URL(root.location.href);
      if (kind === 'returns') {
        const data = payload?.data || {};
        if (data.order_id) url.searchParams.set('order_id', String(data.order_id));
        if (data.return_id) url.searchParams.set('return_id', String(data.return_id));
      }
      const api = root.BarProAsync;
      if (!api?.request) throw new Error('Module de mise à jour indisponible.');
      const html = await api.request(url.href, { format: 'html', key: 'financial:' + kind, timeout: 15000 });
      const parsed = new DOMParser().parseFromString(html, 'text/html');
      if (kind === 'exchanges') renewExchange(form, parsed);
      updateSections(parsed, sections[kind] || []);
      if (kind === 'returns') root.history.replaceState(root.history.state, '', url.href);
      doc.dispatchEvent(new CustomEvent('barpro:financial-updated', {
        detail: { operation: kind, receipt: payload?.data || {} }
      }));
    } catch (error) {
      // The POST was already committed: NEVER retry or claim rollback.
      const region = doc.getElementById('bp-financial-refresh-warning') || doc.createElement('div');
      region.id = 'bp-financial-refresh-warning';
      region.className = 'alert alert-warning mt-3';
      region.setAttribute('role', 'status');
      region.textContent = 'Opération enregistrée, mais affichage non actualisé. Actualisez cette page avant une nouvelle opération. ' + (error.message || '');
      (doc.querySelector('[data-bp-financial-section]') || doc.body).before(region);
    } finally {
      refreshing = false;
    }
  }

  doc.addEventListener('barpro:async-success', event => {
    const form = event.target?.closest?.('form[data-bp-financial]');
    if (!form || !sections[form.dataset.bpFinancial]) return;
    void refreshAfterCommit(form, event.detail?.payload);
  });
})(window);
