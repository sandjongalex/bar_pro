'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const staticPath = (filename) => path.resolve(__dirname, '..', 'app', 'static', filename);

test('skeleton helper exports static, accessible editor, queue, and card templates', () => {
  const window = {};
  const code = fs.readFileSync(staticPath('skeleton_loading.js'), 'utf8');
  vm.runInNewContext(code, { window }, { filename: 'skeleton_loading.js' });
  const helper = window.BarProSkeleton;
  assert.ok(helper);
  assert.deepEqual(Object.keys(helper).sort(), ['editor', 'queue', 'unpaid']);
  for (const type of ['editor', 'queue', 'unpaid']) {
    const markup = helper[type]();
    assert.match(markup, /data-skeleton-placeholder/);
    assert.match(markup, /role="status"/);
    assert.match(markup, /aria-label="Chargement|aria-label="Actualisation/);
    assert.match(markup, /class="visually-hidden"/);
    assert.match(markup, /class="bp-skeleton-line/);
    assert.ok(!/<script\b/i.test(markup));
    assert.ok(!/<input\b/i.test(markup));
    assert.ok(!/<button\b/i.test(markup));
  }
  assert.equal((helper.editor().match(/bp-skeleton-editor-row/g) || []).length, 2);
  assert.equal((helper.unpaid().match(/bp-skeleton-unpaid-card/g) || []).length, 2);
  assert.equal(Object.isFrozen(helper), true);
});

test('the loading lifecycle keeps server-rendered cards on periodic polling', () => {
  const cashier = fs.readFileSync(staticPath('cashier_live_ui.js'), 'utf8');
  const unpaid = fs.readFileSync(staticPath('unpaid_orders_ui.js'), 'utf8');
  for (const text of [cashier, unpaid]) {
    assert.match(text, /!initialLoadAttempted/);
    assert.match(text, /initialLoadAttempted = true/);
    assert.match(text, /if \(showSkeleton\)/);
    assert.match(text, /finally \{/);
    assert.match(text, /removeAttribute\('aria-busy'\)/);
    assert.match(text, /fetch\(/);
  }
  assert.match(cashier, /!queue\.querySelector\('\.cashier-order-link'\)/);
  assert.match(unpaid, /!grid\.querySelector\('\[data-unpaid-order-id\]'\)/);
});

test('styles support shimmer with stable sizes and reduced motion', () => {
  const css = fs.readFileSync(staticPath('skeleton_loading.css'), 'utf8');
  assert.match(css, /@keyframes shimmer/);
  assert.match(css, /animation: shimmer 1\.45s linear infinite/);
  assert.match(css, /min-height: 228px/);
  assert.match(css, /min-height: 238px/);
  assert.match(css, /data-bs-theme="dark"/);
  assert.match(css, /prefers-reduced-motion: reduce/);
});
