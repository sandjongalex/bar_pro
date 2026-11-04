'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const script = fs.readFileSync(path.resolve(__dirname, '../app/static/browser_compat.js'), 'utf8');

function run(userAgent, missing = []) {
  const attrs = {}, listeners = {};
  const warning = { hidden: true, removeAttribute(name) { if (name === 'hidden') this.hidden = false; } };
  const root = { className: '', setAttribute(k, v) { attrs[k] = v; } };
  const doc = {
    documentElement: root, readyState: 'loading',
    addEventListener(type, cb) { listeners[type] = cb; },
    getElementById(id) { return id === 'browserCompatNotice' ? warning : null; },
    querySelectorAll() { return []; },
  };
  const win = {
    navigator: { userAgent }, CSS: { supports: () => true },
    fetch() {}, Promise, URL, Headers, AbortController, FormData, Map, Set, WeakSet,
    Object, Array, CustomEvent: function () {}, Element: { prototype: { closest() {} } },
  };
  for (const key of missing) delete win[key];
  vm.runInNewContext(script, { window: win, document: doc }, { filename: 'browser_compat.js' });
  listeners.DOMContentLoaded();
  return { mode: attrs['data-bp-compat'], win, warning };
}

test('a supported browser retains modern mode', () => {
  const page = run('Mozilla/5.0 Chrome/120.0.0.0');
  assert.equal(page.mode, 'modern');
  assert.equal(page.warning.hidden, true);
  assert.equal(page.win.BarProBrowserCompat.asyncReady, true);
});

test('Samsung Android 5.1 Chrome 95 uses a lightweight visual mode', () => {
  const page = run('Mozilla/5.0 (Linux; Android 5.1.1; SM-T900) AppleWebKit/537.36 Chrome/95.0.4638.74 Mobile Safari/537.36');
  assert.equal(page.mode, 'lite');
  assert.equal(page.warning.hidden, true);
  assert.equal(page.win.BarProBrowserCompat.asyncReady, true);
});

test('older iOS selects lite; missing browser APIs select limited and show a warning', () => {
  assert.equal(run('Mozilla/5.0 (iPad; CPU OS 12_5 like Mac OS X)').mode, 'lite');
  const page = run('Mozilla/5.0 Chrome/46', ['fetch', 'AbortController']);
  assert.equal(page.mode, 'limited');
  assert.equal(page.warning.hidden, false);
  assert.equal(page.win.BarProBrowserCompat.asyncReady, false);
});

test('the compatibility bootstrap never hardcodes an app hostname', () => {
  assert.ok(!script.includes('pythonanywhere.com'));
  assert.ok(!script.includes('contabo'));
});
