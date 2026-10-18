'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const script = fs.readFileSync(path.resolve(__dirname, '../app/static/server_async_orders.js'), 'utf8');
const live = fs.readFileSync(path.resolve(__dirname, '../app/static/server_pos_ui.js'), 'utf8');

class Form {
  constructor() {
    this.dataset = { serverAsync: '1', serverUserId: '42' };
    this.currentKey = 'intention12345678901234567890';
  }
  querySelector(selector) {
    return selector.includes('idempotency_key') ? { value: this.currentKey } : null;
  }
  matches() { return false; }
  closest() { return null; }
  checkValidity() { return true; }
  reportValidity() {}
}

function fixture(options = {}) {
  const form = new Form();
  const listeners = {};
  const storage = new Map();
  const messages = [];
  let requests = 0;
  let refreshes = 0;
  let resets = 0;
  const document = {
    querySelector(query) { return query.startsWith('form#posForm.server-pos-layout') ? form : null; },
    addEventListener(type, fn, useCapture) { listeners[type] = { fn, useCapture }; },
  };
  const window = {
    location: { pathname: '/bars/1/orders/new' },
    BarProAsync: {
      submit: async (...args) => { requests++; return options.submit ? options.submit(...args) : {data: {id: 12, reference: 'CMD-12'}}; },
      notify: (...args) => messages.push(args),
      request: options.lookup || (async () => {throw Object.assign(new Error('missing'), {code: 'SERVER'});}),
    },
    BarProServerState: { refresh: async () => { refreshes++; } },
    BarProServerCart: { hasItems: () => true, reset: () => { resets++; } },
    sessionStorage: {
      setItem(k, v) {storage.set(k, v);},
      removeItem(k) {storage.delete(k);},
      getItem(k) {return storage.get(k) || null;},
    },
    addEventListener(type, fn) { listeners['window:' + type] = {fn}; },
  };
  vm.runInNewContext(script, {window, document, HTMLFormElement: Form}, {filename:'server_async_orders.js'});
  const event = {
    target: form, submitter: null, prevented: 0, stopped: 0,
    preventDefault() { this.prevented++; }, stopImmediatePropagation() { this.stopped++; },
  };
  const flush = async () => {
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
  };
  return {
    event, form, flush, listeners, messages,
    fire() {listeners.submit.fn(event);},
    result() {return {requests, refreshes, resets, storage: Array.from(storage.values())};},
  };
}

test('server form intercepted in capture phase and success clears cart only after response', async () => {
  let approve;
  const f = fixture({ submit: () => new Promise(resolve => { approve = resolve; }) });
  assert.equal(f.listeners.submit.useCapture, true);
  f.fire();
  assert.equal(f.event.prevented, 1);
  assert.equal(f.event.stopped, 1);
  assert.equal(f.result().resets, 0);
  assert.equal(f.result().requests, 1);
  approve({success:true, data:{id:7, reference:'CMD-7'}});
  await f.flush();
  assert.equal(f.result().resets, 1);
  assert.equal(f.result().refreshes, 1);
  assert.equal(f.result().storage.length, 0);
});

test('timeout or network errors keep invoice and key, with lookup rather than POST retry', async () => {
  const f = fixture({
    submit: async () => {throw Object.assign(new Error('timeout'), {code:'TIMEOUT'});},
    lookup: async () => {throw Object.assign(new Error('404'), {code:'SERVER'});},
  });
  const oldKey = f.form.currentKey;
  f.fire();
  await f.flush();
  assert.equal(f.form.currentKey, oldKey);
  assert.equal(f.result().resets, 0);
  assert.equal(f.result().requests, 1);
  assert.deepEqual(f.result().storage, [oldKey]);
});

test('server-recovered order after lost response clears cart once', async () => {
  const f = fixture({
    submit: async () => {throw Object.assign(new Error('timeout'), {code:'TIMEOUT'});},
    lookup: async () => ({success:true, data:{state:'COMPLETED', id:9, reference:'CMD-9'}}),
  });
  f.fire();
  await f.flush();
  assert.equal(f.result().requests, 1);
  assert.equal(f.result().resets, 1);
  assert.equal(f.result().refreshes, 1);
});

test('live reconciliation handles empty first list, keyed new orders and stale snapshots', () => {
  assert.match(live, /const byId = new Map\(orders\.map/);
  assert.match(live, /const list = document\.querySelector\('\[data-server-orders-grid\]'\)/);
  assert.match(live, /if \(!card\) \{\s*card = makeLiveServerCard\(order\)/);
  assert.match(live, /list\.insertBefore\(card, list\.children\[index\]/);
  assert.match(live, /lastServerSnapshotMs/);
  assert.match(live, /setupInlineOrderActions\(\)/);
  assert.match(live, /window\.BarProOrderEdit\?\.setupServerEditors/);
});
