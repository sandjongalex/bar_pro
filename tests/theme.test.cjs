'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const script = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'theme.js'), 'utf8');
const key = 'bar-pro:color-mode';

function createBrowser({ hour, saved = null, storageFails = false }) {
  let localHour = hour;
  let theme = null;
  const attrs = {};
  const buttons = [];
  const onDocument = new Map();
  const onWindow = new Map();
  const timers = [];
  const meta = { color: null, setAttribute(k, v) { assert.equal(k, 'content'); this.color = v; } };
  const store = new Map(saved ? [[key, saved]] : []);

  const root = {
    setAttribute(k, v) { attrs[k] = v; if (k === 'data-bs-theme') theme = v; },
    getAttribute(k) { return attrs[k] || null; }
  };
  const document = {
    documentElement: root, hidden: false,
    querySelector(selector) {
      assert.equal(selector, 'meta[name="theme-color"]');
      return meta;
    },
    querySelectorAll(selector) {
      assert.equal(selector, '[data-theme-toggle]');
      return buttons;
    },
    addEventListener(k, callback) { onDocument.set(k, callback); }
  };
  const window = {
    localStorage: {
      getItem(k) { if (storageFails) throw Error('blocked'); return store.get(k) || null; },
      setItem(k, v) { if (storageFails) throw Error('blocked'); store.set(k, v); }
    },
    setInterval(fn, delay) { timers.push({ fn, delay }); },
    addEventListener(k, cb) { onWindow.set(k, cb); }
  };
  class FakeDate { getHours() { return localHour; } }

  function addButton() {
    const listeners = new Map();
    const button = {
      attrs: {},
      setAttribute(k, v) { this.attrs[k] = v; },
      querySelector(selector) {
        return selector === '[data-theme-icon]' ? this.icon :
          selector === '[data-theme-label]' ? this.label : null;
      },
      icon: { textContent: '' }, label: { textContent: '' },
      addEventListener(k, fn) { listeners.set(k, fn); },
      click() { listeners.get('click')(); }
    };
    buttons.push(button);
    return button;
  }

  vm.runInNewContext(script, { document, window, Date: FakeDate });
  return {
    get theme() { return theme; },
    attrs, meta, store, document, onDocument, onWindow, timers, addButton,
    setHour(next) { localHour = next; },
    ready() { onDocument.get('DOMContentLoaded')(); },
    tick() { timers[0].fn(); }
  };
}

test('uses local device time with the correct morning/evening boundaries', () => {
  for (const [hour, expected] of [
    [0, 'dark'], [5, 'dark'], [6, 'light'], [12, 'light'],
    [17, 'light'], [18, 'dark'], [23, 'dark']
  ]) {
    const browser = createBrowser({ hour });
    assert.equal(browser.theme, expected, `hour ${hour}`);
    assert.equal(browser.attrs['data-theme-preference'], 'auto');
    assert.equal(browser.meta.color, expected === 'dark' ? '#121212' : '#f5f7f6');
  }
});

test('manual button switches immediately, updates accessibility and persists preference', () => {
  const b = createBrowser({ hour: 20 });
  const button = b.addButton();
  b.ready();
  assert.equal(button.attrs['aria-label'], 'Activer le thème clair');
  assert.equal(button.attrs['aria-pressed'], 'true');
  button.click();
  assert.equal(b.theme, 'light');
  assert.equal(b.store.get(key), 'light');
  assert.equal(button.label.textContent, 'Sombre');
  assert.equal(button.attrs['aria-pressed'], 'false');
  b.setHour(23);
  b.tick();
  assert.equal(b.theme, 'light', 'manual light stays light overnight');
  button.click();
  assert.equal(b.theme, 'dark');
  assert.equal(b.store.get(key), 'dark');
});

test('automatic mode tracks device time on a running app', () => {
  const b = createBrowser({ hour: 17 });
  b.ready();
  assert.equal(b.timers[0].delay, 60000);
  b.setHour(18);
  b.tick();
  assert.equal(b.theme, 'dark');
  b.setHour(6);
  b.onDocument.get('visibilitychange')();
  assert.equal(b.theme, 'light');
});

test('saved settings, storage events, and storage-disabled browsers remain usable', () => {
  const b = createBrowser({ hour: 20, saved: 'light' });
  b.ready();
  assert.equal(b.theme, 'light');
  b.store.set(key, 'dark');
  b.onWindow.get('storage')({ key });
  assert.equal(b.theme, 'dark');
  b.store.delete(key);
  b.onWindow.get('storage')({ key: null });
  assert.equal(b.theme, 'dark');
  b.setHour(11);
  b.tick();
  assert.equal(b.theme, 'light');

  const restricted = createBrowser({ hour: 20, storageFails: true });
  const button = restricted.addButton();
  restricted.ready();
  button.click();
  assert.equal(restricted.theme, 'light', 'manual choice works without localStorage');
});
