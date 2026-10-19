/* Employee mobile navigation and virtual keyboard coexistence.
 * Positioning only: never intercepts financial form submission. */
(function (root) {
  'use strict';
  const doc = root.document;
  if (!doc) return;
  const nav = doc.querySelector('[data-employee-primary-nav]');
  if (!nav) return;

  const viewport = root.visualViewport;
  const canMeasure = Boolean(viewport);
  function activeInput() {
    const el = doc.activeElement;
    return el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && !el.readOnly;
  }
  function update() {
    const offset = canMeasure
      ? Math.max(0, root.innerHeight - viewport.height - viewport.offsetTop)
      : 0;
    const keyboard = Boolean(activeInput()) && offset > 120 && root.innerWidth < 768;
    doc.body.classList.toggle('bp-keyboard-open', keyboard);
    doc.documentElement.style.setProperty(
      '--employee-keyboard-offset', keyboard ? Math.round(offset) + 'px' : '0px'
    );
  }
  if (viewport) {
    viewport.addEventListener('resize', update, {passive: true});
    viewport.addEventListener('scroll', update, {passive: true});
  }
  root.addEventListener('resize', update, {passive: true});
  doc.addEventListener('focusin', update);
  doc.addEventListener('focusout', () => {
    // focusout fires before focusin when switching between inputs; defer one frame.
    root.requestAnimationFrame(update);
  });
  doc.addEventListener('visibilitychange', () => {
    if (!doc.hidden) update();
  });
  update();
})(window);
