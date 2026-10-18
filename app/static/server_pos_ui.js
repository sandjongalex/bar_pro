(function () {
  'use strict';

  const layout = document.querySelector('.server-pos-layout');
  if (!layout) return;

  const grid = document.getElementById('productGrid');
  const cartLines = document.getElementById('cartLines');
  const cartPanel = layout.querySelector('.pos-cart-panel');
  const totalNode = document.getElementById('cartTotal');
  const countNode = document.getElementById('cartCount');
  const search = document.getElementById('posSearch');
  const clearCart = document.getElementById('clearCart');
  const orderPanel = document.getElementById('mes-commandes');
  const orderForm = document.getElementById('posForm');
  if (!grid || !cartLines || !cartPanel || !totalNode || !countNode) return;

  const RETURN_KEY = 'bar-pro:server-orders-return';
  const pathMatch = window.location.pathname.match(/\/bars\/(\d+)\//);
  const liveEndpoint = pathMatch ? `/bars/${pathMatch[1]}/live/orders` : '';
  const grandTotal = cartPanel.querySelector('.pos-grand-total strong');
  const initialTotal = totalNode.textContent || '0';
  const currency = (grandTotal?.textContent || '').replace(initialTotal, '').trim();
  let refreshOrderFilters = function () {};
  let currentOrderFilter = 'active';
  let liveTimer = null;
  let liveInFlight = false;
  let liveRefreshQueued = false;

  const dock = document.createElement('div');
  dock.className = 'server-cart-dock';
  dock.hidden = true;
  dock.innerHTML = `
    <button type="button" class="server-cart-dock-button" aria-label="Voir le panier">
      <span class="server-cart-dock-copy">
        <small>Commande en cours</small>
        <strong><span data-server-dock-count>0</span> article · <span data-server-dock-total>0</span> ${escapeHtml(currency)}</strong>
      </span>
      <span class="server-cart-dock-action">Voir panier ↑</span>
    </button>
  `;
  document.body.appendChild(dock);

  const dockButton = dock.querySelector('.server-cart-dock-button');
  const dockCount = dock.querySelector('[data-server-dock-count]');
  const dockTotal = dock.querySelector('[data-server-dock-total]');

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
  }

  function rememberOrdersReturn(filter) {
    try {
      window.sessionStorage.setItem(RETURN_KEY, filter || 'active');
    } catch (error) {}
  }

  function consumeOrdersReturn() {
    try {
      const value = window.sessionStorage.getItem(RETURN_KEY);
      if (value) window.sessionStorage.removeItem(RETURN_KEY);
      return value || '';
    } catch (error) {
      return '';
    }
  }

  function ensureProductBadge(card) {
    card.classList.add('server-fast-product');
    let badge = card.querySelector('[data-server-product-qty]');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'server-product-qty';
      badge.dataset.serverProductQty = '';
      badge.hidden = true;
      card.appendChild(badge);
    }
    return badge;
  }

  function cartQuantities() {
    const quantities = new Map();
    cartLines.querySelectorAll('.pos-cart-line').forEach((row) => {
      const id = row.querySelector('input[name="product_id"]')?.value;
      const quantity = Number(row.querySelector('input[name="quantity"]')?.value || 0);
      if (id && Number.isFinite(quantity) && quantity > 0) quantities.set(String(id), quantity);
    });
    return quantities;
  }

  function formatQuantity(value) {
    return Number.isInteger(value) ? String(value) : String(value).replace(/\.0+$/, '');
  }

  function sync() {
    const quantities = cartQuantities();

    grid.querySelectorAll('.pos-product').forEach((card) => {
      const badge = ensureProductBadge(card);
      const quantity = quantities.get(String(card.dataset.productId || '')) || 0;
      const selected = quantity > 0;
      card.classList.toggle('is-selected', selected);
      card.setAttribute('aria-pressed', selected ? 'true' : 'false');
      badge.hidden = !selected;
      badge.textContent = selected ? `×${formatQuantity(quantity)}` : '';
    });

    const hasCart = quantities.size > 0;
    dock.hidden = !hasCart;
    dockCount.textContent = countNode.textContent || '0';
    dockTotal.textContent = totalNode.textContent || '0';
    const itemCount = Number((countNode.textContent || '0').replace(/\s/g, '').replace(',', '.'));
    dockButton?.setAttribute('aria-label', `Voir le panier, ${Number.isFinite(itemCount) ? itemCount : 0} article${itemCount > 1 ? 's' : ''}`);
    document.body.classList.toggle('server-cart-active', hasCart);
  }

  function setupOrderFilters(initialFilter) {
    const filters = Array.from(document.querySelectorAll('[data-server-order-filter]'));
    const empty = document.querySelector('[data-server-orders-empty]');
    if (!filters.length) return;

    function applyFilter(filter) {
      let visible = 0;
      const cards = Array.from(document.querySelectorAll('[data-server-order-card]'));
      cards.forEach((card) => {
        const state = card.dataset.orderState || 'other';
        const matches = filter === 'all'
          || (filter === 'active' && (state === 'waiting' || state === 'to_pay'))
          || state === filter;
        card.hidden = !matches;
        if (matches) visible += 1;
      });
      if (empty) {
        empty.hidden = visible !== 0;
        empty.textContent = filter === 'active'
          ? 'Aucune commande active. Touchez « Toutes » pour voir l’historique récent.'
          : 'Aucune commande dans ce filtre.';
      }
    }

    function selectFilter(filter) {
      const requested = filters.find((item) => item.dataset.serverOrderFilter === filter) || filters[0];
      currentOrderFilter = requested?.dataset.serverOrderFilter || 'active';
      filters.forEach((item) => {
        const active = item === requested;
        item.classList.toggle('active', active);
        item.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      applyFilter(currentOrderFilter);
    }

    filters.forEach((button) => {
      button.addEventListener('click', () => selectFilter(button.dataset.serverOrderFilter || 'active'));
    });

    refreshOrderFilters = () => selectFilter(currentOrderFilter);
    selectFilter(initialFilter || 'active');
  }

  function setupInlineOrderActions() {
    const cards = Array.from(document.querySelectorAll('[data-server-order-card]'));
    if (!cards.length) return;

    function closeEditors(exceptEditor) {
      document.querySelectorAll('.server-order-inline-editor').forEach((editor) => {
        if (editor === exceptEditor) return;
        editor.hidden = true;
        editor.closest('[data-server-order-card]')?.classList.remove('is-editing');
        const triggerId = editor.dataset.triggerId;
        if (triggerId) document.getElementById(triggerId)?.setAttribute('aria-expanded', 'false');
      });
    }

    cards.forEach((card) => {
      const actionButtons = Array.from(card.querySelectorAll('.server-order-actions button[data-bs-target]'));
      actionButtons.forEach((button, index) => {
        const target = button.getAttribute('data-bs-target');
        const modal = target ? document.querySelector(target) : null;
        const sourceForm = modal?.querySelector('form');
        if (!sourceForm) return;

        const action = sourceForm.querySelector('input[name="action"]')?.value || '';
        const orderId = sourceForm.querySelector('input[name="order_id"]')?.value || '';
        const sourceTextarea = sourceForm.querySelector('textarea');
        if (!action || !orderId || !sourceTextarea) return;

        const editor = document.createElement('div');
        const editorId = `server-order-inline-${action}-${orderId}`;
        const triggerId = `server-order-trigger-${action}-${orderId}-${index}`;
        editor.id = editorId;
        editor.className = `server-order-inline-editor server-order-inline-${action}`;
        editor.hidden = true;
        editor.dataset.triggerId = triggerId;

        const form = document.createElement('form');
        form.method = 'post';
        form.className = 'server-order-inline-form';
        sourceForm.querySelectorAll('input[type="hidden"]').forEach((input) => form.appendChild(input.cloneNode(true)));

        const heading = document.createElement('div');
        heading.className = 'server-order-inline-head';
        heading.innerHTML = action === 'cancel'
          ? '<strong>Annuler cette commande ?</strong><small>Indiquez le motif. Cette action n’est disponible que tant que la caisse n’a pas confirmé la livraison.</small>'
          : '<strong>Ajouter une note à la caisse</strong><small>La note est ajoutée à cette commande sans ouvrir une autre fenêtre.</small>';
        form.appendChild(heading);

        const textarea = sourceTextarea.cloneNode(true);
        textarea.classList.add('server-order-inline-textarea');
        textarea.rows = 2;
        textarea.setAttribute('aria-label', action === 'cancel' ? 'Motif de l’annulation' : 'Note à la caisse');
        form.appendChild(textarea);

        const controls = document.createElement('div');
        controls.className = 'server-order-inline-controls';
        const closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'btn btn-sm btn-light border';
        closeButton.textContent = 'Fermer';
        const submitButton = document.createElement('button');
        submitButton.type = 'submit';
        submitButton.className = action === 'cancel' ? 'btn btn-sm btn-danger' : 'btn btn-sm btn-primary';
        submitButton.textContent = action === 'cancel' ? 'Confirmer l’annulation' : 'Envoyer la note';
        controls.append(closeButton, submitButton);
        form.appendChild(controls);
        editor.appendChild(form);
        card.appendChild(editor);

        button.id = triggerId;
        button.removeAttribute('data-bs-toggle');
        button.removeAttribute('data-bs-target');
        button.setAttribute('aria-controls', editorId);
        button.setAttribute('aria-expanded', 'false');

        function closeCurrent() {
          editor.hidden = true;
          card.classList.remove('is-editing');
          button.setAttribute('aria-expanded', 'false');
        }

        button.addEventListener('click', () => {
          const opening = editor.hidden;
          closeEditors(opening ? editor : null);
          if (!opening) {
            closeCurrent();
            return;
          }
          editor.hidden = false;
          card.classList.add('is-editing');
          button.setAttribute('aria-expanded', 'true');
          window.setTimeout(() => {
            textarea.focus({ preventScroll: true });
            if (window.matchMedia('(max-width: 767px)').matches) {
              editor.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
          }, 40);
        });

        closeButton.addEventListener('click', closeCurrent);
        form.addEventListener('submit', () => {
          rememberOrdersReturn(action === 'cancel' ? 'all' : 'active');
          submitButton.disabled = true;
          submitButton.textContent = action === 'cancel' ? 'Annulation…' : 'Envoi…';
        });

        modal?.remove();
      });
    });
  }

  function setupSectionNavigation() {
    if (!orderForm || !orderPanel) return;

    const stats = document.querySelector('.server-pos-stats');
    const switcher = document.createElement('nav');
    switcher.className = 'server-section-switcher';
    switcher.setAttribute('aria-label', 'Navigation rapide du service');
    switcher.innerHTML = `
      <a href="#posForm" data-server-section-link="new"><span>＋</span><strong>Nouvelle commande</strong></a>
      <a href="#mes-commandes" data-server-section-link="orders"><span>▤</span><strong>Mes commandes</strong></a>
    `;
    if (stats) stats.insertAdjacentElement('afterend', switcher);
    else orderForm.insertAdjacentElement('beforebegin', switcher);

    const sectionLinks = Array.from(document.querySelectorAll('[data-server-section-link], [data-server-bottom-link]'));
    if (!sectionLinks.length) return;

    function activate(section) {
      sectionLinks.forEach((link) => {
        const linkSection = link.dataset.serverSectionLink || link.dataset.serverBottomLink;
        const active = linkSection === section;
        link.classList.toggle('active', active);
        if (active) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
      });
    }

    function targetFor(section) {
      return section === 'orders' ? orderPanel : orderForm;
    }

    sectionLinks.forEach((link) => {
      const section = link.dataset.serverSectionLink || link.dataset.serverBottomLink;
      if (!section) return;
      link.addEventListener('click', (event) => {
        const target = targetFor(section);
        if (!target) return;
        event.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        const hash = section === 'orders' ? '#mes-commandes' : '#posForm';
        try { window.history.replaceState(null, '', hash); } catch (error) {}
        activate(section);
      });
    });

    let scheduled = false;
    function syncFromScroll() {
      scheduled = false;
      const threshold = Math.min(window.innerHeight * 0.48, 420);
      activate(orderPanel.getBoundingClientRect().top <= threshold ? 'orders' : 'new');
    }
    window.addEventListener('scroll', () => {
      if (scheduled) return;
      scheduled = true;
      window.requestAnimationFrame(syncFromScroll);
    }, { passive: true });

    const initialSection = window.location.hash === '#mes-commandes' ? 'orders' : 'new';
    activate(initialSection);
    window.setTimeout(syncFromScroll, 80);
  }

  function syncLiveStock(stock) {
    const activeCategory = document.querySelector('#categoryTabs .pos-category.active')?.dataset.category || 'all';
    const query = (search?.value || '').trim().toLocaleLowerCase('fr');
    grid.querySelectorAll('.pos-product[data-product-id]').forEach((card) => {
      const quantity = Number(stock?.[String(card.dataset.productId)] || 0);
      const available = Number.isFinite(quantity) && quantity > 0;
      card.dataset.stock = String(quantity);
      card.classList.toggle('out-of-stock', !available);
      card.disabled = !available;
      const stockBadge = card.querySelector('.pos-stock');
      if (stockBadge) {
        stockBadge.textContent = available ? `Stock ${formatQuantity(quantity)}` : 'Rupture';
        stockBadge.classList.toggle('danger', !available);
      }
      const categoryOk = activeCategory === 'all' || card.dataset.category === activeCategory;
      const searchOk = !query || (card.dataset.name || '').toLocaleLowerCase('fr').includes(query);
      card.hidden = !(available && categoryOk && searchOk);
    });
  }

  let lastServerSnapshotMs = 0;

  function makeLiveServerCard(order) {
    const id = Number(order.id);
    if (!Number.isSafeInteger(id) || id < 1) return null;
    const state = ['waiting', 'to_pay', 'paid', 'cancelled'].includes(order.state) ? order.state : 'other';
    const name = escapeHtml(order.display_name || 'COMPTOIR');
    const tableName = escapeHtml(order.table || 'Sans table');
    const ref = escapeHtml(order.reference || '');
    const amount = new Intl.NumberFormat('fr-FR', {maximumFractionDigits: 0}).format(Number(order.total_amount) || 0);
    const currency = escapeHtml(order.currency || '');
    const csrf = escapeHtml(document.querySelector('#posForm [name="csrf_token"]')?.value || '');
    const card = document.createElement('article');
    card.className = 'server-order-card server-order-card-' + state;
    card.dataset.serverOrderCard = '';
    card.dataset.serverOrderId = String(id);
    card.dataset.orderState = state;
    const allowActions = state === 'waiting' || state === 'to_pay';
    const actions = allowActions
      ? '<div class="server-order-actions">' +
          '<a class="btn btn-sm btn-light border server-order-direct-detail" data-server-order-detail href="/bars/' + encodeURIComponent(barId) + '/orders/' + id + '/detail">Détails</a>' +
          '<a class="btn btn-sm btn-outline-primary server-order-direct-suborder" data-server-add-suborder href="/bars/' + encodeURIComponent(barId) + '/orders/' + id + '/suborders/new">＋ Ajouter des produits</a>' +
          '<button class="btn btn-sm btn-light border" type="button" data-bs-toggle="modal" data-bs-target="#noteOrder' + id + '">＋ Note</button>' +
          (state === 'waiting' ? '<button class="btn btn-sm btn-outline-danger" type="button" data-bs-toggle="modal" data-bs-target="#cancelOrder' + id + '">Annuler</button>' : '') +
        '</div>' : '';
    const products = (order.lines || []).slice(0, 4).map(line =>
      '<div><strong>' + escapeHtml(line.quantity) + '×</strong><span>' + escapeHtml(line.name) + '</span></div>'
    ).join('');
    card.innerHTML =
      '<div class="server-order-card-head"><div class="server-order-location"><small>Facture</small><strong>' + name +
        '</strong><small class="server-order-table-caption">Table : ' + tableName + '</small></div>' +
      '<div class="server-order-amount">' + amount + ' <small>' + currency + '</small></div></div>' +
      '<div class="server-order-status-row"><span class="server-order-state"></span><small class="server-order-reference">' + ref + '</small></div>' +
      '<div class="server-order-products">' + products + '</div>' +
      '<div class="server-order-footer"><small>' + escapeHtml(order.created_at || '') + '</small>' + actions + '</div>';
    if (allowActions) {
      const createModal = (action, textareaName, label, requiredText) => {
        const idValue = action + 'Order' + id;
        const modal = document.createElement('div');
        modal.className = 'modal fade';
        modal.id = idValue;
        modal.setAttribute('tabindex', '-1');
        modal.setAttribute('aria-hidden', 'true');
        modal.innerHTML =
          '<div class="modal-dialog modal-dialog-centered"><div class="modal-content pos-modal"><form method="post">' +
            '<input type="hidden" name="csrf_token" value="' + csrf + '">' +
            '<input type="hidden" name="action" value="' + escapeHtml(action) + '">' +
            '<input type="hidden" name="order_id" value="' + id + '">' +
            '<div class="modal-header"><h2 class="modal-title h5">' + label + ' · ' + name + '</h2>' +
            '<button class="btn-close" type="button" data-bs-dismiss="modal" aria-label="Fermer"></button></div>' +
            '<div class="modal-body"><label class="form-label" for="input' + idValue + '">' + requiredText + '</label>' +
            '<textarea class="form-control" id="input' + idValue + '" name="' + textareaName + '" maxlength="' +
              (action === 'note' ? '220' : '500') + '" rows="3" required></textarea></div>' +
            '<div class="modal-footer"><button class="btn btn-light border" type="button" data-bs-dismiss="modal">Retour</button>' +
            '<button class="btn btn-primary" type="submit">Confirmer</button></div></form></div></div>';
        document.body.appendChild(modal);
      };
      createModal('note', 'note', 'Note', 'Message à la caisse');
      if (state === 'waiting') createModal('cancel', 'reason', 'Annulation', 'Motif obligatoire');
    }
    return card;
  }

  function syncLiveOrders(payload) {
    if (!payload || payload.mode !== 'SERVER') return;
    const orders = Array.isArray(payload.orders) ? payload.orders : [];
    const snapshotMs = Date.parse(payload.generated_at || '');
    if (Number.isFinite(snapshotMs) && snapshotMs < lastServerSnapshotMs) return;
    if (Number.isFinite(snapshotMs)) lastServerSnapshotMs = snapshotMs;
    const byReference = new Map(orders.map((order) => [String(order.reference), order]));
    const byId = new Map(orders.map((order) => [String(order.id), order]));
    const list = document.querySelector('[data-server-orders-grid]');
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    const activeCard = document.activeElement?.closest?.('[data-server-order-card]');
    let cardsChanged = false;
    if (list) {
      orders.forEach((order, index) => {
        const id = String(order.id);
        let card = Array.from(list.querySelectorAll('[data-server-order-card]'))
          .find(item => item.dataset.serverOrderId === id);
        if (!card) {
          card = makeLiveServerCard(order);
          if (card) { list.appendChild(card); cardsChanged = true; }
        }
        if (card && card !== activeCard && list.children[index] !== card) {
          list.insertBefore(card, list.children[index] || null);
          cardsChanged = true;
        }
      });
      list.querySelectorAll('[data-server-order-card]').forEach(card => {
        const id = card.dataset.serverOrderId;
        if (id && !byId.has(id) && !card.contains(document.activeElement)) { card.remove(); cardsChanged = true; }
      });
      setupInlineOrderActions();
      window.BarProOrderEdit?.setupServerEditors?.();
      if (cardsChanged) window.scrollTo?.(scrollX, scrollY);
    }

    document.querySelectorAll('[data-server-order-card]').forEach((card) => {
      const reference = card.querySelector('.server-order-reference')?.textContent?.trim() || '';
      const order = byId.get(card.dataset.serverOrderId) || byReference.get(reference);
      if (!order) return;

      const state = order.state || 'other';
      card.dataset.orderState = state;
      ['waiting', 'to_pay', 'paid', 'cancelled', 'other'].forEach((value) => {
        card.classList.remove(`server-order-card-${value}`);
      });
      card.classList.add(`server-order-card-${state}`);

      const stateNode = card.querySelector('.server-order-state');
      if (stateNode) {
        stateNode.className = `server-order-state ${state === 'to_pay' ? 'to-pay' : state}`;
        if (state === 'waiting') stateNode.textContent = '● En attente caisse';
        else if (state === 'to_pay') stateNode.textContent = '✓ Livrée · à payer';
        else if (state === 'paid') stateNode.textContent = '✓ Payée';
        else if (state === 'cancelled') stateNode.textContent = 'Annulée';
        else stateNode.textContent = order.status || 'Mise à jour';
      }

      const amountNode = card.querySelector('.server-order-amount');
      if (amountNode) {
        const amount = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 }).format(Number(order.total_amount || 0));
        amountNode.innerHTML = `${amount} <small>${escapeHtml(order.currency || '')}</small>`;
      }

      const productsNode = card.querySelector('.server-order-products');
      if (productsNode && Array.isArray(order.lines)) {
        const visibleLines = order.lines.slice(0, 4).map((line) => `<div><strong>${escapeHtml(line.quantity)}×</strong><span>${escapeHtml(line.name)}</span></div>`).join('');
        const extra = order.lines.length > 4 ? `<small>+ ${order.lines.length - 4} autre${order.lines.length - 4 > 1 ? 's' : ''}</small>` : '';
        productsNode.innerHTML = visibleLines + extra;
      }

      const noteNode = card.querySelector('.server-order-note');
      if (noteNode && String(order.notes || '') !== (noteNode.querySelector('p')?.textContent || '')) {
        noteNode.querySelector('p').textContent = String(order.notes || '');
      } else if (!noteNode && order.notes) {
        const node = document.createElement('div');
        node.className = 'server-order-note';
        node.innerHTML = '<span>Note</span><p></p>';
        node.querySelector('p').textContent = String(order.notes);
        card.querySelector('.server-order-footer')?.before(node);
      }
      if (noteNode && !order.notes) noteNode.remove();

      if (state !== 'waiting') {
        card.querySelector('.server-order-actions .btn-outline-danger')?.remove();
        card.querySelector('.server-order-inline-cancel')?.remove();
      }
      if (state === 'paid' || state === 'cancelled') {
        card.querySelector('.server-order-actions')?.remove();
        card.querySelectorAll('.server-order-inline-editor').forEach((editor) => editor.remove());
        card.classList.remove('is-editing');
      }
    });

    const statNodes = document.querySelectorAll('.server-pos-stats article strong');
    if (statNodes[0]) statNodes[0].textContent = String(payload.stats?.waiting ?? 0);
    if (statNodes[1]) statNodes[1].textContent = String(payload.stats?.to_pay ?? 0);
    if (statNodes[2]) statNodes[2].textContent = String(payload.stats?.paid ?? 0);

    document.querySelectorAll('[data-server-order-filter]').forEach((button) => {
      const filter = button.dataset.serverOrderFilter;
      const count = button.querySelector('span');
      if (!count) return;
      if (filter === 'active') count.textContent = String((payload.stats?.waiting || 0) + (payload.stats?.to_pay || 0));
      else if (filter === 'waiting') count.textContent = String(payload.stats?.waiting || 0);
      else if (filter === 'to_pay') count.textContent = String(payload.stats?.to_pay || 0);
      else if (filter === 'paid') count.textContent = String(payload.stats?.paid || 0);
    });

    refreshOrderFilters();
  }

  async function pollLiveState() {
    if (!liveEndpoint || document.hidden || navigator.onLine === false) return;
    if (liveInFlight) { liveRefreshQueued = true; return; }
    liveInFlight = true;
    try {
      const response = await fetch(liveEndpoint, {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) return;
      const payload = await response.json();
      syncLiveStock(payload.stock || {});
      syncLiveOrders(payload);
    } catch (error) {
      // Temporary network errors are ignored; the next poll retries automatically.
    } finally {
      liveInFlight = false;
      if (liveRefreshQueued) {
        liveRefreshQueued = false;
        void pollLiveState();
      }
    }
  }

  function startLiveSync() {
    if (!liveEndpoint) return;
    window.clearInterval(liveTimer);
    liveTimer = window.setInterval(pollLiveState, 4000);
    window.addEventListener('online', pollLiveState);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) pollLiveState();
    });
    pollLiveState();
  }

  window.BarProServerState = Object.freeze({ refresh: pollLiveState });

  const returnFilter = consumeOrdersReturn();
  const observer = new MutationObserver(sync);
  observer.observe(cartLines, { childList: true, subtree: true });

  grid.addEventListener('click', () => window.requestAnimationFrame(sync));
  clearCart?.addEventListener('click', () => window.requestAnimationFrame(sync));
  cartLines.addEventListener('change', () => window.requestAnimationFrame(sync));
  cartLines.addEventListener('input', () => window.requestAnimationFrame(sync));

  dockButton?.addEventListener('click', () => {
    cartPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  document.querySelectorAll('.server-table-chip').forEach((button) => {
    button.addEventListener('click', () => {
      if (!window.matchMedia('(max-width: 980px)').matches) return;
      window.setTimeout(() => {
        search?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        if (window.matchMedia('(min-width: 768px)').matches) search?.focus({ preventScroll: true });
      }, 120);
    });
  });

  orderForm?.addEventListener('submit', () => rememberOrdersReturn('active'));
  setupInlineOrderActions();
  setupOrderFilters(returnFilter || 'active');
  setupSectionNavigation();
  sync();
  startLiveSync();

  if (returnFilter && orderPanel) {
    window.setTimeout(() => orderPanel.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
  }
})();