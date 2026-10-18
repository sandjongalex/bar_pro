'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'app', 'static', 'server_order_animations.js'),
  'utf8'
);

function classList() {
  const values = new Set();
  return {
    add(name) { values.add(name); },
    remove(name) { values.delete(name); },
    contains(name) { return values.has(name); }
  };
}

function harness({ server = true, reducedMotion = false } = {}) {
  class Element {}
  const listeners = {};
  const frames = [];
  const timers = [];
  let valid = true;
  let postCount = 0;

  const button = {
    disabled: false,
    innerHTML: '3. Envoyer à la caisse',
    classList: classList(),
    setAttribute(name, value) { this[name] = value; }
  };
  const cartLines = {
    rows: [],
    addEventListener(name, callback) { listeners[name] = callback; },
    contains(node) { return !!node && node.inCart === true; },
    querySelectorAll(selector) {
      assert.equal(selector, '.pos-cart-line');
      return this.rows;
    }
  };
  const form = {
    attributes: {},
    querySelector(selector) {
      return selector === '#cartLines' ? cartLines
        : selector === '#confirmOrder' ? button : null;
    },
    addEventListener(name, callback) { listeners[name] = callback; },
    checkValidity() { return valid; },
    setAttribute(name, value) { this.attributes[name] = value; }
  };

  function HTMLFormElement() {}
  HTMLFormElement.prototype.submit = function () {
    assert.equal(this, form);
    postCount += 1;
  };

  const window = {
    matchMedia() { return { matches: reducedMotion }; },
    requestAnimationFrame(fn) { frames.push(fn); },
    setTimeout(fn, ms) { timers.push({ fn, ms }); }
  };
  const document = {
    querySelector(selector) {
      assert.equal(selector, 'form#posForm.server-pos-layout');
      return server ? form : null;
    }
  };

  vm.runInNewContext(source, { document, window, Element, HTMLFormElement }, {
    filename: 'server_order_animations.js'
  });

  function makeRow(productId, quantity) {
    const input = { classList: classList(), value: String(quantity) };
    const row = {
      querySelector(selector) {
        if (selector === 'input[name="product_id"]') return { value: String(productId) };
        if (selector === 'input[name="quantity"]') return input;
        return null;
      }
    };
    const plus = new Element();
    plus.inCart = true;
    plus.closest = selector => selector === 'button.qty-plus, button.qty-minus' ? plus
      : selector === '.pos-cart-line' ? row : null;
    return { row, input, plus };
  }

  function submit({ prevented = false } = {}) {
    const event = {
      defaultPrevented: prevented,
      preventDefault() { this.defaultPrevented = true; }
    };
    listeners.submit(event);
    return event;
  }

  function flushFrame() {
    const fn = frames.shift();
    assert.ok(fn, 'animation frame must exist');
    fn();
  }

  function runTimer() {
    const timer = timers.shift();
    assert.ok(timer, 'timer must exist');
    timer.fn();
    return timer.ms;
  }

  return {
    listeners, cartLines, button, form, frames, timers,
    makeRow, submit, flushFrame, runTimer,
    setValid(next) { valid = next; },
    get postCount() { return postCount; }
  };
}

test('no behavior is installed outside the server POS', () => {
  const h = harness({ server: false });
  assert.equal(Object.keys(h.listeners).length, 0);
});

test('quantity animation targets recreated row, not detached button', () => {
  const h = harness();
  const before = h.makeRow('42', 1);
  h.cartLines.rows = [before.row];
  h.listeners.click({ target: before.plus });

  // The existing server POS code replaces the whole row after the click.
  const after = h.makeRow('42', 2);
  h.cartLines.rows = [after.row];
  h.flushFrame();
  assert.equal(after.input.classList.contains('server-qty-bounce'), true);
  assert.equal(before.input.classList.contains('server-qty-bounce'), false);
  assert.equal(h.runTimer(), 135);
  assert.equal(after.input.classList.contains('server-qty-bounce'), false);
});

test('decrement that removes a line does not throw or animate another product', () => {
  const h = harness();
  const before = h.makeRow('42', 1);
  h.cartLines.rows = [before.row];
  h.listeners.click({ target: before.plus });
  h.cartLines.rows = [h.makeRow('99', 5).row];
  h.flushFrame();
  assert.equal(h.timers.length, 0);
});

test('invalid and previously cancelled submit never starts a POST', () => {
  const h = harness();
  h.setValid(false);
  const invalid = h.submit();
  assert.equal(invalid.defaultPrevented, false);
  assert.equal(h.timers.length, 0);
  h.setValid(true);
  const cancelled = h.submit({ prevented: true });
  assert.equal(cancelled.defaultPrevented, true);
  assert.equal(h.timers.length, 0);
  assert.equal(h.postCount, 0);
});

test('valid submit shows spinner, check, then posts exactly once', () => {
  const h = harness();
  const event = h.submit();
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.button.disabled, true);
  assert.equal(h.form.attributes['aria-busy'], 'true');
  assert.match(h.button.innerHTML, /spinner-border/);
  assert.equal(h.runTimer(), 220);
  assert.match(h.button.innerHTML, /bi-check-circle/);
  assert.match(h.button.innerHTML, /Prêt pour envoi/);
  assert.equal(h.postCount, 0);

  // Duplicate submits (including Enter while pending) are ignored.
  const duplicate = h.submit();
  assert.equal(duplicate.defaultPrevented, true);
  assert.equal(h.timers.length, 1);
  assert.equal(h.runTimer(), 1000);
  assert.equal(h.postCount, 1);
  assert.equal(h.timers.length, 0);
});

test('reduced-motion disables counter bounce and skips initial spinner delay', () => {
  const h = harness({ reducedMotion: true });
  const row = h.makeRow('42', 2);
  h.cartLines.rows = [row.row];
  h.listeners.click({ target: row.plus });
  assert.equal(h.frames.length, 0);
  h.submit();
  assert.equal(h.runTimer(), 0);
  assert.equal(h.runTimer(), 1000);
  assert.equal(h.postCount, 1);
});
