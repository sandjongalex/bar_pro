/* Shared product-category shortcuts for the server, cashier and sub-order carts.
   Client-side only: filtering never mutates the basket or sends a request. */
(function () {
  'use strict';

  var instances = {};

  function fold(value) {
    var text = String(value || '').toLocaleLowerCase('fr');
    return text.normalize ? text.normalize('NFD').replace(/[\u0300-\u036f]/g, '') : text;
  }

  function setup(tabs) {
    var gridId = tabs.getAttribute('data-pos-filter');
    var grid = document.getElementById(gridId);
    if (!grid) return;

    var input = document.getElementById(tabs.getAttribute('data-pos-search'));
    var status = document.getElementById(tabs.getAttribute('data-pos-count'));
    var empty = document.getElementById(tabs.getAttribute('data-pos-empty'));
    var buttons = Array.prototype.slice.call(tabs.querySelectorAll('[data-category]'));
    var cards = Array.prototype.slice.call(grid.querySelectorAll('.pos-product[data-product-id], .suborder-product[data-product-id]'));
    var active = 'all';

    function render() {
      var query = fold(input && input.value).trim();
      var totals = {all: cards.length};
      var visible = 0;
      var available = 0;

      cards.forEach(function (card) {
        var category = card.getAttribute('data-category') || '';
        totals[category] = (totals[category] || 0) + 1;

        var matchesCategory = active === 'all' || category === active;
        var matchesText = !query || fold(card.getAttribute('data-name')).indexOf(query) !== -1;
        var show = matchesCategory && matchesText;
        card.hidden = !show;
        if (show) {
          visible += 1;
          if (!card.disabled && Number(card.getAttribute('data-stock') || 0) > 0) {
            available += 1;
          }
        }
      });

      buttons.forEach(function (button) {
        var selected = button.getAttribute('data-category') === active;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-pressed', selected ? 'true' : 'false');
        var badge = button.querySelector('[data-filter-count]');
        if (badge) badge.textContent = String(totals[button.getAttribute('data-category')] || 0);
      });
      if (status) {
        status.textContent = visible + ' produit' + (visible !== 1 ? 's' : '') +
          ' affiché' + (visible !== 1 ? 's' : '') + ' · ' + available + ' disponible' +
          (available !== 1 ? 's' : '');
      }
      if (empty) empty.hidden = cards.length === 0 || visible !== 0;
    }

    function choose(button) {
      if (!button) return;
      active = button.getAttribute('data-category') || 'all';
      render();
      if (tabs.scrollWidth > tabs.clientWidth) {
        // Scroll only the category strip, never the whole order form.
        tabs.scrollLeft = Math.max(0, button.offsetLeft - tabs.offsetLeft -
          (tabs.clientWidth - button.offsetWidth) / 2);
      }
    }

    buttons.forEach(function (button) {
      button.addEventListener('click', function () { choose(button); });
    });
    if (input) input.addEventListener('input', render);
    if (empty) {
      var reset = empty.querySelector('[data-pos-filter-reset]');
      if (reset) {
        reset.addEventListener('click', function () {
          if (input) input.value = '';
          choose(buttons[0]);
        });
      }
    }

    instances[gridId] = render;
    render();
  }

  window.BarProProductCategoryFilters = {
    refreshByGrid: function (gridId) {
      if (instances[gridId]) instances[gridId]();
    }
  };

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-pos-filter]'), setup);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
