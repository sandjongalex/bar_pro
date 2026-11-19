/* BAR_PRO: role-specific same-origin workspace with persistent POS and closable modules. */
(function () {
  'use strict';

  // Each module keeps its own scripts and form state inside an iframe.
  // Embedded pages never build a second workspace.
  if (window.parent !== window) {
    if (window.location.pathname === '/login') {
      try { window.top.location.assign(window.location.href); } catch (error) {}
    }
    return;
  }

  var workspace = document.getElementById('bpWorkspace');
  var home = document.getElementById('main');
  if (!workspace || !home) return;

  var barId = workspace.getAttribute('data-bar-id');
  var role = workspace.getAttribute('data-workspace-role') || 'OWNER';
  var tabBar = document.getElementById('bpWorkspaceTabs');
  var panels = document.getElementById('bpWorkspacePanels');
  if (!barId || !tabBar || !panels) return;

  var opened = [];
  var active = null;
  var nextId = 0;
  var maxTabs = 5;
  var barPrefix = '/bars/' + barId;
  var homeLabel = role === 'CASHIER' ? '₣ Poste de caisse' :
    role === 'SERVER' ? '＋ Nouvelle commande' : '⌂ Tableau de bord';
  var homeTab = document.createElement('button');
  homeTab.type = 'button';
  homeTab.className = 'bp-workspace-home';
  homeTab.textContent = homeLabel;
  homeTab.setAttribute('role', 'tab');
  homeTab.setAttribute('aria-selected', 'true');
  homeTab.addEventListener('click', function () { activate(null); });
  tabBar.appendChild(homeTab);

  function setFrameActivity(tab, visible) {
    try {
      tab.iframe.contentWindow.BarProWorkspaceInactive = !visible;
      tab.iframe.contentWindow.dispatchEvent(new CustomEvent('barpro:workspace-visibility', {
        detail: { visible: visible }
      }));
    } catch (error) { /* A navigation may temporarily be in progress. */ }
  }

  function activate(tab) {
    active = tab;
    home.hidden = !!tab;
    // Keep the tab strip accessible even when returning to the main screen.
    workspace.hidden = false;
    panels.hidden = !tab;
    window.BarProWorkspaceInactive = !!tab;
    window.dispatchEvent(new CustomEvent('barpro:workspace-visibility', {
      detail: { visible: !tab }
    }));
    homeTab.setAttribute('aria-selected', tab ? 'false' : 'true');
    homeTab.classList.toggle('is-active', !tab);
    opened.forEach(function (item) {
      var visible = item === tab;
      item.iframe.hidden = !visible;
      item.button.setAttribute('aria-selected', visible ? 'true' : 'false');
      item.wrapper.classList.toggle('is-active', visible);
      setFrameActivity(item, visible);
    });
  }

  function removeTab(tab, force) {
    if (!force && tab.dirtyForms.size &&
        !window.confirm('Ce module contient des informations non enregistrées. Fermer quand même ?')) return;
    var index = opened.indexOf(tab);
    if (index === -1) return;
    opened.splice(index, 1);
    tab.wrapper.remove();
    tab.iframe.src = 'about:blank';
    tab.iframe.remove();
    if (active === tab) activate(opened[index - 1] || opened[index] || null);
  }

  function isHomeUrl(url) {
    if (role === 'OWNER') {
      return url.pathname === '/dashboard' && url.searchParams.get('bar_id') === barId;
    }
    if (role === 'CASHIER') {
      return url.pathname === barPrefix + '/checkout' ||
        url.pathname === barPrefix + '/cashier/workspace';
    }
    return role === 'SERVER' && (
      url.pathname === barPrefix + '/orders/new' ||
      (url.pathname === '/dashboard' && url.searchParams.get('bar_id') === barId)
    );
  }

  function dismissDrawer() {
    var drawer = document.getElementById('appNavDrawer');
    if (drawer && window.bootstrap && window.bootstrap.Offcanvas) {
      var instance = window.bootstrap.Offcanvas.getInstance(drawer);
      if (instance) instance.hide();
    }
  }

  function activateHomeUrl(url) {
    activate(null);
    if (role === 'CASHIER') {
      var orderId = url.searchParams.get('order_id');
      if (orderId && /^\d+$/.test(orderId) && window.CashierWorkspace &&
          typeof window.CashierWorkspace.selectOrder === 'function') {
        void window.CashierWorkspace.selectOrder(url.href);
      } else if (url.searchParams.get('sale') === '1') {
        var sale = document.getElementById('openQuickSale');
        if (sale) sale.click();
      }
    }
    if (role === 'SERVER' && url.hash === '#posForm') {
      var orderForm = document.getElementById('posForm');
      if (orderForm) orderForm.scrollIntoView({ block: 'start' });
    }
  }

  function addTab(url, label) {
    var key = url.pathname + url.search;
    var existing = opened.find(function (tab) { return tab.key === key; });
    if (existing) {
      activate(existing);
      return;
    }
    if (opened.length >= maxTabs) {
      window.alert('Vous avez déjà ouvert cinq modules. Fermez un onglet avant de continuer.');
      return;
    }
    var tab = { key: key, id: ++nextId, dirtyForms: new Set() };
    var wrapper = document.createElement('span');
    wrapper.className = 'bp-workspace-tab';
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'bp-workspace-tab-title';
    button.textContent = label || 'Module';
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-selected', 'false');
    button.addEventListener('click', function () { activate(tab); });
    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'bp-workspace-tab-close';
    close.textContent = '×';
    close.setAttribute('aria-label', 'Fermer ' + (label || 'le module'));
    close.addEventListener('click', function () { removeTab(tab, false); });
    wrapper.appendChild(button);
    wrapper.appendChild(close);
    tabBar.appendChild(wrapper);
    var frame = document.createElement('iframe');
    frame.className = 'bp-workspace-frame';
    frame.title = label || 'Module BAR_PRO';
    frame.setAttribute('loading', 'eager');
    frame.setAttribute('referrerpolicy', 'same-origin');
    frame.hidden = true;
    tab.wrapper = wrapper;
    tab.button = button;
    tab.iframe = frame;
    frame.addEventListener('load', function () {
      tab.dirtyForms.clear();
      setFrameActivity(tab, active === tab);
      try {
        var child = frame.contentWindow;
        var childDocument = child.document;
        if (child.location.pathname === '/login') {
          window.location.assign(child.location.href);
          return;
        }
        var selectedBar = child.location.pathname.match(/^\/bars\/(\d+)\//);
        if (selectedBar && selectedBar[1] !== barId) {
          window.location.assign(child.location.href);
          return;
        }
        childDocument.addEventListener('click', function (event) {
          if (event.defaultPrevented || event.button !== 0 || event.ctrlKey ||
              event.shiftKey || event.altKey || event.metaKey) return;
          var anchor = event.target.closest && event.target.closest('a[href]');
          var url = resolveLink(anchor);
          if (!url) return;
          if (isHomeUrl(url)) {
            event.preventDefault();
            activateHomeUrl(url);
          } else if (role === 'OWNER' && url.pathname === '/dashboard' &&
                     !url.searchParams.get('bar_id')) {
            event.preventDefault();
            window.location.assign(url.href);
          }
        }, true);
        childDocument.addEventListener('input', function (event) {
          var form = event.target.closest && event.target.closest('form');
          if (form) tab.dirtyForms.add(form);
        });
        childDocument.addEventListener('change', function (event) {
          var form = event.target.closest && event.target.closest('form');
          if (form) tab.dirtyForms.add(form);
        });
        childDocument.addEventListener('reset', function (event) {
          tab.dirtyForms.delete(event.target);
        });
        childDocument.addEventListener('barpro:async-success', function (event) {
          tab.dirtyForms.delete(event.target);
        });
        childDocument.addEventListener('barpro:workspace-task-complete', function (event) {
          var detail = event.detail || {};
          // A dedicated payment tab may close after the server has committed.
          // The permanent Caisse tab and any voucher-to-print remain open.
          if (detail.task === 'payment' &&
              detail.voucherRequired === false &&
              /^Paiement\b/i.test(button.textContent)) {
            removeTab(tab, true);
          }
        });
      } catch (error) { /* Keep the visible page usable if a tab navigates away. */ }
    });
    panels.appendChild(frame);
    opened.push(tab);
    activate(tab);
    var embedded = new URL(url.href);
    embedded.searchParams.set('bp_workspace', '1');
    frame.src = embedded.href;
  }

  function resolveLink(anchor) {
    if (!anchor || anchor.hasAttribute('download') ||
        anchor.getAttribute('target') && anchor.getAttribute('target') !== '_self' ||
        anchor.hasAttribute('data-bp-workspace-exclude')) return null;
    try {
      var url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin) return null;
      url.searchParams.delete('bp_workspace');
      return url;
    } catch (error) { return null; }
  }

  document.addEventListener('click', function (event) {
    if (event.defaultPrevented || event.button !== 0 ||
        event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    var anchor = event.target.closest && event.target.closest(
      '.sidebar .app-nav a, .sidebar .brand-block, .app-nav-drawer .app-nav a, .global-mobile-bottom a, .context-nav a, a[data-bp-workspace-open]'
    );
    if (!anchor) return;
    var url = resolveLink(anchor);
    if (!url) return;
    if (isHomeUrl(url)) {
      event.preventDefault();
      activateHomeUrl(url);
      dismissDrawer();
      return;
    }
    // Owner's global overview changes context; never embed it in a bar tab.
    if (role === 'OWNER' && url.pathname === '/dashboard' &&
        !url.searchParams.get('bar_id')) return;
    var sameBarPath = url.pathname === '/bars/' + barId ||
      url.pathname.indexOf('/bars/' + barId + '/') === 0;
    var cashierOverview = role === 'CASHIER' && url.pathname === '/dashboard' &&
      url.searchParams.get('bar_id') === barId;
    if (!sameBarPath && !cashierOverview) return;
    event.preventDefault();
    var text = anchor.querySelector('.nav-link-text');
    addTab(url, (text ? text.textContent : anchor.textContent).trim());
    dismissDrawer();
  });

  window.addEventListener('beforeunload', function (event) {
    // Switching tabs never navigates. Only warn if actually leaving the app
    // with unsaved work inside a secondary window.
    if (!opened.some(function (tab) { return tab.dirtyForms.size > 0; })) return;
    event.preventDefault();
    event.returnValue = '';
  });

  activate(null);
})();
