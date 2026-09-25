(function () {
  'use strict';

  function money(value) {
    const number = Number(value || 0);
    return new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 }).format(Number.isFinite(number) ? number : 0);
  }

  function workspaceBase() {
    const match = window.location.pathname.match(/^\/bars\/(\d+)\/cashier\/workspace/);
    if (!match) return null;
    return `/bars/${match[1]}/cashier/change-vouchers/`;
  }

  function addVoucherNavigation() {
    const base = workspaceBase();
    if (!base) return;
    const head = document.querySelector('.cashier-head-actions');
    if (head && !head.querySelector('[data-change-vouchers-link]')) {
      const link = document.createElement('a');
      link.href = base;
      link.className = 'btn btn-light border';
      link.dataset.changeVouchersLink = '1';
      link.textContent = '🎟 Bons de monnaie';
      head.appendChild(link);
    }

    const form = document.getElementById('cashierPaymentForm');
    const orderId = form?.querySelector('input[name="order_id"]')?.value;
    const paymentPanel = document.getElementById('paymentPanel');
    if (paymentPanel && orderId && !paymentPanel.querySelector('[data-use-voucher]')) {
      const link = document.createElement('a');
      link.href = `${base}?order_id=${encodeURIComponent(orderId)}`;
      link.className = 'btn btn-outline-primary w-100 mt-2';
      link.dataset.useVoucher = '1';
      link.textContent = '🎟 Utiliser un bon de monnaie';
      const paymentForm = document.getElementById('cashierPaymentForm');
      paymentForm?.insertAdjacentElement('beforebegin', link);
    }
  }

  function enhanceCashPayment() {
    const form = document.getElementById('cashierPaymentForm');
    const base = workspaceBase();
    if (!form || !base || form.dataset.changeVoucherReady === '1') return;

    const presented = document.getElementById('cashierPresentedInput');
    const applied = document.getElementById('cashierApplied');
    const presentedBox = document.getElementById('cashierPresented');
    if (!presented || !applied || !presentedBox) return;

    form.dataset.changeVoucherReady = '1';
    const currency = presented.closest('.input-group')?.querySelector('.input-group-text')?.textContent?.trim() || 'XAF';

    const panel = document.createElement('div');
    panel.className = 'change-voucher-payment mt-3';
    panel.hidden = true;
    panel.innerHTML = `
      <div class="change-voucher-payment-head">
        <div><strong>Monnaie réellement rendue</strong><small>Si vous n'avez pas toute la monnaie, saisissez seulement ce que vous rendez au client.</small></div>
      </div>
      <div class="input-group input-group-lg mt-2">
        <input class="form-control" id="cashActualChange" name="actual_change_given" type="number" min="0" step="1" inputmode="numeric" value="0">
        <span class="input-group-text">${currency}</span>
      </div>
      <div class="change-voucher-preview mt-2">
        <div><span>Monnaie totale due</span><strong id="cashTotalChange">0 ${currency}</strong></div>
        <div class="voucher-due-row"><span>Bon de monnaie à créer</span><strong id="cashVoucherDue">0 ${currency}</strong></div>
      </div>
      <div class="change-voucher-client mt-2" hidden>
        <div class="row g-2">
          <div class="col-7"><label class="form-label">Nom du client <span class="text-secondary">(facultatif)</span></label><input class="form-control" name="voucher_customer_name" maxlength="160" autocomplete="off"></div>
          <div class="col-5"><label class="form-label">Téléphone <span class="text-secondary">(facultatif)</span></label><input class="form-control" name="voucher_customer_phone" maxlength="32" autocomplete="off" inputmode="tel"></div>
        </div>
        <div class="change-voucher-warning mt-2">Le reliquat n'est pas une recette : il restera une dette du bar envers le client jusqu'à utilisation ou remboursement du bon.</div>
      </div>`;
    presentedBox.insertAdjacentElement('afterend', panel);

    const actual = panel.querySelector('#cashActualChange');
    const totalChangeNode = panel.querySelector('#cashTotalChange');
    const voucherNode = panel.querySelector('#cashVoucherDue');
    const clientBox = panel.querySelector('.change-voucher-client');
    let actualManuallyEdited = false;

    const style = document.createElement('style');
    style.textContent = `
      .change-voucher-payment{border:1px solid #d8e5df;border-radius:13px;padding:12px;background:#fbfdfc}
      .change-voucher-payment-head strong,.change-voucher-payment-head small{display:block}.change-voucher-payment-head strong{font-size:.82rem}.change-voucher-payment-head small{font-size:.68rem;color:var(--muted);margin-top:2px}
      .change-voucher-preview{border-radius:10px;overflow:hidden;border:1px solid #e0e8e4}.change-voucher-preview>div{display:flex;justify-content:space-between;gap:12px;padding:8px 9px;font-size:.74rem}.change-voucher-preview>div+div{border-top:1px solid #e0e8e4}.change-voucher-preview span{color:var(--muted)}.change-voucher-preview strong{font-size:.8rem}.voucher-due-row.active{background:#fff4dd}.voucher-due-row.active strong{color:#8a5f0a;font-size:.92rem}
      .change-voucher-warning{font-size:.68rem;line-height:1.35;padding:8px 9px;border-radius:9px;background:#fff4dd;color:#78540b}.change-voucher-client .form-label{font-size:.69rem;margin-bottom:4px}
      @media(max-width:480px){.change-voucher-client .col-7,.change-voucher-client .col-5{width:100%}}
    `;
    document.head.appendChild(style);

    function method() {
      return form.querySelector('input[name="method"]:checked')?.value || 'CASH';
    }

    function refresh(resetActual) {
      const cashMode = method() === 'CASH';
      const received = Math.max(0, Number(presented.value || 0));
      const appliedAmount = Math.max(0, Number(applied.value || 0));
      const fullChange = Math.max(0, received - appliedAmount);
      panel.hidden = !cashMode || fullChange <= 0;
      if (!cashMode || fullChange <= 0) {
        actual.value = '0';
        actualManuallyEdited = false;
        clientBox.hidden = true;
        return;
      }

      if (resetActual || !actualManuallyEdited) actual.value = String(fullChange);
      let actuallyGiven = Math.max(0, Number(actual.value || 0));
      if (actuallyGiven > fullChange) {
        actuallyGiven = fullChange;
        actual.value = String(fullChange);
      }
      const voucherDue = Math.max(0, fullChange - actuallyGiven);
      totalChangeNode.textContent = `${money(fullChange)} ${currency}`;
      voucherNode.textContent = `${money(voucherDue)} ${currency}`;
      panel.querySelector('.voucher-due-row')?.classList.toggle('active', voucherDue > 0);
      clientBox.hidden = voucherDue <= 0;
    }

    actual.addEventListener('input', () => {
      actualManuallyEdited = true;
      refresh(false);
    });
    presented.addEventListener('input', () => setTimeout(() => refresh(true), 0));
    applied.addEventListener('input', () => setTimeout(() => refresh(true), 0));
    form.querySelectorAll('input[name="method"]').forEach((radio) => radio.addEventListener('change', () => setTimeout(() => refresh(true), 0)));

    form.addEventListener('submit', (event) => {
      if (method() !== 'CASH') return;
      const received = Math.max(0, Number(presented.value || 0));
      const appliedAmount = Math.max(0, Number(applied.value || 0));
      const fullChange = Math.max(0, received - appliedAmount);
      const actuallyGiven = Math.max(0, Number(actual.value || 0));
      if (actuallyGiven > fullChange) {
        event.preventDefault();
        actual.setCustomValidity('La monnaie rendue ne peut pas dépasser la monnaie due.');
        actual.reportValidity();
        actual.setCustomValidity('');
        return;
      }
      form.action = `${base}pay`;
    });

    refresh(true);
  }

  document.addEventListener('DOMContentLoaded', () => {
    addVoucherNavigation();
    enhanceCashPayment();
  });
})();
