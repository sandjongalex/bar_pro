(function () {
  'use strict';

  const form = document.getElementById('cashierPaymentForm');
  if (!form) return;

  const dueInput = document.getElementById('cashierDue');
  const appliedInput = document.getElementById('cashierApplied');
  const presentedInput = document.getElementById('cashierPresentedInput');
  const presentedBox = document.getElementById('cashierPresented');
  const singleAmount = document.getElementById('cashierSingleAmount');
  const changeNode = document.getElementById('cashierChange');
  const submit = document.getElementById('cashierPaymentSubmit');
  const mixedCash = document.getElementById('cashierMixedCash');

  if (!dueInput || !appliedInput || !presentedInput || !presentedBox || !changeNode || !submit) return;

  const due = Number(dueInput.value || 0);
  const currency = presentedInput.closest('.input-group')?.querySelector('.input-group-text')?.textContent?.trim() || 'XAF';
  const label = presentedBox.querySelector('label[for="cashierPresentedInput"]');
  const inputGroup = presentedInput.closest('.input-group');
  const originalSubmitText = submit.textContent;

  function money(value) {
    const number = Number(value || 0);
    return new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 }).format(Number.isFinite(number) ? number : 0);
  }

  function method() {
    return form.querySelector('input[name="method"]:checked')?.value || 'CASH';
  }

  function nextRounded(value) {
    const step = value < 2000 ? 500 : 1000;
    return Math.ceil((value + 1) / step) * step;
  }

  function suggestedAmounts(target) {
    const value = Math.max(0, Number(target || 0));
    if (!value) return [];
    const standard = [500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000];
    const rounded = nextRounded(value);
    const firstNote = standard.find((amount) => amount > value && amount >= rounded) || rounded;
    const secondNote = standard.find((amount) => amount > firstNote) || firstNote * 2;
    return [...new Set([value, rounded, firstNote, secondNote])].slice(0, 4);
  }

  const style = document.createElement('style');
  style.textContent = `
    .cash-presented-help{font-size:.7rem;color:var(--muted);margin-top:5px}
    .cash-presented-quick{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px;margin-top:9px}
    .cash-presented-quick button{min-height:42px;border:1px solid #d8e3de;border-radius:10px;background:#fff;color:var(--brand);font-size:.72rem;font-weight:850;padding:6px 4px}
    .cash-presented-quick button:hover,.cash-presented-quick button:focus{border-color:var(--brand);background:var(--brand-soft)}
    .cash-payment-result{margin-top:9px;border:1px solid #dce7e2;border-radius:12px;overflow:hidden;background:#f8fbf9}
    .cash-payment-result-row{display:flex;justify-content:space-between;gap:12px;padding:8px 10px;font-size:.76rem;border-bottom:1px solid #e7eeea}
    .cash-payment-result-row:last-child{border-bottom:0}.cash-payment-result-row span{color:var(--muted)}
    .cash-payment-result-row strong{font-size:.82rem;text-align:right}
    .cash-payment-state{margin-top:8px;padding:8px 10px;border-radius:10px;font-size:.7rem;font-weight:800;background:#eaf7ee;color:#176b36}
    .cash-payment-state.partial{background:#fff4dd;color:#8a5f0a}
    .cash-payment-state.waiting{background:#f2f5f3;color:#68756f}
    @media(max-width:480px){.cash-presented-quick{grid-template-columns:1fr 1fr}.cash-presented-quick button{min-height:46px;font-size:.78rem}}
  `;
  document.head.appendChild(style);

  if (label) label.textContent = 'Montant donné par le client';
  presentedInput.setAttribute('autocomplete', 'off');
  presentedInput.setAttribute('aria-describedby', 'cashPresentedHelp');

  const help = document.createElement('div');
  help.id = 'cashPresentedHelp';
  help.className = 'cash-presented-help';
  help.textContent = 'Saisissez la somme réellement remise par le client. La monnaie est calculée automatiquement.';
  inputGroup?.insertAdjacentElement('afterend', help);

  const quick = document.createElement('div');
  quick.className = 'cash-presented-quick';
  quick.setAttribute('aria-label', 'Montants rapides');
  help.insertAdjacentElement('afterend', quick);

  const result = document.createElement('div');
  result.className = 'cash-payment-result';
  result.innerHTML = `
    <div class="cash-payment-result-row"><span>À encaisser</span><strong id="cashAppliedPreview">0 ${currency}</strong></div>
    <div class="cash-payment-result-row"><span>Monnaie à rendre</span><strong id="cashChangePreview">0 ${currency}</strong></div>`;
  quick.insertAdjacentElement('afterend', result);

  const state = document.createElement('div');
  state.className = 'cash-payment-state waiting';
  state.id = 'cashPaymentState';
  result.insertAdjacentElement('afterend', state);

  const oldSummary = changeNode.closest('.cashier-order-summary');
  if (oldSummary) oldSummary.hidden = true;

  const appliedPreview = document.getElementById('cashAppliedPreview');
  const changePreview = document.getElementById('cashChangePreview');

  function renderQuick(target) {
    quick.innerHTML = '';
    suggestedAmounts(target).forEach((amount) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = money(amount);
      button.dataset.amount = String(amount);
      button.addEventListener('click', () => {
        presentedInput.value = String(amount);
        presentedInput.dispatchEvent(new Event('input', { bubbles: true }));
        presentedInput.focus();
      });
      quick.appendChild(button);
    });
  }

  function setApplied(value) {
    const normalized = Math.max(0, Math.min(due, Number(value || 0)));
    appliedInput.value = normalized > 0 ? String(normalized) : '';
    appliedInput.dispatchEvent(new Event('input', { bubbles: true }));
    return normalized;
  }

  function syncCash() {
    if (method() !== 'CASH') return;
    const received = Math.max(0, Number(presentedInput.value || 0));
    const applied = setApplied(Math.min(received, due));
    const change = Math.max(0, received - due);
    const remaining = Math.max(0, due - applied);

    if (singleAmount) singleAmount.hidden = true;
    if (label) label.textContent = 'Montant donné par le client';
    help.textContent = 'Saisissez la somme réellement remise par le client. La monnaie est calculée automatiquement.';
    appliedPreview.textContent = `${money(applied)} ${currency}`;
    changePreview.textContent = `${money(change)} ${currency}`;
    changeNode.textContent = `${money(change)} ${currency}`;

    state.classList.remove('partial', 'waiting');
    if (received <= 0) {
      state.classList.add('waiting');
      state.textContent = `Reste à payer : ${money(due)} ${currency}`;
      submit.disabled = true;
      submit.textContent = originalSubmitText;
    } else if (received < due) {
      state.classList.add('partial');
      state.textContent = `Paiement partiel · il restera ${money(remaining)} ${currency} à payer.`;
      submit.disabled = false;
      submit.textContent = `Encaisser ${money(applied)} ${currency}`;
    } else {
      state.textContent = 'Facture soldée après cet encaissement.';
      submit.disabled = false;
      submit.textContent = `Encaisser ${money(applied)} ${currency}`;
    }
    renderQuick(due);
  }

  function syncMixed() {
    if (method() !== 'MIXED') return;
    if (singleAmount) singleAmount.hidden = true;
    if (label) label.textContent = 'Espèces remises par le client';
    help.textContent = 'Pour un paiement mixte, indiquez ici les espèces physiquement remises afin de calculer la monnaie.';
    const target = Math.max(0, Number(mixedCash?.value || 0));
    const received = Math.max(0, Number(presentedInput.value || 0));
    const change = Math.max(0, received - target);
    appliedPreview.textContent = `${money(target)} ${currency} en espèces`;
    changePreview.textContent = `${money(change)} ${currency}`;
    changeNode.textContent = `${money(change)} ${currency}`;
    state.classList.remove('partial', 'waiting');
    state.textContent = `Espèces ${money(target)} ${currency} + Mobile Money pour compléter la facture.`;
    renderQuick(target);
  }

  function syncMode() {
    const current = method();
    const visibleCashTools = current === 'CASH' || current === 'MIXED';
    help.hidden = !visibleCashTools;
    quick.hidden = !visibleCashTools;
    result.hidden = !visibleCashTools;
    state.hidden = !visibleCashTools;

    if (current === 'CASH') {
      if (!presentedInput.value) presentedInput.value = String(due);
      syncCash();
    } else if (current === 'MIXED') {
      syncMixed();
    } else {
      if (singleAmount && current !== 'CREDIT') singleAmount.hidden = false;
      submit.disabled = false;
    }
  }

  presentedInput.addEventListener('input', () => {
    if (method() === 'CASH') syncCash();
    else if (method() === 'MIXED') syncMixed();
  });
  mixedCash?.addEventListener('input', syncMixed);
  form.querySelectorAll('input[name="method"]').forEach((radio) => radio.addEventListener('change', () => setTimeout(syncMode, 0)));

  form.addEventListener('submit', (event) => {
    if (method() !== 'CASH') return;
    const received = Math.max(0, Number(presentedInput.value || 0));
    if (received <= 0) {
      event.preventDefault();
      state.classList.add('waiting');
      state.textContent = 'Saisissez le montant donné par le client.';
      presentedInput.focus();
      return;
    }
    setApplied(Math.min(received, due));
  });

  syncMode();
})();
