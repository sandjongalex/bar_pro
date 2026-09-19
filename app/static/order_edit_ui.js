(function () {
  'use strict';

  const pathMatch = window.location.pathname.match(/\/bars\/(\d+)\//);
  if (!pathMatch) return;
  const barId = pathMatch[1];
  const csrf = document.querySelector('input[name="csrf_token"]')?.value || '';

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
  }

  function numberText(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return '0';
    return Number.isInteger(number) ? String(number) : String(number).replace(/\.0+$/, '');
  }

  function stateEndpoint(orderId) {
    return `/bars/${barId}/order-edits/${encodeURIComponent(orderId)}`;
  }

  function makeHost(orderId, context) {
    const host = document.createElement('div');
    host.className = `order-edit-editor ${context === 'cashier' ? 'cashier-order-edit-host' : 'server-order-edit-host'}`;
    host.dataset.orderEditEditor = '';
    host.dataset.orderId = String(orderId);
    host.dataset.context = context;
    host.hidden = true;
    return host;
  }

  function findServerOrderId(card) {
    const input = card.querySelector('.server-order-inline-editor input[name="order_id"], form input[name="order_id"]');
    return input?.value || '';
  }

  function createToggle(label) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-sm btn-outline-primary order-edit-toggle';
    button.dataset.orderEditToggle = '';
    button.textContent = label || '✎ Modifier';
    button.setAttribute('aria-expanded', 'false');
    return button;
  }

  function renderLocked(host, message) {
    host.innerHTML = `<div class="order-edit-lock">${escapeHtml(message || 'Cette commande ne peut plus être modifiée.')}</div>`;
  }

  function renderEditor(host, payload, context) {
    const order = payload.order || {};
    const products = Array.isArray(payload.products) ? payload.products : [];
    const originalLines = Array.isArray(order.lines) ? order.lines : [];
    const originalByProduct = new Map(originalLines.map((line) => [String(line.product_id), line]));

    if (!order.editable) {
      renderLocked(host, order.payment_status === 'PAID'
        ? 'Commande payée : les produits sont verrouillés.'
        : 'Cette commande a déjà été livrée. La serveuse ne peut plus modifier ses produits.');
      return;
    }

    host.innerHTML = `
      <form method="post" action="${stateEndpoint(order.id)}" data-order-edit-form>
        <input type="hidden" name="csrf_token" value="${escapeHtml(csrf)}">
        <div class="order-edit-head">
          <div>
            <strong>Modifier ${escapeHtml(order.reference || 'la commande')}</strong>
            <small>${context === 'server'
              ? 'Ajoutez, retirez ou changez les quantités avant la livraison.'
              : (order.delivered
                ? 'La caisse peut modifier les produits tant que la commande n’est pas totalement payée.'
                : 'Ajoutez, retirez ou changez les quantités avant la livraison.')}</small>
          </div>
          <button class="order-edit-close" type="button" aria-label="Fermer">×</button>
        </div>
        <div class="order-edit-lines" data-order-edit-lines></div>
        <div class="order-edit-add">
          <select class="form-select" data-order-edit-product-select aria-label="Ajouter un produit"></select>
          <button class="btn btn-light border" type="button" data-order-edit-add>＋ Ajouter</button>
        </div>
        ${context === 'cashier' && order.delivered ? `
        <div class="order-edit-reason">
          <label for="orderEditReason${escapeHtml(order.id)}">Motif de la modification <span class="text-secondary">(facultatif)</span></label>
          <input class="form-control" id="orderEditReason${escapeHtml(order.id)}" name="reason" maxlength="500" placeholder="Ex. le client ajoute une bière / retire une eau">
        </div>` : ''}
        <div class="order-edit-foot">
          <p class="order-edit-message" data-order-edit-message></p>
          <button class="btn btn-primary order-edit-save" type="submit" data-order-edit-save>Enregistrer les modifications</button>
        </div>
      </form>`;

    const form = host.querySelector('[data-order-edit-form]');
    const linesNode = host.querySelector('[data-order-edit-lines]');
    const select = host.querySelector('[data-order-edit-product-select]');
    const addButton = host.querySelector('[data-order-edit-add]');
    const saveButton = host.querySelector('[data-order-edit-save]');
    const message = host.querySelector('[data-order-edit-message]');
    const close = host.querySelector('.order-edit-close');

    function currentIds() {
      return new Set(Array.from(linesNode.querySelectorAll('.order-edit-line')).map((row) => String(row.dataset.productId)));
    }

    function refreshMessage() {
      const count = linesNode.querySelectorAll('.order-edit-line').length;
      const valid = count > 0;
      saveButton.disabled = !valid;
      message.classList.toggle('danger', !valid);
      message.textContent = valid
        ? `${count} produit${count > 1 ? 's' : ''} dans la commande.`
        : 'La commande doit garder au moins un produit. Utilisez « Annuler » si le client abandonne toute la commande.';
      refreshProductSelect();
    }

    function refreshProductSelect() {
      const used = currentIds();
      const available = products.filter((product) => !used.has(String(product.id)) && Number(product.stock || 0) > 0);
      select.innerHTML = '<option value="">Ajouter un produit en stock…</option>' + available.map((product) =>
        `<option value="${escapeHtml(product.id)}">${escapeHtml(product.name)} · stock ${escapeHtml(numberText(product.stock))}</option>`
      ).join('');
      addButton.disabled = available.length === 0;
    }

    function addLine(line) {
      const id = String(line.product_id);
      if (linesNode.querySelector(`.order-edit-line[data-product-id="${CSS.escape(id)}"]`)) return;
      const quantity = Math.max(Number(line.quantity || 1), 0.001);
      const maximum = Math.max(Number(line.max_quantity || quantity), quantity);
      const stock = Number(line.stock || 0);
      const row = document.createElement('div');
      row.className = 'order-edit-line';
      row.dataset.productId = id;
      row.dataset.maxQuantity = String(maximum);
      row.innerHTML = `
        <input type="hidden" name="product_id" value="${escapeHtml(id)}">
        <div class="order-edit-copy">
          <strong>${escapeHtml(line.name || 'Produit')}</strong>
          <small>Stock disponible : ${escapeHtml(numberText(stock))}</small>
        </div>
        <div class="order-edit-qty">
          <button type="button" data-order-edit-minus aria-label="Diminuer">−</button>
          <input name="quantity" type="number" min="0.001" max="${escapeHtml(maximum)}" step="0.001" value="${escapeHtml(numberText(quantity))}" inputmode="decimal" aria-label="Quantité ${escapeHtml(line.name || 'produit')}">
          <button type="button" data-order-edit-plus aria-label="Augmenter">+</button>
        </div>
        <button class="order-edit-remove" type="button" data-order-edit-remove>Supprimer</button>`;

      const input = row.querySelector('input[name="quantity"]');
      row.querySelector('[data-order-edit-minus]').addEventListener('click', () => {
        const current = Number(input.value || 0);
        if (current <= 1) {
          row.remove();
          refreshMessage();
          return;
        }
        input.value = numberText(Math.max(0.001, current - 1));
      });
      row.querySelector('[data-order-edit-plus]').addEventListener('click', () => {
        const current = Number(input.value || 0);
        input.value = numberText(Math.min(maximum, current + 1));
      });
      row.querySelector('[data-order-edit-remove]').addEventListener('click', () => {
        row.remove();
        refreshMessage();
      });
      input.addEventListener('change', () => {
        let value = Number(input.value || 0);
        if (!Number.isFinite(value) || value <= 0) {
          row.remove();
          refreshMessage();
          return;
        }
        value = Math.min(value, maximum);
        input.value = numberText(value);
      });
      linesNode.appendChild(row);
    }

    originalLines.forEach(addLine);
    refreshMessage();

    addButton.addEventListener('click', () => {
      const selectedId = String(select.value || '');
      if (!selectedId) return;
      const product = products.find((item) => String(item.id) === selectedId);
      if (!product) return;
      const original = originalByProduct.get(selectedId);
      addLine({
        product_id: product.id,
        name: product.name,
        quantity: 1,
        stock: product.stock,
        max_quantity: original?.max_quantity || product.stock,
      });
      refreshMessage();
    });

    close.addEventListener('click', () => {
      host.hidden = true;
      const toggle = document.querySelector(`[data-order-edit-toggle][aria-controls="${CSS.escape(host.id)}"]`);
      toggle?.setAttribute('aria-expanded', 'false');
    });

    form.addEventListener('submit', (event) => {
      if (!linesNode.querySelector('.order-edit-line')) {
        event.preventDefault();
        refreshMessage();
        return;
      }
      saveButton.disabled = true;
      saveButton.textContent = 'Enregistrement…';
    });
  }

  async function openEditor(button, host, orderId, context) {
    if (!host.hidden) {
      host.hidden = true;
      button.setAttribute('aria-expanded', 'false');
      return;
    }

    host.hidden = false;
    host.classList.add('is-loading');
    host.innerHTML = '<div class="small text-secondary p-2">Chargement de la commande…</div>';
    button.setAttribute('aria-expanded', 'true');

    try {
      const response = await fetch(stateEndpoint(orderId), {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
        cache: 'no-store',
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.success) {
        renderLocked(host, payload.error || 'Impossible de charger la commande.');
        return;
      }
      renderEditor(host, payload, context);
      if (!payload.order?.editable) {
        button.disabled = true;
        button.textContent = 'Verrouillée';
      }
    } catch (error) {
      renderLocked(host, 'Connexion indisponible. Réessayez dans quelques secondes.');
    } finally {
      host.classList.remove('is-loading');
    }
  }

  function setupServerEditors() {
    document.querySelectorAll('.server-order-card[data-order-state="waiting"]').forEach((card) => {
      const actions = card.querySelector('.server-order-actions');
      const orderId = findServerOrderId(card);
      if (!actions || !orderId || card.querySelector('[data-order-edit-toggle]')) return;

      const button = createToggle('✎ Modifier');
      const host = makeHost(orderId, 'server');
      host.id = `serverOrderEdit${orderId}`;
      button.setAttribute('aria-controls', host.id);
      actions.prepend(button);
      card.appendChild(host);
      button.addEventListener('click', () => openEditor(button, host, orderId, 'server'));

      const observer = new MutationObserver(() => {
        const editable = card.dataset.orderState === 'waiting';
        button.hidden = !editable;
        if (!editable) {
          host.hidden = true;
          button.setAttribute('aria-expanded', 'false');
        }
      });
      observer.observe(card, { attributes: true, attributeFilter: ['data-order-state'] });
    });
  }

  function setupCashierEditor() {
    const panel = document.getElementById('paymentPanel');
    if (!panel || panel.querySelector('[data-order-edit-toggle]')) return;
    const orderId = panel.querySelector('input[name="order_id"]')?.value || '';
    const title = panel.querySelector('.cashier-pay-title');
    const summary = panel.querySelector('.cashier-order-summary');
    if (!orderId || !title || !summary) return;

    const button = createToggle('✎ Modifier');
    const host = makeHost(orderId, 'cashier');
    host.id = `cashierOrderEdit${orderId}`;
    button.setAttribute('aria-controls', host.id);
    title.appendChild(button);
    summary.insertAdjacentElement('afterend', host);
    button.addEventListener('click', () => openEditor(button, host, orderId, 'cashier'));
  }

  setupServerEditors();
  setupCashierEditor();
})();
