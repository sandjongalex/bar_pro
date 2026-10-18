'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.resolve(__dirname, '../app/static/server_orders_page.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

test('dedicated server history uses URL filters, ETag 304 and keeps unchanged cards', async () => {
  const handlers = {};
  const windowHandlers = {};
  const filters = ['active', 'waiting', 'to_pay', 'paid', 'cancelled', 'all'].map(name => ({
    dataset:{serverMineFilter:name},
    href:'https://bar.example/bars/1/orders/mine?filter=' + name,
    classList:{toggle() {}}, setAttribute() {}, removeAttribute() {},
  }));
  const refreshButton = {addEventListener(name, callback) {handlers['refresh:' + name] = callback;}};
  const status = {textContent:'',classList:{toggle(){}}};
  const stats = ['waiting','to_pay','paid','cancelled','active','all'].map(name => ({
    dataset:{mineCount:name},textContent:'0',
  }));
  const grid = {
    children:[], querySelectorAll() {return [];}, querySelector() {return null;},
  };
  const pagination = {outerHTML:'<nav data-server-mine-pagination></nav>'};
  const results = {
    querySelector(selector) {
      return selector === '[data-server-mine-grid]' ? grid :
        selector === '[data-server-mine-pagination]' ? pagination : null;
    },
  };
  const root = {
    querySelector(selector) {
      return selector === '[data-server-mine-status]' ? status :
        selector === '[data-server-mine-refresh]' ? refreshButton : null;
    },
    querySelectorAll(selector) {
      return selector === '[data-server-mine-filter]' ? filters : stats;
    },
    addEventListener(name, callback) {handlers['root:' + name] = callback;},
  };
  const document = {
    hidden:false, activeElement:null,
    querySelector(selector) {return selector === '[data-server-mine]' ? root : null;},
    getElementById(id) {return id === 'serverMineResults' ? results : null;},
    addEventListener(name, callback) {handlers['document:' + name] = callback;},
  };
  const window = {
    location:{href:'https://bar.example/bars/1/orders/mine'},
    history:{pushState(_state,_title,url) {window.location.href=url;}},
    scrollX:0,scrollY:0,scrollTo(){},setInterval(){},
    addEventListener(name,callback){windowHandlers[name]=callback;},
  };
  const calls = [];
  const fetch = async (url, options) => {
    calls.push({url,options});
    if (calls.length === 2) return {status:304};
    if (calls.length === 3) throw new Error('network down');
    return {
      ok:true,status:200,headers:{get(name) {
        return name === 'Content-Type' ? 'application/json' : name === 'ETag' ? '"etag-1"' : null;
      }},
      json:async () => ({html:'<div id="serverMineResults"></div>',
        filter:'waiting',page:1,pages:1,stats:{waiting:2,to_pay:0,paid:0,cancelled:0,active:2,all:2}}),
    };
  };
  class DOMParser {
    parseFromString() {
      return {getElementById() {return {
        querySelector(selector) {
          return selector === '[data-server-mine-grid]' ? grid :
            selector === '[data-server-mine-pagination]' ? pagination : null;
        },
      };}};
    }
  }
  vm.runInNewContext(script, {window,document,fetch,URL,AbortController,DOMParser,
    navigator:{onLine:true},Intl,Date,Set,Map,Number});
  const waiting = filters[1];
  handlers['root:click']({target:{closest(){return waiting;}},preventDefault(){}});
  await flush();
  assert.match(window.location.href,/filter=waiting/);
  assert.match(calls[0].url,/snapshot=1/);
  assert.equal(calls[0].options.headers['If-None-Match'],undefined);
  assert.equal(stats[0].textContent,'2');
  assert.equal(grid.children.length,0);

  handlers['refresh:click']();
  await flush();
  assert.equal(calls[1].options.headers['If-None-Match'],'"etag-1"');
  assert.match(status.textContent,/aucune modification/);
  handlers['refresh:click']();
  await flush();
  assert.match(status.textContent,/Connexion interrompue/);
  assert.equal(grid.children.length,0);
});
