/* BAR_PRO · Cockpit caissière 2026
 * Les soldes, droits, paiements, bons et transferts sont vérifiés côté Flask.
 * Aucune mutation optimiste de vente ou de stock n'est autorisée.
 */
(() => {
  'use strict';
  const root = document.getElementById('cashierCockpit');
  if (!root) return;
  const $ = id => document.getElementById(id);
  const barId = Number(root.dataset.barId);
  const api = {
    orders: root.dataset.ordersUrl,
    stock: root.dataset.stockUrl,
    payment: root.dataset.payUrl || '/api/caisse/encaisser',
    request: root.dataset.requestUrl,
    receipts: root.dataset.receiptsUrl,
    receive: root.dataset.receiveUrl,
    inventory: root.dataset.inventoryUrl,
    balance: root.dataset.balanceUrl,
  };
  const locale = 'fr-FR';
  const currency = root.dataset.currency || 'XAF';
  const money = value => `${new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(value)} FCFA`;
  const state = {
    orders: [], stock: [], transfers: [], selectedId: null, filter: 'all',
    inFlight: false, paymentIntent: null, changedPayment: false,
    lastDue: null, failed: false, stockConfigured: false, autoAdvanceInitialized: false,
    inventoryVersion: null, inventorySessionId: null, expectedCash: null,
  };
  const form = $('paymentForm');
  const inputs = [$('cashAmount'), $('mtnAmount'), $('orangeAmount')];
  const submit = $('confirmPayment');
  const receiptLink = $('receiptLink');
  const voucherLink = $('voucherLink');
  let notifyTimer = null;
  let refreshing = false;
  let restockIntent = null;
  let receiveIntent = null;

  function message(text, kind = 'info') {
    const box = $('cockpitAlert');
    if (notifyTimer) window.clearTimeout(notifyTimer);
    box.textContent = text;
    box.dataset.type = kind;
    box.hidden = false;
    notifyTimer = window.setTimeout(() => { box.hidden = true; }, kind === 'error' ? 12000 : 6500);
  }

  function randomKey() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    const values = new Uint8Array(16);
    if (!window.crypto || !window.crypto.getRandomValues) throw Error('Un navigateur sécurisé est nécessaire pour enregistrer un paiement.');
    window.crypto.getRandomValues(values);
    return Array.from(values, v => v.toString(16).padStart(2, '0')).join('');
  }

  function withinBar(url) {
    if (!url) throw Error('Route Flask non configurée pour cette opération.');
    const resolved = new URL(url, window.location.href);
    if (resolved.origin !== window.location.origin) throw Error('URL de service hors du domaine BAR_PRO.');
    return resolved.href;
  }

  async function fetchJson(url, { method = 'GET', payload, signal } = {}) {
    const headers = { Accept: 'application/json' };
    const init = { method, credentials: 'same-origin', cache: 'no-store', headers, signal };
    if (method !== 'GET') {
      headers['Content-Type'] = 'application/json';
      headers['X-CSRFToken'] = $('cockpitCsrf').value;
      headers['X-Requested-With'] = 'XMLHttpRequest';
      init.body = JSON.stringify(payload);
    }
    const response = await fetch(withinBar(url), init);
    const type = response.headers.get('content-type') || '';
    if (!type.includes('application/json')) {
      throw Error(response.redirected ? 'Session expirée. Reconnectez-vous.' : 'Réponse inattendue du serveur Flask.');
    }
    const result = await response.json();
    if (!response.ok || result.success === false) {
      throw Error(result.message || result.error?.message || `Requête refusée (${response.status}).`);
    }
    const data = result.data || result;
    if (data && typeof data === 'object') data.message = result.message || data.message;
    return data;
  }

  function integerInput(node) {
    const value = node.value.trim();
    if (!/^\d+$/.test(value)) return null;
    const number = Number(value);
    return Number.isSafeInteger(number) && number >= 0 ? number : null;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function ownOrder() { return state.orders.find(x => Number(x.id) === state.selectedId) || null; }
  function dueOf(order) { const value = Number(order?.amount_due); return Number.isSafeInteger(value) && value >= 0 ? value : null; }
  function payable(order) {
    return order && ['CONFIRMED', 'SERVED'].includes(order.status) &&
      ['UNPAID', 'PARTIAL'].includes(order.payment_status) && !order.pending_delivery && dueOf(order) > 0;
  }
  function voucherOrder(order) { return Boolean(order?.has_open_voucher || order?.voucher_status === 'ACTIVE' || order?.state === 'voucher'); }
  function changeVisible(name, show) { $(name).hidden = !show; }

  function renderOrders() {
    const list = $('ordersList');
    list.replaceChildren();
    const filtered = state.orders.filter(o => {
      if (state.filter === 'voucher') return voucherOrder(o);
      if (state.filter === 'to_pay') return payable(o);
      return true;
    });
    $('queueCount').textContent = String(filtered.length);
    if (!filtered.length) {
      list.appendChild(element('p', 'cc-empty', 'Aucune commande pour ce filtre.'));
      return;
    }
    const fragment = document.createDocumentFragment();
    filtered.forEach(order => {
      const button = element('button', 'cc-order' + (Number(order.id) === state.selectedId ? ' is-active' : ''));
      button.type = 'button';
      button.setAttribute('role', 'option');
      button.setAttribute('aria-selected', String(Number(order.id) === state.selectedId));
      const line = element('div', 'd-flex justify-content-between gap-2');
      const left = element('div', 'min-w-0');
      left.appendChild(element('div', 'cc-order-title', order.display_name || order.reference || 'Commande'));
      left.appendChild(element('div', 'cc-order-sub', `${order.server_name || 'Comptoir'} · ${order.table || 'Sans table'} · ${order.reference || ''}`));
      let tag = 'À livrer', css = 'is-waiting';
      if (voucherOrder(order)) { tag = 'Bon en cours'; css = 'is-voucher'; }
      else if (payable(order)) { tag = order.payment_status === 'PARTIAL' ? 'Paiement partiel' : 'À encaisser'; css = 'is-to-pay'; }
      else if (order.payment_status === 'PAID') { tag = 'Payée'; css = 'is-paid'; }
      left.appendChild(element('span', `cc-status ${css}`, tag));
      line.appendChild(left);
      line.appendChild(element('strong', 'cc-order-amount', money(dueOf(order) ?? 0)));
      button.appendChild(line);
      button.addEventListener('click', () => selectOrder(Number(order.id)));
      fragment.appendChild(button);
    });
    list.appendChild(fragment);
  }

  function selectOrder(orderId) {
    if (state.inFlight) return;
    const order = state.orders.find(x => Number(x.id) === orderId);
    if (!order) return;
    state.selectedId = orderId;
    state.paymentIntent = null;
    state.changedPayment = false;
    state.autoAdvanceInitialized = false;
    state.failed = false;
    state.lastDue = dueOf(order);
    inputs.forEach(input => { input.value = '0'; });
    $('issueVoucher').checked = false;
    $('retainAdvance').checked = false;
    receiptLink.hidden = true;
    voucherLink.hidden = true;
    const name = order.display_name || order.reference || 'Facture';
    $('invoiceName').textContent = name;
    $('invoiceRef').textContent = `${order.reference || ''} · ${order.server_name || 'Comptoir'}`;
    const lines = $('invoiceLines');
    lines.replaceChildren();
    (order.lines || []).forEach(line => {
      const li = element('li');
      li.appendChild(element('span', '', `${line.quantity} × ${line.name}`));
      li.appendChild(element('span', '', line.amount != null ? money(Number(line.amount)) : ''));
      lines.appendChild(li);
    });
    if (!lines.children.length) lines.appendChild(element('li', '', 'Aucun détail disponible.'));
    $('amountDue').replaceChildren(document.createTextNode(`${new Intl.NumberFormat(locale).format(state.lastDue ?? 0)} `), element('span', '', 'FCFA'));
    $('paymentFields').disabled = !payable(order);
    $('orderNotice').textContent = payable(order) ? 'Les montants sont contrôlés par le serveur avant validation.' :
      order.pending_delivery ? 'Livraison à confirmer par la caisse avant tout paiement.' :
      voucherOrder(order) ? 'Bon de monnaie ou avance en cours : consulter la caisse classique.' :
      'Commande non encaissable dans son état actuel.';
    $('addSuborder').href = `${window.location.origin}/bars/${barId}/orders/${encodeURIComponent(orderId)}/suborders/new`;
    $('addSuborder').setAttribute('aria-disabled', 'false');
    recalculate();
    renderOrders();
  }

  function recalculate() {
    const order = ownOrder();
    const due = state.lastDue;
    const amounts = inputs.map(integerInput);
    const validAmounts = amounts.every(x => x !== null);
    const [cash, mtn, orange] = validAmounts ? amounts : [0, 0, 0];
    const total = cash + mtn + orange;
    const safeTotal = Number.isSafeInteger(total);
    const mobile = mtn + orange;
    const dueAfterMobile = due === null ? null : Math.max(0, due - mobile);
    const extraCash = dueAfterMobile === null ? 0 : Math.max(0, cash - dueAfterMobile);
    const balance = due === null ? 0 : total - due;
    const issue = $('issueVoucher').checked;
    const mobileOver = due !== null && mobile > due;
    const canVoucher = payable(order) && validAmounts && extraCash > 0 && !mobileOver;
    $('issueVoucher').disabled = !canVoucher;
    if (!canVoucher) $('issueVoucher').checked = false;
    const canAdvance = canVoucher && order?.is_counter === true;
    if (canAdvance && !state.autoAdvanceInitialized) {
      $('retainAdvance').checked = true;
      state.autoAdvanceInitialized = true;
    }
    $('advanceSwitchRow').hidden = !canAdvance;
    $('retainAdvance').disabled = !canAdvance;
    if (!canAdvance) $('retainAdvance').checked = false;
    // On a counter tab the default is to retain excess cash until explicit closure.
    // The cashier can choose returning it or issuing a portable BM voucher instead.
    const advance = $('retainAdvance').checked && canAdvance;
    if (advance && $('issueVoucher').checked) $('issueVoucher').checked = false;
    if (cash > 0 && dueAfterMobile === 0) {
      $('issueVoucher').disabled = true;
      $('retainAdvance').disabled = true;
    }
    $('totalGiven').textContent = money(safeTotal ? total : 0);
    $('differenceTitle').textContent = balance >= 0 ? 'Monnaie à rendre' : 'Reste à régler';
    const difference = Math.abs(balance);
    $('changeAmount').textContent = money(difference);
    $('changeAmount').className = balance >= 0 ? 'is-positive' : 'is-negative';
    const voucher = $('issueVoucher').checked ? extraCash : 0;
    changeVisible('voucherRow', voucher > 0);
    $('voucherAmount').textContent = money(voucher);
    const invalid = !validAmounts || !safeTotal || mobileOver || (cash > 0 && dueAfterMobile === 0) || total <= 0 || state.inFlight || !payable(order) || state.failed;
    submit.disabled = invalid;
    if (!order) $('paymentHint').textContent = 'Sélectionnez une commande livrée.';
    else if (!validAmounts || !safeTotal) $('paymentHint').textContent = 'Montants entiers, positifs ou nuls uniquement.';
    else if (mobileOver || (cash > 0 && dueAfterMobile === 0)) $('paymentHint').textContent = 'Le Mobile Money couvre déjà tout le solde : aucune espèce supplémentaire ne peut être encaissée sur cette facture.';
    else if (total < due) $('paymentHint').textContent = `Paiement partiel : ${money(due - total)} restera dû sur la facture.`;
    else if (advance) $('paymentHint').textContent = `Avance de ${money(extraCash)} conservée : facture comptoir toujours ouverte pour d'autres produits.`;
    else if (voucher > 0) $('paymentHint').textContent = `Bon traçable de ${money(voucher)} à imprimer pour le client après validation.`;
    else if (extraCash > 0) $('paymentHint').textContent = `La caissière confirme avoir rendu ${money(extraCash)} en espèces.`;
    else $('paymentHint').textContent = 'Le solde actuel est couvert. La confirmation serveur reste obligatoire.';
  }

  async function refreshOrders({ silent = false } = {}) {
    if (!api.orders || document.hidden || window.BarProWorkspaceInactive || refreshing) return;
    refreshing = true;
    $('ordersList').setAttribute('aria-busy', 'true');
    try {
      const result = await fetchJson(api.orders);
      const orders = Array.isArray(result.orders) ? result.orders : Array.isArray(result.data?.orders) ? result.data.orders : [];
      state.orders = orders;
      const selected = ownOrder();
      if (state.selectedId !== null && !selected) {
        state.selectedId = null;
        $('paymentFields').disabled = true;
        $('orderNotice').textContent = 'Cette commande n’apparaît plus dans la file. Vérifiez son statut avant de poursuivre.';
        submit.disabled = true;
      } else if (selected && state.lastDue !== dueOf(selected)) {
        // Never apply a new balance to an in-progress payment silently.
        state.failed = true;
        $('paymentHint').textContent = 'Le montant dû a changé. Sélectionnez à nouveau la facture avant de payer.';
        submit.disabled = true;
      }
      renderOrders();
    } catch (error) {
      if (!silent) message(error.message, 'error');
    } finally {
      refreshing = false;
      $('ordersList').setAttribute('aria-busy', 'false');
    }
  }

  async function submitPayment(event) {
    event.preventDefault();
    const order = ownOrder();
    if (!payable(order) || state.inFlight || state.failed) return;
    const values = inputs.map(integerInput);
    if (values.some(v => v === null)) return;
    const [cash, mtn, orange] = values;
    const due = dueOf(order);
    if (due === null || mtn + orange > due || cash + mtn + orange <= 0) return;
    const extraCash = Math.max(0, cash - Math.max(0, due - mtn - orange));
    const retainAdvance = $('retainAdvance').checked && order?.is_counter === true;
    let confirmation = 'Confirmer les montants réellement reçus ?';
    if (cash + mtn + orange < due) confirmation = `Paiement partiel : ${money(due - cash - mtn - orange)} restera dû. Confirmer ?`;
    if (extraCash > 0 && retainAdvance) confirmation = `Conserver une avance de ${money(extraCash)} et laisser la facture ouverte pour de nouveaux produits ?`;
    else if (extraCash > 0 && !$('issueVoucher').checked) confirmation = `Confirmez-vous avoir rendu ${money(extraCash)} en espèces au client ?`;
    if (!window.confirm(confirmation)) return;
    try {
      if (!state.paymentIntent) state.paymentIntent = randomKey();
    } catch (error) { message(error.message, 'error'); return; }
    const payload = {
      bar_id: barId,
      order_id: state.selectedId,
      expected_due: due,
      especes: cash,
      mtn: mtn,
      orange: orange,
      cash_change_given: ($('issueVoucher').checked || retainAdvance) ? 0 : extraCash,
      issue_voucher: $('issueVoucher').checked,
      retain_advance: retainAdvance,
      idempotency_key: state.paymentIntent,
    };
    state.inFlight = true;
    recalculate();
    const button = $('confirmPayment');
    button.textContent = 'Validation en cours…';
    try {
      const data = await fetchJson(api.payment, { method: 'POST', payload });
      const voucher = data.voucher_url || null;
      const receipt = data.receipt_url || null;
      // Only a server-confirmed transaction changes the financial status.
      if (receipt) { receiptLink.href = withinBar(receipt); receiptLink.hidden = false; }
      if (voucher) { voucherLink.href = withinBar(voucher); voucherLink.hidden = false; }
      message(data.message || (data.payment_finalized ? 'Encaissement confirmé.' : 'Paiement partiel enregistré.'), 'success');
      state.inFlight = false;
      state.paymentIntent = null;
      state.changedPayment = false;
      state.failed = false;
      await Promise.all([refreshOrders({ silent: true }), refreshBalance({ silent: true })]);
      const wasFinished = data.payment_finalized === true && !data.order_open;
      if (wasFinished) {
        // Do not close the cockpit; printing must remain reachable. A modal may
        // close only when there is neither an outstanding voucher nor an advance.
        $('paymentFields').disabled = true;
        submit.disabled = true;
        $('paymentHint').textContent = voucher ? 'Imprimez le bon avant de terminer.' : 'Facture encaissée. Sélectionnez la prochaine commande.';
        document.dispatchEvent(new CustomEvent('barpro:workspace-task-complete', {
          detail: { task: 'payment', voucherRequired: Boolean(voucher) }
        }));
      } else if (data.order_open) {
        $('paymentHint').textContent = 'Commande toujours ouverte : les ajouts et le bon d’avance restent disponibles dans la caisse.';
      }
    } catch (error) {
      // Never change the intent after a timeout: the server might have committed.
      state.failed = true;
      message(`${error.message} Vérifiez le résultat dans la caisse avant de réessayer.`, 'error');
    } finally {
      state.inFlight = false;
      button.textContent = 'Confirmer l’encaissement';
      recalculate();
    }
  }

  function stockLevel(item) {
    const qty = Number(item.stock || 0);
    const threshold = Math.max(1, Number(item.threshold || 1));
    const percent = Math.max(0, Math.min(100, Math.round(100 * qty / (threshold * 3))));
    return { qty, percent, cls: qty <= 0 ? 'is-critical' : qty <= threshold ? 'is-low' : '' };
  }

  function renderStock() {
    const list = $('stockList'); list.replaceChildren();
    const products = state.stock;
    if (!products.length) {
      list.appendChild(element('p', 'cc-empty', state.stockConfigured ? 'Aucun produit actif.' : 'Stock caisse non encore configuré.'));
    }
    products.forEach(item => {
      const level = stockLevel(item);
      const article = element('article', `cc-stock-item ${level.qty === 0 ? 'is-empty' : ''}`);
      const head = element('div', 'cc-stock-head');
      head.appendChild(element('strong', '', item.name));
      head.appendChild(element('span', '', `${new Intl.NumberFormat(locale).format(level.qty)} bout.`));
      article.appendChild(head);
      const bar = element('div', 'cc-progress');
      bar.setAttribute('role', 'progressbar');
      bar.setAttribute('aria-valuemin', '0');
      bar.setAttribute('aria-valuemax', '100');
      bar.setAttribute('aria-valuenow', String(level.percent));
      bar.setAttribute('aria-label', `Stock de ${item.name}`);
      const fill = element('span', level.cls); fill.style.width = `${level.percent}%`; bar.appendChild(fill);
      article.appendChild(bar);
      article.appendChild(element('small', '', `Seuil d’alerte : ${new Intl.NumberFormat(locale).format(item.threshold || 0)} bouteilles`));
      list.appendChild(article);
    });
    const select = $('restockProduct');
    const previous = select.value;
    select.replaceChildren(new Option('Choisir un produit…', ''));
    state.stock.forEach(item => select.add(new Option(item.name, String(item.id))));
    if (state.stock.some(item => String(item.id) === previous)) select.value = previous;
    $('openRestock').disabled = !state.stock.length || !api.request;
    $('openReceive').disabled = !state.stockConfigured || !api.receipts || !api.receive;
  }

  async function refreshStock({ silent = false } = {}) {
    if (!api.stock || document.hidden || window.BarProWorkspaceInactive) return;
    try {
      const data = await fetchJson(api.stock);
      state.stock = Array.isArray(data.items) ? data.items : [];
      state.stockConfigured = data.warehouse_live === true;
      $('stockSource').textContent = state.stockConfigured
        ? 'Stock réel CAISSE en bouteilles · MAIN → TRANSIT → CAISSE.'
        : 'Stock opérationnel historique en bouteilles ; réception multi-magasins désactivée jusqu’à activation du magasin.';
      renderStock();
    } catch (error) { if (!silent) message(error.message, 'error'); }
  }

  async function submitRestock(event) {
    event.preventDefault();
    const productId = Number($('restockProduct').value);
    const quantity = integerInput($('restockQty'));
    if (!productId || !quantity || quantity <= 0) {
      message('Sélectionnez un produit et une quantité valide.', 'error'); return;
    }
    const button = $('submitRestock'); button.disabled = true;
    try {
      if (!restockIntent) restockIntent = randomKey();
      const data = await fetchJson(api.request, {
        method: 'POST', payload: {
          bar_id: barId, product_id: productId, quantity,
          note: $('restockNote').value.slice(0, 200), idempotency_key: restockIntent,
        }
      });
      message(data.message || 'Demande transmise au magasin principal.', 'success');
      restockIntent = null;
      $('restockForm').reset();
      const modal = window.bootstrap?.Modal?.getInstance($('restockModal'));
      modal?.hide();
    } catch (error) { message(error.message, 'error'); }
    finally { button.disabled = false; }
  }

  function renderReceiptLines() {
    const id = Number($('transferSelect').value);
    const transfer = state.transfers.find(row => Number(row.id) === id);
    const holder = $('receiptLines'); holder.replaceChildren();
    if (!transfer) return;
    transfer.lines.forEach(item => {
      const remaining = Math.max(0, Number(item.remaining || 0));
      const line = element('div', 'cc-receipt-row');
      line.appendChild(element('strong', '', item.name));
      line.appendChild(element('span', '', `En transit : ${remaining} bouteilles`));
      const input = element('input', 'form-control');
      input.type = 'number'; input.min = '0'; input.max = String(remaining);
      input.step = '1'; input.required = true; input.inputMode = 'numeric';
      input.value = String(remaining); input.dataset.productId = String(item.product_id);
      input.setAttribute('aria-label', `Quantité reçue de ${item.name}`);
      line.appendChild(input); holder.appendChild(line);
    });
  }

  async function loadReceipts() {
    if (!state.stockConfigured) return;
    try {
      const data = await fetchJson(api.receipts);
      state.transfers = Array.isArray(data.transfers) ? data.transfers : [];
      const select = $('transferSelect'); select.replaceChildren(new Option('Choisir un transfert expédié…', ''));
      state.transfers.forEach(transfer => select.add(new Option(`${transfer.reference} · ${transfer.status}`, String(transfer.id))));
      renderReceiptLines();
      $('submitReceive').disabled = !state.transfers.length;
    } catch (error) { message(error.message, 'error'); }
  }

  async function submitReceive(event) {
    event.preventDefault();
    const id = Number($('transferSelect').value);
    const rows = Array.from($('receiptLines').querySelectorAll('[data-product-id]'));
    const quantities = {};
    for (const input of rows) {
      const qty = integerInput(input);
      const max = Number(input.max);
      if (qty === null || qty > max) { message('Quantité reçue invalide ou supérieure au transit.', 'error'); return; }
      if (qty > 0) quantities[input.dataset.productId] = qty;
    }
    if (!id || !Object.keys(quantities).length) { message('Saisissez au moins une quantité physiquement reçue.', 'error'); return; }
    const button = $('submitReceive'); button.disabled = true;
    try {
      if (!receiveIntent) receiveIntent = randomKey();
      const data = await fetchJson(api.receive, { method: 'POST', payload: {
        bar_id: barId, transfer_id: id, quantities, idempotency_key: receiveIntent,
      }});
      receiveIntent = null;
      message(data.message || 'Réception enregistrée au comptoir.', 'success');
      window.bootstrap?.Modal?.getInstance($('receiveModal'))?.hide();
      await refreshStock({ silent: true });
    } catch (error) { message(error.message, 'error'); }
    finally { button.disabled = false; }
  }

  function renderInventory() {
    const body = $('inventoryRows'); body.replaceChildren();
    state.stock.forEach(item => {
      const row = element('tr');
      const current = Number(item.stock || 0);
      const title = element('td', '', item.name);
      const theoretical = element('td', 'text-end', new Intl.NumberFormat(locale).format(current));
      const physical = element('td');
      const input = element('input', 'form-control cc-inventory-count');
      input.type = 'number'; input.inputMode = 'numeric'; input.step = '1'; input.min = '0';
      input.dataset.productId = String(item.id); input.dataset.theoretical = String(current);
      input.placeholder = 'À compter'; input.setAttribute('aria-label', `Stock physique de ${item.name} en bouteilles`);
      physical.appendChild(input);
      const difference = element('td', 'text-end cc-delta-zero', '—');
      input.addEventListener('input', () => {
        const counted = input.value.trim() === '' ? null : integerInput(input);
        const delta = counted === null ? null : counted - current;
        difference.textContent = delta === null ? '—' : `${delta > 0 ? '+' : ''}${new Intl.NumberFormat(locale).format(delta)}`;
        difference.className = 'text-end ' + (delta === null || delta === 0 ? 'cc-delta-zero' : delta > 0 ? 'cc-delta-positive' : 'cc-delta-negative');
      });
      row.append(title, theoretical, physical, difference);
      body.appendChild(row);
    });
  }

  async function refreshBalance({ silent = false } = {}) {
    if (!api.balance) return;
    try {
      const data = await fetchJson(api.balance);
      const values = data.financial || data;
      const fields = { sales: 'sales', collected: 'collected', expenses: 'expenses', debts: 'debts' };
      Object.keys(fields).forEach(key => {
        const node = root.querySelector(`[data-kpi="${key}"]`);
        const amount = Number(values[key]);
        if (node && Number.isFinite(amount)) node.replaceChildren(document.createTextNode(`${money(amount)} `));
      });
      $('closingRevenue').textContent = money(Number(values.collected || 0));
      $('closingExpenses').textContent = money(Number(values.expenses || 0));
      $('closingDebts').textContent = money(Number(values.debts || 0));
      if (Number.isFinite(Number(values.expected_cash))) state.expectedCash = Number(values.expected_cash);
      if (values.session_id != null) state.inventorySessionId = Number(values.session_id);
      updateCashDifference();
    } catch (error) { if (!silent) message(error.message, 'error'); }
  }

  function updateCashDifference() {
    const value = $('countedCash').value.trim();
    const counted = value === '' ? null : integerInput($('countedCash'));
    const difference = counted === null || state.expectedCash === null ? null : counted - state.expectedCash;
    const node = $('closingDifference');
    node.textContent = difference === null ? '—' : money(difference);
    node.className = difference === null || difference === 0 ? '' : difference > 0 ? 'cc-delta-positive' : 'cc-delta-negative';
    $('inventoryStatus').textContent = difference === null ? 'Le comptage est un aperçu ; il ne clôture pas la session.' :
      `Espèces attendues : ${money(state.expectedCash)} · écart physique : ${money(difference)}. Tout écart exige une justification dans le workflow officiel.`;
  }

  function openInventory() {
    renderInventory();
    void refreshBalance({ silent: true });
  }

  document.querySelectorAll('[data-queue-filter]').forEach(btn => btn.addEventListener('click', () => {
    state.filter = btn.dataset.queueFilter;
    document.querySelectorAll('[data-queue-filter]').forEach(item => {
      item.classList.toggle('is-active', item === btn);
      item.setAttribute('aria-pressed', String(item === btn));
    });
    renderOrders();
  }));

  inputs.forEach(input => input.addEventListener('input', () => { state.changedPayment = true; recalculate(); }));
  $('issueVoucher').addEventListener('change', () => {
    if ($('issueVoucher').checked) $('retainAdvance').checked = false;
    recalculate();
  });
  $('retainAdvance').addEventListener('change', () => {
    if ($('retainAdvance').checked) $('issueVoucher').checked = false;
    recalculate();
  });
  $('paymentForm').addEventListener('submit', submitPayment);
  $('refreshOrders').addEventListener('click', () => { state.failed = false; if (ownOrder()) selectOrder(state.selectedId); void refreshOrders(); });
  $('refreshStock').addEventListener('click', () => { void refreshStock(); });
  $('restockForm').addEventListener('submit', submitRestock);
  $('receiveForm').addEventListener('submit', submitReceive);
  $('receiveModal').addEventListener('show.bs.modal', loadReceipts);
  $('inventoryModal').addEventListener('show.bs.modal', openInventory);
  $('countedCash').addEventListener('input', updateCashDifference);
  // Preserve the primary POS: never install a beforeunload handler that blocks receipt printing.
  window.addEventListener('barpro:workspace-visibility', event => {
    if (event.detail?.visible) {
      void refreshOrders({ silent: true });
      void refreshStock({ silent: true });
    }
  });
  const refreshTimer = window.setInterval(() => {
    if (!document.hidden && !window.BarProWorkspaceInactive && !state.inFlight) void refreshOrders({ silent: true });
  }, 12000);
  window.addEventListener('pagehide', () => window.clearInterval(refreshTimer));

  // Initial render works with the real, tenant-scoped live-order endpoint.
  void refreshOrders();
  void refreshStock();
  void refreshBalance({ silent: true });
  recalculate();
})();