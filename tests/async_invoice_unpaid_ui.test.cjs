'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const invoiceCode = fs.readFileSync(path.resolve(__dirname, '../app/static/cashier_invoices_ui.js'), 'utf8');
const unpaidCode = fs.readFileSync(path.resolve(__dirname, '../app/static/unpaid_orders_ui.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

test('invoice filters update URL without navigation and use conditional JSON snapshots', async () => {
  const listeners = {};
  const windowListeners = {};
  const fields = Object.fromEntries(['delivery', 'payment', 'origin'].map(name => [
    name, { value: name === 'payment' ? 'open' : 'all',
      options: [{value:'all'}, {value:'open'}, {value:'delivered'}, {value:'paid'}] },
  ]));
  const form = {
    elements: fields,
    addEventListener(name, fn) { listeners['form:' + name] = fn; },
    querySelector() { return null; },
  };
  const statsNodes = Array.from({length: 4}, () => ({textContent: '0'}));
  const stats = {querySelectorAll() { return statsNodes; }};
  const results = {innerHTML: 'unchanged', querySelector() { return null; }};
  const status = {classList: {toggle() {}}};
  const statusText = {textContent: ''};
  const nodes = {invoiceFilters:form, invoiceStats:stats, invoiceResults:results,
    invoiceAutoRefresh:status, invoiceAutoRefreshText:statusText};
  const document = {
    visibilityState:'visible', activeElement:null,
    getElementById(id) { return nodes[id]; },
    addEventListener(name, fn) { listeners['document:' + name] = fn; },
  };
  const calls = [];
  const window = {
    location: {href:'https://example.test/bars/1/cashier/invoices'},
    history: {pushState(_state, _title, url) {window.location.href = url;}},
    scrollX:0, scrollY:0, scrollTo() {},
    addEventListener(name, fn) {windowListeners[name] = fn;},
    setInterval() {},
    confirm() {return true;},
  };
  const fetch = async (url, options) => {
    calls.push({url, options});
    if (calls.length === 2) return {status:304};
    if (calls.length === 3) throw new TypeError('Network unavailable');
    return {
      status:200, ok:true, headers:{get(name) {
        return name === 'Content-Type' ? 'application/json' : name === 'ETag' ? '"invoice-etag"' : null;
      }},
      json:async () => ({html:'<div id="invoiceResults"></div>',
        stats:{waiting:1,delivered:2,paid:3,visible:4}}),
    };
  };
  class DOMParser {
    parseFromString() {
      return {getElementById() {return {querySelector() {return null;},
        innerHTML:'unchanged', childNodes:[]};}};
    }
  }
  vm.runInNewContext(invoiceCode, {window, document, fetch, navigator:{onLine:true},
    DOMParser, URL, AbortController, Intl, Date, HTMLFormElement:class {}});
  fields.delivery.value = 'delivered';
  listeners['form:change']();
  await flush();
  assert.match(window.location.href, /delivery=delivered/);
  assert.match(calls[0].url, /snapshot=1/);
  assert.equal(statsNodes[3].textContent, '4');
  await window.refreshCashierInvoices();
  assert.equal(calls[1].options.headers['If-None-Match'], '"invoice-etag"');
  assert.equal(results.innerHTML, 'unchanged');
  assert.match(statusText.textContent, /aucune modification/);
  await window.refreshCashierInvoices();
  assert.match(statusText.textContent, /Mise à jour différée/);
  assert.equal(results.innerHTML, 'unchanged');
});

test('unpaid keyed poll preserves selected DOM, uses ETag and warns when selected order disappears', async () => {
  const listeners = {};
  const intervals = {};
  const selected = {checked:true, value:'1'};
  const grid = {
    children:[],
    querySelectorAll(selector) {
      if (selector === '[data-unpaid-order-id]') return this.children;
      if (selector === '[data-merge-checkbox]:checked') {
        return this.children.filter(node => node.checkbox?.checked).map(node => node.checkbox);
      }
      return [];
    },
    querySelector(selector) {
      if (selector === '[data-unpaid-order-id]') return this.children[0] || null;
      return null;
    },
    addEventListener(name, fn) {listeners['grid:' + name] = fn;},
    insertBefore(node, reference) {
      this.children = this.children.filter(item => item !== node);
      const index = reference ? this.children.indexOf(reference) : -1;
      this.children.splice(index < 0 ? this.children.length : index, 0, node);
    },
    removeAttribute() {},
  };
  const card = {
    dataset:{unpaidOrderId:'1', unpaidSignature:'unchanged', unpaidCurrency:'XAF', postedAt:''},
    checkbox:selected,
    querySelector() {return null;},
    classList:{toggle() {}},
    remove() {grid.children = grid.children.filter(node => node !== this);},
  };
  selected.closest = () => card;
  grid.children.push(card);
  const mergeSubmit = {disabled:false, textContent:''};
  const warning = {hidden:true, textContent:''};
  const badge = {textContent:'● Mise à jour auto', classList:{add(){},remove(){}}};
  const empty = {hidden:true};
  const count = {textContent:'1'}, pending = {textContent:'0'};
  const partial = {textContent:'0'}, due = {textContent:'100'};
  const mergeForm = {addEventListener() {}};
  const root = {dataset:{liveUrl:'/bars/1/unpaid-orders/data?staff=all',
    role:'CASHIER',csrfToken:'token',staffFilter:'all'}};
  const selectors = {'[data-unpaid-monitor]':root,'[data-unpaid-grid]':grid,
    '[data-unpaid-live-badge]':badge,'[data-unpaid-empty]':empty,
    '[data-unpaid-count]':count,'[data-unpaid-pending-delivery]':pending,
    '[data-unpaid-partial]':partial,'[data-unpaid-due]':due,
    '[data-merge-form]':mergeForm,'[data-merge-submit]':mergeSubmit,
    '[data-unpaid-merge-warning]':warning};
  const document = {
    hidden:false,activeElement:null,
    querySelector(selector) {return selectors[selector] || null;},
    getElementById() {return null;},
    addEventListener(name, fn) {listeners['document:' + name] = fn;},
    createElement() {throw new Error('Unchanged cards must never be rebuilt');},
  };
  const calls = [];
  const snapshots = [
    {status:200,ok:true,headers:{get(name) {return name === 'ETag' ? '"unpaid-etag"' : null;}},
      json:async () => ({orders:[{id:1,signature:'unchanged',currency:'XAF',
        pending_deliveries:[],payment_blocked:false}],stats:{count:1,partial:0,due:'100',pending_delivery:0}})},
    {status:304},
    {status:200,ok:true,headers:{get() {return '"unpaid-new"';}},
      json:async () => ({orders:[],stats:{count:0,partial:0,due:'0',pending_delivery:0}})},
  ];
  const fetch = async (url, options) => {
    calls.push({url,options});
    return snapshots.shift();
  };
  const window = {
    location:{href:'https://example.test/bars/1/unpaid-orders'},
    scrollX:0,scrollY:0,scrollTo() {},
    setTimeout() {},
    setInterval(fn,ms) {intervals[ms] = fn;},
    addEventListener() {},
  };
  vm.runInNewContext(unpaidCode, {document,window,fetch,URL,AbortController,
    navigator:{onLine:true},Intl,Date,Number,Set,Map});
  await flush();
  assert.equal(grid.children[0], card);
  assert.equal(selected.checked, true);
  assert.equal(mergeSubmit.disabled, true); // One selected order cannot be merged.
  await intervals[4000]();
  await flush();
  assert.equal(calls[1].options.headers['If-None-Match'], '"unpaid-etag"');
  assert.equal(grid.children[0], card);
  await intervals[4000]();
  await flush();
  assert.equal(grid.children.length, 0);
  assert.equal(warning.hidden, false);
  assert.match(warning.textContent, /ne sont plus éligibles/);
  assert.equal(count.textContent, '0');
});
