/* Touch feedback for the server POS only; no changes to the cart business logic. */
(() => {
  'use strict';

  const form = document.querySelector('form#posForm.server-pos-layout');
  if (!form) return;

  const cartLines = form.querySelector('#cartLines');
  const submitButton = form.querySelector('#confirmOrder');
  if (!cartLines || !submitButton) return;

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let submitting = false;

  // Rows are rebuilt on every cart change. Delegate clicks to their stable parent
  // and animate the NEW input after the existing quantity handler has rendered it.
  cartLines.addEventListener('click', (event) => {
    if (reducedMotion || !(event.target instanceof Element)) return;
    const control = event.target.closest('button.qty-plus, button.qty-minus');
    if (!control || !cartLines.contains(control)) return;
    const productId = control.closest('.pos-cart-line')
      ?.querySelector('input[name="product_id"]')?.value;
    if (!productId) return;

    window.requestAnimationFrame(() => {
      const currentRow = Array.from(cartLines.querySelectorAll('.pos-cart-line'))
        .find((row) => row.querySelector('input[name="product_id"]')?.value === productId);
      const counter = currentRow?.querySelector('input[name="quantity"]');
      if (!counter) return; // Decrement to zero removes the line.
      counter.classList.add('server-qty-bounce');
      window.setTimeout(() => counter.classList.remove('server-qty-bounce'), 135);
    });
  });

  // This runs after the existing POS submit listener: an empty cart or invalid
  // fields must NEVER be converted into a valid submission by animation code.
  form.addEventListener('submit', (event) => {
    if (submitting) {
      event.preventDefault();
      return;
    }
    if (event.defaultPrevented || !form.checkValidity()) return;

    event.preventDefault();
    submitting = true;
    form.setAttribute('aria-busy', 'true');
    submitButton.disabled = true;
    submitButton.setAttribute('aria-live', 'polite');

    // Spinner indicates preparation; the green tick means ready, not paid/accepted.
    submitButton.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>Préparation de l’envoi…</span>';

    window.setTimeout(() => {
      submitButton.classList.add('server-submit-ready');
      submitButton.innerHTML = '<svg class="bi bi-check-circle" xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 15A7 7 0 1 0 8 1a7 7 0 0 0 0 14m0 1A8 8 0 1 1 8 0a8 8 0 0 1 0 16"/><path d="m10.97 4.97-.02.022-3.473 4.425-2.093-2.093a.75.75 0 0 0-1.06 1.06l2.667 2.667a.75.75 0 0 0 1.128-.084l4-5a.75.75 0 0 0-1.128-1.016"/></svg><span>Prêt pour envoi…</span>';
      window.setTimeout(() => {
        // A native submit sends this exact POST once, without replaying submit
        // event handlers (and without losing the existing CSRF/quantity fields).
        HTMLFormElement.prototype.submit.call(form);
      }, 1000);
    }, reducedMotion ? 0 : 220);
  });
})();
