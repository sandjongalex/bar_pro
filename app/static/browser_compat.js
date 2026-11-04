/* BAR_PRO browser compatibility bootstrap (ES5): no host-specific logic.
 * This only changes presentation; authorization, CSRF and finance stay server-side.
 */
(function (win, doc) {
  'use strict';
  var root = doc.documentElement;
  if (!root) return;
  var ua = (win.navigator && win.navigator.userAgent) || '';
  var android = /Android\s+(\d+)/i.exec(ua);
  var ios = /(?:iPhone|iPad|iPod).*OS\s+(\d+)_/i.exec(ua);
  var oldDevice = (android && Number(android[1]) < 6) ||
                  (ios && Number(ios[1]) < 13);
  var supportsGrid = false;
  try {
    supportsGrid = !!(win.CSS && win.CSS.supports &&
                      win.CSS.supports('display', 'grid'));
  } catch (ignored) {}

  // Missing essential APIs means the modern async runtime may not work.
  var asyncReady = !!(win.fetch && win.Promise && win.URL && win.Headers &&
    win.AbortController && win.FormData && win.Map && win.Set &&
    win.WeakSet && win.Object && win.Object.assign && win.Array &&
    win.Array.from && win.CustomEvent && win.Element &&
    win.Element.prototype && win.Element.prototype.closest);
  var mode = !asyncReady ? 'limited' :
             (oldDevice || !supportsGrid ? 'lite' : 'modern');
  root.setAttribute('data-bp-compat', mode);
  root.className += (root.className ? ' ' : '') + 'bp-compat-' + mode;
  win.BarProBrowserCompat = { mode: mode, asyncReady: asyncReady };

  function hasClass(element, name) {
    return element && typeof element.className === 'string' &&
           (' ' + element.className + ' ').indexOf(' ' + name + ' ') >= 0;
  }
  function openDrawer(drawer, value) {
    if (!drawer) return;
    if (value && !hasClass(drawer, 'bp-compat-drawer-open')) {
      drawer.className += ' bp-compat-drawer-open';
    }
    if (!value) {
      drawer.className = (' ' + drawer.className + ' ')
        .replace(/\s+bp-compat-drawer-open\s+/g, ' ')
        .replace(/^\s+|\s+$/g, '');
    }
    drawer.setAttribute('aria-hidden', value ? 'false' : 'true');
    var triggers = doc.querySelectorAll('[data-bs-target="#appNavDrawer"]');
    for (var i = 0; i < triggers.length; i++) {
      triggers[i].setAttribute('aria-expanded', value ? 'true' : 'false');
    }
  }
  function ready() {
    if (mode === 'limited') {
      var warning = doc.getElementById('browserCompatNotice');
      if (warning) warning.removeAttribute('hidden');
    }
    // Retain a working mobile navigation if Bootstrap 5 is unavailable.
    if (mode === 'modern' || (win.bootstrap && win.bootstrap.Offcanvas)) return;
    var drawer = doc.getElementById('appNavDrawer');
    if (!drawer) return;
    doc.addEventListener('click', function (event) {
      var node = event.target;
      while (node && node !== doc) {
        if (node.getAttribute &&
            node.getAttribute('data-bs-target') === '#appNavDrawer') {
          event.preventDefault();
          openDrawer(drawer, true);
          return;
        }
        if (hasClass(node, 'btn-close') &&
            hasClass(drawer, 'bp-compat-drawer-open')) {
          event.preventDefault();
          openDrawer(drawer, false);
          return;
        }
        node = node.parentNode;
      }
    }, false);
    doc.addEventListener('keydown', function (event) {
      if ((event.key === 'Escape' || event.keyCode === 27) &&
          hasClass(drawer, 'bp-compat-drawer-open')) {
        openDrawer(drawer, false);
      }
    }, false);
  }
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', ready);
  else ready();
})(window, document);
