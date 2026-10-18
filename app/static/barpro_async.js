/* Opt-in progressive enhancement for Flask/Jinja web routes. No API/JWT calls. */
(function (root) {
  'use strict';
  const doc = root.document;
  if (!doc) return;

  const activeRequests = new Map();
  const activeForms = new WeakSet();
  const DEFAULT_TIMEOUT = 12000;
  let noticeTimer = null;

  function webUrl(value) {
    const url = new URL(value, root.location.href);
    if (url.origin !== root.location.origin || /^\/api\/v1(?:\/|$)/.test(url.pathname)) {
      throw new Error('Seules les routes web du même site sont autorisées.');
    }
    return url;
  }

  function csrfToken(form) {
    return form?.querySelector('[name="csrf_token"]')?.value ||
      doc.querySelector('meta[name="csrf-token"]')?.content ||
      doc.querySelector('input[name="csrf_token"]')?.value || '';
  }

  function messageFor(payload, status) {
    if (payload && typeof payload.message === 'string' && payload.message.trim()) return payload.message;
    if (payload && typeof payload.error === 'string' && payload.error.trim()) return payload.error;
    if (payload && typeof payload.error?.message === 'string') return payload.error.message;
    if (status === 401 || status === 403) return 'Session expirée ou accès refusé. Vérifiez votre connexion.';
    if (status === 409) return 'Les données ont changé. Vérifiez les informations avant de recommencer.';
    if (status === 422 || status === 400) return 'Vérifiez les informations saisies.';
    return 'Opération impossible. Aucun nouveau paiement ou commande ne doit être supposé enregistré.';
  }

  function notify(message, kind = 'info') {
    let region = doc.getElementById('bp-async-announcements');
    if (!region) {
      region = doc.createElement('div');
      region.id = 'bp-async-announcements';
      region.className = 'bp-async-announcements';
      doc.body.appendChild(region);
    }
    // A newer notification must not be dismissed by an older notification's timer.
    if (noticeTimer !== null) {
      root.clearTimeout(noticeTimer);
      noticeTimer = null;
    }
    const type = kind === 'error' ? 'error' : kind === 'success' ? 'success' : 'info';
    const item = doc.createElement('div');
    item.className = 'bp-async-notice bp-async-notice-' + type;
    item.setAttribute('role', type === 'error' ? 'alert' : 'status');
    item.setAttribute('aria-live', type === 'error' ? 'assertive' : 'polite');
    item.textContent = String(message);

    const close = doc.createElement('button');
    close.type = 'button';
    close.className = 'bp-async-notice-close';
    close.textContent = '×';
    close.setAttribute('aria-label', 'Fermer la notification');
    close.addEventListener('click', () => {
      if (region.firstElementChild !== item) return;
      if (noticeTimer !== null) root.clearTimeout(noticeTimer);
      noticeTimer = null;
      item.remove();
    });
    item.appendChild(close);
    region.replaceChildren(item);

    const duration = type === 'success' ? 3000 : type === 'error' ? 10000 : 5000;
    noticeTimer = root.setTimeout(() => {
      if (region.firstElementChild !== item) return;
      item.remove();
      noticeTimer = null;
    }, duration);
    return item;
  }

  function connectionChanged() {
    const online = root.navigator?.onLine !== false;
    doc.documentElement.dataset.bpNetwork = online ? 'online' : 'offline';
    doc.dispatchEvent(new CustomEvent('barpro:network', { detail: { online } }));
    if (!online) notify('Connexion interrompue : les opérations ne peuvent pas être confirmées.', 'error');
  }

  async function request(url, options = {}) {
    const target = webUrl(url);
    const method = String(options.method || 'GET').toUpperCase();
    const format = options.format === 'html' ? 'html' : 'json';
    const mutation = !['GET', 'HEAD'].includes(method);
    const key = options.key || null;
    if (root.navigator?.onLine === false) {
      throw Object.assign(new Error('Connexion indisponible.'), { code: 'OFFLINE' });
    }
    if (key && options.latest !== false) activeRequests.get(key)?.abort();
    const controller = new AbortController();
    if (key) activeRequests.set(key, controller);
    const timeout = Math.max(1, Number(options.timeout) || DEFAULT_TIMEOUT);
    let timedOut = false;
    const timeoutId = root.setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
    const externalAbort = () => controller.abort();
    options.signal?.addEventListener('abort', externalAbort, { once: true });
    try {
      const headers = new Headers(options.headers || {});
      headers.set('Accept', format === 'html' ? 'text/html' : 'application/json');
      headers.set('X-Requested-With', 'XMLHttpRequest');
      headers.set('X-BarPro-Async', '1');
      if (mutation) {
        const csrf = csrfToken(options.form);
        if (!csrf) throw Object.assign(new Error('Jeton CSRF manquant.'), { code: 'CSRF' });
        headers.set('X-CSRFToken', csrf);
      }
      const response = await root.fetch(target.href, {
        method, credentials: 'same-origin', redirect: 'follow', cache: 'no-store',
        body: options.body, headers, signal: controller.signal
      });
      if (response.redirected || !webUrl(response.url || target.href)) {
        throw Object.assign(new Error('Session expirée ou redirection inattendue.'), { code: 'REDIRECT', status: 401 });
      }
      const type = response.headers.get('Content-Type') || '';
      if (!type.toLowerCase().includes(format === 'json' ? 'application/json' : 'text/html')) {
        throw Object.assign(new Error('Réponse inattendue du serveur. Vérifiez votre session.'), { code: 'FORMAT', status: response.status });
      }
      const payload = format === 'json' ? await response.json() : await response.text();
      if (key && activeRequests.get(key) !== controller) {
        throw Object.assign(new Error('Réponse obsolète.'), { code: 'STALE' });
      }
      if (!response.ok || (format === 'json' && (payload?.success === false || payload?.ok === false))) {
        throw Object.assign(new Error(messageFor(payload, response.status)), { code: 'SERVER', status: response.status, payload });
      }
      return payload;
    } catch (error) {
      if (error?.name === 'AbortError') {
        throw Object.assign(new Error(timedOut
          ? (mutation ? 'Délai dépassé : résultat incertain. Vérifiez la caisse avant toute nouvelle tentative.' : 'Délai réseau dépassé.')
          : 'Requête remplacée ou annulée.'), { code: timedOut ? 'TIMEOUT' : 'ABORT' });
      }
      if (error instanceof TypeError) {
        throw Object.assign(new Error(mutation
          ? 'Réseau indisponible : vérifiez si l’opération a été enregistrée avant de la répéter.'
          : 'Connexion réseau indisponible.'), { code: 'NETWORK' });
      }
      throw error;
    } finally {
      root.clearTimeout(timeoutId);
      options.signal?.removeEventListener('abort', externalAbort);
      if (key && activeRequests.get(key) === controller) activeRequests.delete(key);
    }
  }

  async function submit(form, submitter) {
    if (!form || activeForms.has(form)) return { skipped: true };
    const method = String(form.getAttribute('method') || 'GET').toUpperCase();
    const action = submitter?.formAction || form.action || root.location.href;
    webUrl(action);
    if (method === 'GET') throw new Error('Utilisez la navigation HTML pour les formulaires GET.');
    const buttons = Array.from(form.querySelectorAll('button[type="submit"], input[type="submit"]'));
    const previous = buttons.map(button => button.disabled);
    const body = new FormData(form);
    if (submitter?.name && !body.has(submitter.name)) body.append(submitter.name, submitter.value);
    activeForms.add(form);
    form.setAttribute('aria-busy', 'true');
    buttons.forEach(button => { button.disabled = true; });
    try {
      const payload = await request(action, { method, form, body, key: null });
      notify(messageForSuccess(payload), 'success');
      form.dispatchEvent(new CustomEvent('barpro:async-success', { bubbles: true, detail: { payload } }));
      // Do not reset user input, navigate or reload: integrations explicitly decide what to update.
      return payload;
    } catch (error) {
      notify(error.message || 'Opération impossible.', 'error');
      form.dispatchEvent(new CustomEvent('barpro:async-error', { bubbles: true, detail: { error } }));
      throw error;
    } finally {
      buttons.forEach((button, index) => { button.disabled = previous[index]; });
      form.removeAttribute('aria-busy');
      activeForms.delete(form);
    }
  }

  function messageForSuccess(payload) {
    return typeof payload?.message === 'string' && payload.message.trim()
      ? payload.message : 'Opération enregistrée.';
  }

  function safeClone(element) {
    const clone = doc.importNode(element, true);
    const nodes = [clone, ...clone.querySelectorAll('*')];
    nodes.forEach(node => {
      if (['SCRIPT', 'IFRAME', 'OBJECT', 'EMBED'].includes(node.tagName)) {
        node.remove();
        return;
      }
      Array.from(node.attributes).forEach(attribute => {
        if (/^on/i.test(attribute.name) || (['href', 'src', 'xlink:href'].includes(attribute.name.toLowerCase()) &&
          /^\s*(?:javascript|data):/i.test(attribute.value))) node.removeAttribute(attribute.name);
      });
    });
    return clone;
  }

  function reconcile(container, html, selector) {
    if (!container?.isConnected) return { skipped: true };
    const parsed = new DOMParser().parseFromString(html, 'text/html');
    const source = parsed.querySelector(selector || (container.id ? '#' + CSS.escape(container.id) : '[data-bp-component]'));
    if (!source) throw new Error('Composant absent de la réponse du serveur.');
    const current = Array.from(container.children);
    const incoming = Array.from(source.children);
    if (incoming.some(child => !child.dataset.bpKey) ||
        current.some(child => !child.dataset.bpKey)) {
      throw new Error('Chaque élément du composant doit posséder un data-bp-key stable.');
    }
    const seen = new Set();
    if (incoming.some(child => {
      const duplicate = seen.has(child.dataset.bpKey);
      seen.add(child.dataset.bpKey);
      return duplicate;
    })) throw new Error('Identifiant métier dupliqué dans le composant.');
    const byKey = new Map(current.map(child => [child.dataset.bpKey, child]));
    const focus = doc.activeElement;
    const scrollX = root.scrollX, scrollY = root.scrollY;
    let updated = 0;
    incoming.forEach((candidate, index) => {
      const key = candidate.dataset.bpKey;
      let node = byKey.get(key);
      if (node && !node.isEqualNode(candidate) && !(focus && node.contains(focus))) {
        const replacement = safeClone(candidate);
        node.replaceWith(replacement);
        node = replacement;
        updated++;
      }
      if (!node) {
        node = safeClone(candidate);
        updated++;
      }
      if (container.children[index] !== node) container.insertBefore(node, container.children[index] || null);
    });
    byKey.forEach((node, key) => {
      if (!seen.has(key) && !(focus && node.contains(focus))) { node.remove(); updated++; }
    });
    if (updated && root.scrollTo) root.scrollTo(scrollX, scrollY);
    return { updated };
  }

  async function refresh(container, url, options = {}) {
    const element = typeof container === 'string' ? doc.querySelector(container) : container;
    if (!element || !element.isConnected || doc.visibilityState === 'hidden' || root.navigator?.onLine === false) {
      return { skipped: true };
    }
    const key = options.key || (element.id ? 'fragment:' + element.id : null);
    if (!key) throw new Error('Un ID de composant ou une clé explicite est requis.');
    const html = await request(url, { format: 'html', key, timeout: options.timeout });
    return reconcile(element, html, options.selector);
  }

  function watch(container, url, options = {}) {
    const interval = Math.max(1000, Number(options.interval) || 5000);
    let disposed = false, pending = false, timer = null;
    async function tick() {
      if (disposed || pending || doc.visibilityState === 'hidden' || root.navigator?.onLine === false) return;
      pending = true;
      try { await refresh(container, url, options); }
      catch (error) { if (!['ABORT', 'STALE'].includes(error.code)) notify(error.message, 'error'); }
      finally { pending = false; }
    }
    function resume() { if (doc.visibilityState !== 'hidden' && root.navigator?.onLine !== false) void tick(); }
    timer = root.setInterval(tick, interval);
    doc.addEventListener('visibilitychange', resume);
    root.addEventListener('online', resume);
    if (options.immediate === true) void tick();
    return function stop() {
      disposed = true;
      root.clearInterval(timer);
      doc.removeEventListener('visibilitychange', resume);
      root.removeEventListener('online', resume);
    };
  }

  doc.addEventListener('submit', event => {
    const form = event.target;
    // No form is converted unless a developer explicitly opts it in and adapts its Flask route.
    if (!form?.matches?.('form[data-bp-ajax="json"]') || event.defaultPrevented) return;
    event.preventDefault();
    void submit(form, event.submitter).catch(() => {});
  });
  root.addEventListener('online', connectionChanged);
  root.addEventListener('offline', connectionChanged);
  connectionChanged();

  root.BarProAsync = Object.freeze({ request, submit, refresh, reconcile, watch, notify, csrfToken, webUrl });
})(window);
