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
  if (!grid || !cartLines || !cartPanel || !totalNode || !countNode) return;

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

  sync();
})();
