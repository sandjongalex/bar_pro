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
  const grandTotal = cartPanel.querySelector('.pos-grand-total strong');
  const initialTotal = totalNode.textContent || '0';
  const currency = (grandTotal?.textContent || '').replace(initialTotal, '').trim();

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
    const cards = Array.from(document.querySelectorAll('[data-server-order-card]'));
    const empty = document.querySelector('[data-server-orders-empty]');
    if (!filters.length || !cards.length) return;

    function applyFilter(filter) {
      let visible = 0;
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
      filters.forEach((item) => {
        const active = item === requested;
        item.classList.toggle('active', active);
        item.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      applyFilter(requested?.dataset.serverOrderFilter || 'active');
    }

    filters.forEach((button) => {
      button.addEventListener('click', () => selectFilter(button.dataset.serverOrderFilter || 'active'));
    });

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

  if (returnFilter && orderPanel) {
    window.setTimeout(() => orderPanel.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
  }
})();