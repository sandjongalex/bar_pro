'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const code = fs.readFileSync(path.resolve(__dirname, '../app/static/barpro_async.js'), 'utf8');

function start(fetch, overrides = {}) {
  const registrations = {};
  const document = {
    visibilityState: 'visible',
    documentElement: { dataset: {} },
    addEventListener(type, callback) { registrations[type] = callback; },
    removeEventListener() {},
    dispatchEvent() {},
    querySelector() { return null; },
    getElementById() { return null; },
    createElement() {
      return {
        setAttribute() {}, addEventListener() {}, appendChild() {}, replaceChildren() {}, textContent: '',
        attributes: [], querySelectorAll() { return []; }
      };
    },
    body: { appendChild() {} },
    ...overrides.document,
  };
  const window = {
    document,
    navigator: { onLine: true },
    location: { href: 'https://example.test/bars/1/orders/new', origin: 'https://example.test' },
    fetch,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    addEventListener() {},
    removeEventListener() {},
    scrollTo() {},
    ...overrides.window,
  };
  vm.runInNewContext(code, {
    window,
    URL,
    Headers,
    AbortController,
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
    DOMParser: class {},
    CSS: { escape: value => String(value) },
    FormData: class { constructor() { this.values = new Map(); } has(k) { return this.values.has(k); } append(k,v) { this.values.set(k,v); } },
  }, { filename: 'barpro_async.js' });
  return { api: window.BarProAsync, window, document, registrations };
}

function response(payload, { status = 200, html = false, redirected = false } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status, redirected, url: 'https://example.test/bars/1/async-test',
    headers: new Headers({ 'Content-Type': html ? 'text/html; charset=utf-8' : 'application/json' }),
    json: async () => payload, text: async () => payload,
  };
}

test('exposes opt-in API and refuses foreign origins and JWT API paths', () => {
  const { api } = start(async () => response({ success: true }));
  for (const method of ['request', 'submit', 'refresh', 'reconcile', 'watch', 'notify', 'csrfToken', 'webUrl']) {
    assert.equal(typeof api[method], 'function');
  }
  assert.throws(() => api.webUrl('https://other.test/bars/1'), /même site/);
  assert.throws(() => api.webUrl('/api/v1/bars/1/orders'), /routes web/);
  assert.equal(api.webUrl('/bars/1/orders').pathname, '/bars/1/orders');
});

test('GET includes cookie credentials and explicit web-only headers', async () => {
  let sent;
  const { api } = start(async (url, options) => {
    sent = { url, options };
    return response({ success: true, data: { count: 2 } });
  });
  const answer = await api.request('/bars/1/count');
  assert.equal(answer.data.count, 2);
  assert.equal(sent.options.credentials, 'same-origin');
  assert.equal(sent.options.headers.get('X-BarPro-Async'), '1');
  assert.equal(sent.options.headers.get('Accept'), 'application/json');
  assert.equal(sent.options.headers.get('Authorization'), null);
});

test('POST forwards form CSRF token and keeps Bearer API disabled', async () => {
  let sent;
  const { api } = start(async (url, options) => {
    sent = options;
    return response({ success: true });
  });
  const form = { querySelector: () => ({ value: 'signed-csrf-token' }) };
  await api.request('/bars/1/async-test', { method: 'POST', form, body: 'a=1' });
  assert.equal(sent.headers.get('X-CSRFToken'), 'signed-csrf-token');
  assert.equal(sent.headers.get('Authorization'), null);
  await assert.rejects(api.request('/bars/1/async-test', { method: 'POST' }), /CSRF/);
});

test('application errors, HTTP failures and HTML redirects are not successes', async () => {
  const { api } = start(async () => response({ success: false, error: { message: 'Stock insuffisant' } }));
  await assert.rejects(api.request('/bars/1/test'), /Stock insuffisant/);
  const denied = start(async () => response({ message: 'Accès refusé' }, { status: 403 }));
  await assert.rejects(denied.api.request('/bars/1/test'), /Accès refusé/);
  const redirected = start(async () => response('<html>login</html>', { html: true, redirected: true }));
  await assert.rejects(redirected.api.request('/bars/1/test'), /Session expirée/);
});

test('network timeouts signal uncertain mutations, without unsafe automatic retries', async () => {
  let attempts = 0;
  const { api } = start((_url, options) => {
    attempts++;
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
    });
  });
  const form = { querySelector: () => ({ value: 'csrf' }) };
  await assert.rejects(api.request('/bars/1/orders', { method: 'POST', form, timeout: 10 }), /résultat incertain/);
  assert.equal(attempts, 1);
});

test('newer response supersedes older response using stable refresh key', async () => {
  const pending = [];
  const { api } = start(() => new Promise(resolve => pending.push(resolve)));
  const old = api.request('/bars/1/state', { key: 'bar:1:orders' });
  const recent = api.request('/bars/1/state', { key: 'bar:1:orders' });
  pending[1](response({ success: true, data: 2 }));
  assert.equal((await recent).data, 2);
  pending[0](response({ success: true, data: 1 }));
  await assert.rejects(old, /Réponse obsolète/);
});

test('opt-in form prevents concurrent sends and restores submit buttons on errors', async () => {
  let resolveRequest;
  let requests = 0;
  const { api } = start(async () => { requests++; return new Promise(resolve => { resolveRequest = resolve; }); });
  const button = { disabled: false };
  const attributes = new Map();
  const form = {
    action: 'https://example.test/bars/1/orders/new',
    getAttribute: () => 'POST',
    querySelector: () => ({ value: 'csrf' }),
    querySelectorAll: () => [button],
    setAttribute: (key, val) => attributes.set(key, val),
    removeAttribute: key => attributes.delete(key),
    dispatchEvent: () => {},
  };
  const first = api.submit(form);
  const second = await api.submit(form);
  assert.equal(second.skipped, true);
  assert.equal(button.disabled, true);
  assert.equal(attributes.get('aria-busy'), 'true');
  resolveRequest(response({ success: true, message: 'Enregistré' }));
  await first;
  assert.equal(requests, 1);
  assert.equal(button.disabled, false);
  assert.equal(attributes.has('aria-busy'), false);
});

test('async delivery uses the form route unless submitter has explicit formaction (mobile Safari)', async () => {
  const sent = [];
  const { api } = start(async (url) => {
    sent.push(url);
    return response({ success: true, message: 'Livraison confirmée' });
  }, { window: { location: { href: 'https://example.test/bars/1/unpaid-orders', origin: 'https://example.test' } } });

  const submitButton = { disabled: false };
  const destination = 'https://example.test/bars/1/unpaid-orders/42/suborders/7/confirm-delivery';
  const form = {
    action: destination,
    getAttribute(name) { return name === 'method' ? 'post' : null; },
    querySelector() { return { value: 'signed-csrf-token' }; },
    querySelectorAll() { return [submitButton]; },
    setAttribute() {}, removeAttribute() {}, dispatchEvent() {},
  };
  const submitter = {
    // Browsers can expose the document URL here even with NO formaction attribute.
    formAction: 'https://example.test/bars/1/unpaid-orders',
    hasAttribute() { return false; },
    name: '',
  };

  await api.submit(form, submitter);
  assert.equal(sent[0], destination, 'default button must not override the form action');

  submitter.formAction = 'https://example.test/bars/1/orders/alternate';
  submitter.hasAttribute = name => name === 'formaction';
  await api.submit(form, submitter);
  assert.equal(sent[1], submitter.formAction, 'explicit formaction must still override the form action');
});

test('customer form named action cannot shadow POST destination', async () => {
  const sent = [];
  const { api } = start(async (url) => {
    sent.push(url);
    return response({ success: true, message: 'Client enregistré' });
  }, { window: { location: {
    href: 'https://example.test/bars/1/customers', origin: 'https://example.test'
  } } });
  const form = {
    // Browser named property collision: input[name=action] shadows form.action.
    action: { toString() { return '[object HTMLInputElement]'; } },
    getAttribute(name) { return name === 'method' ? 'post' : null; },
    querySelector() { return { value: 'csrf-token' }; },
    querySelectorAll() { return [{ disabled: false }]; },
    setAttribute() {}, removeAttribute() {}, dispatchEvent() {},
  };
  await api.submit(form);
  assert.equal(sent[0], 'https://example.test/bars/1/customers');
});

test('watch does not perform hidden or offline polling and provides cleanup', async () => {
  let tick;
  const { api, document } = start(async () => response('<section></section>', { html: true }), {
    window: { setInterval(fn) { tick = fn; return 17; }, clearInterval(id) { assert.equal(id, 17); } },
  });
  const stop = api.watch('#orders', '/bars/1/orders', { interval: 1000 });
  document.visibilityState = 'hidden';
  await tick();
  document.visibilityState = 'visible';
  document.documentElement.dataset.bpNetwork = 'offline';
  stop();
});


function noticeHarness() {
  const nodes = new Map();
  const scheduled = new Map();
  let nextId = 0;
  function element() {
    return {
      children: [], listeners: {},
      setAttribute() {},
      addEventListener(type, callback) { this.listeners[type] = callback; },
      appendChild(child) { this.children.push(child); child.parentNode = this; },
      replaceChildren(...children) {
        this.children.forEach(child => { child.parentNode = null; });
        this.children = children;
        children.forEach(child => { child.parentNode = this; });
      },
      remove() {
        if (!this.parentNode) return;
        this.parentNode.children = this.parentNode.children.filter(child => child !== this);
        this.parentNode = null;
      },
      get firstElementChild() { return this.children[0] || null; },
    };
  }
  const { api } = start(async () => response({ success: true }), {
    document: {
      getElementById(id) { return nodes.get(id) || null; },
      createElement: element,
      body: { appendChild(node) { nodes.set(node.id, node); } },
    },
    window: {
      setTimeout(callback, duration) {
        const id = ++nextId;
        scheduled.set(id, { callback, duration });
        return id;
      },
      clearTimeout(id) { scheduled.delete(id); },
    },
  });
  return { api, scheduled, region: () => nodes.get('bp-async-announcements') };
}

test('success and info notices auto-dismiss after their respective durations', () => {
  const { api, scheduled, region } = noticeHarness();
  const success = api.notify('Commande envoyée', 'success');
  assert.equal(region().firstElementChild, success);
  const successTimer = [...scheduled.values()][0];
  assert.equal(successTimer.duration, 3000);
  successTimer.callback();
  assert.equal(region().firstElementChild, null);

  const info = api.notify('Actualisation en cours', 'info');
  assert.equal(region().firstElementChild, info);
  const infoTimer = [...scheduled.values()].at(-1);
  assert.equal(infoTimer.duration, 5000);
  infoTimer.callback();
  assert.equal(region().firstElementChild, null);
});

test('newer notices survive old timers; errors can be dismissed manually', () => {
  const { api, scheduled, region } = noticeHarness();
  api.notify('Première notification', 'success');
  const oldTimer = [...scheduled.values()][0];
  const error = api.notify('Erreur de connexion', 'error');
  assert.equal(scheduled.size, 1);
  const errorTimer = [...scheduled.values()][0];
  assert.equal(errorTimer.duration, 10000);
  oldTimer.callback(); // Even an already queued callback must not remove the newer notice.
  assert.equal(region().firstElementChild, error);
  const close = error.children[0];
  assert.equal(close.type, 'button');
  close.listeners.click();
  assert.equal(region().firstElementChild, null);
  assert.equal(scheduled.size, 0);
});
