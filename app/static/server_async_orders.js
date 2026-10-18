/* Async server POS only. Event delegation works for orders created after initial render.
   All stock/payment/ownership decisions stay in the existing Flask services. */
(function () {
  'use strict';

  const pos = document.querySelector('form#posForm.server-pos-layout[data-server-async="1"]');
  const api = window.BarProAsync;
  if (!pos || !api) return; // Native HTML form fallback is preserved.
  const barMatch = window.location.pathname.match(/^\/bars\/(\d+)\//);
  if (!barMatch) return;
  const barId = barMatch[1];
  const actorId = pos.dataset.serverUserId || 'user';
  const recoveryStorageKey = 'barpro:server-order-intent:' + barId + ':' + actorId;

  function pendingKey() {
    return pos.querySelector('[name="idempotency_key"]')?.value || '';
  }
  function rememberIntent(key) {
    try { window.sessionStorage.setItem(recoveryStorageKey, key); } catch (_) {}
  }
  function forgetIntent() {
    try { window.sessionStorage.removeItem(recoveryStorageKey); } catch (_) {}
  }
  function recalledKey() {
    try { return window.sessionStorage.getItem(recoveryStorageKey) || ''; }
    catch (_) { return ''; }
  }

  async function refreshOrders() {
    await window.BarProServerState?.refresh?.();
  }

  function completedOrder(result, { clearCart = true } = {}) {
    if (!result || !Number.isInteger(Number(result.id)) || !result.reference) {
      api.notify('Réponse incomplète : vérifiez la commande avant tout nouvel envoi.', 'error');
      return false;
    }
    if (clearCart) window.BarProServerCart?.reset?.();
    forgetIntent();
    void refreshOrders();
    return true;
  }

  async function recover(key, { clearCart = false } = {}) {
    if (!key) return false;
    try {
      const response = await api.request('/bars/' + barId + '/orders/async-intents/' + encodeURIComponent(key), {
        key: 'server:intent:' + barId + ':' + key,
        timeout: 8000
      });
      if (response?.success && response?.data?.state === 'COMPLETED') {
        api.notify('Commande confirmée sur le serveur : ' + response.data.reference, 'success');
        return completedOrder(response.data, { clearCart });
      }
    } catch (error) {
      // Absence / pending / offline does not prove the POST failed. NEVER mint a new
      // key and automatically retry a transaction while its result is uncertain.
    }
    return false;
  }

  function classify(form) {
    if (form === pos) return 'create';
    if (form.matches('[data-order-edit-form]') && form.closest('.server-order-edit-host')) return 'edit';
    if (form.closest('[data-pending-suborder]') &&
        form.querySelector('input[name="action"]')?.value === 'validate_suborder') return 'validate_suborder';
    const action = form.querySelector('input[name="action"]')?.value;
    if (!['note', 'cancel'].includes(action)) return null;
    if (form.closest('.server-order-inline-editor, .modal')) return action;
    return null;
  }

  // Capture before legacy submit handlers; do not disable or modify those
  // handlers for the non-JS fallback.
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const kind = classify(form);
    if (!kind) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!form.checkValidity()) {
      form.reportValidity?.();
      return;
    }
    if (kind === 'create' && !window.BarProServerCart?.hasItems?.()) {
      api.notify('Ajoutez au moins un produit avant d’envoyer la commande.', 'error');
      return;
    }

    const intent = kind === 'create' ? pendingKey() : '';
    if (kind === 'create' && !intent) {
      api.notify('Identifiant de tentative manquant : rechargez le formulaire avant de réessayer.', 'error');
      return;
    }
    if (intent) rememberIntent(intent);
    void (async () => {
      try {
        const response = await api.submit(form, event.submitter);
        if (response?.skipped) return;
        if (kind === 'create') {
          if (!completedOrder(response.data)) {
            await recover(intent, { clearCart: true });
          }
        } else {
          if (kind === 'validate_suborder') {
            form.closest('[data-pending-suborder]')?.remove();
          } else if (kind === 'edit') {
            const editor = form.closest('.server-order-edit-host');
            if (editor) {
              editor.hidden = true;
              editor.closest('[data-server-order-card]')?.classList.remove('is-editing');
            }
          } else {
            const container = form.closest('.server-order-inline-editor');
            if (container) {
              container.hidden = true;
              container.closest('[data-server-order-card]')?.classList.remove('is-editing');
            }
            const modal = form.closest('.modal');
            if (modal && window.bootstrap?.Modal) {
              window.bootstrap.Modal.getInstance(modal)?.hide();
            }
          }
          await refreshOrders();
        }
      } catch (error) {
        if (kind === 'create' && ['TIMEOUT', 'NETWORK', 'ABORT', 'FORMAT', 'REDIRECT'].includes(error.code)) {
          // Keep cart, invoice name and SAME idempotency key until recovery.
          await recover(intent, { clearCart: true });
        }
      }
    })();
  }, true);

  // Clicking modals on dynamically inserted cards is safe; existing inline
  // card actions keep their original behavior and register handlers once.
  window.addEventListener('online', () => {
    const key = recalledKey();
    if (key) void recover(key, { clearCart: key === pendingKey() });
  });
  const previousKey = recalledKey();
  if (previousKey) {
    // A page refresh must not silently mint a different intent for an uncertain
    // POST. Restore the same token before allowing the user to re-enter a cart.
    const input = pos.querySelector('[name="idempotency_key"]');
    if (input) input.value = previousKey;
    api.notify('Une commande précédente est à vérifier avant tout nouvel envoi.', 'info');
    void recover(previousKey, { clearCart: true });
  }
})();
