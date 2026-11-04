# Portable HTTPS and legacy browser mode (BAR_PRO)

## Browser behavior
`browser_compat.js` runs as an ES5 bootstrap in the login page and common layout **before** modern JS.
- `modern`: full CSS/JS capability profile.
- `lite`: Android before 6 (including SM-T900 Android 5.1.1), iOS before 13, or missing CSS Grid. Visual effects are reduced; authorization, stock validation, cash logic, sessions, and CSRF remain untouched. Dynamic operations continue only when supported.
- `limited`: missing core APIs required by AJAX. An explicit notice is shown; traditional HTML routes remain where available. **Not all business operations are guaranteed on very old browsers.**
- A minimal menu drawer is available if Bootstrap Offcanvas cannot run.
- PWA registration and install prompts are disabled in lite/limited mode. No business data are cached offline.

Neither this detection nor CSS is a substitute for testing on actual devices. Never bypass HTTPS or weaken authentication to support old TLS stacks.

## Portable HTTPS at the edge
The TLS certificate and mandatory HTTP to HTTPS redirect belong to the **hosting web server**, not to JavaScript. Flask sets production `SESSION_COOKIE_SECURE=True` and adds `Strict-Transport-Security: max-age=86400` only when the request is recognized as HTTPS (no includeSubDomains or preload). `PREFERRED_URL_SCHEME=https` alone is NOT an HTTP redirect.

### PythonAnywhere (including Free)
Use the HTTPS URL. In the **Web** panel, activate any HTTPS redirect/Force HTTPS option made available on that account; verify actual availability rather than assuming it. Keep `TRUSTED_PROXY_PROTO_HOPS=0` unless the provider requires otherwise. Reload the web application and test both schemes and response headers. Avoid disclosing credentials.

### Contabo VPS (later)
Use Gunicorn WSGI bound **only** to `127.0.0.1:8000`, with firewall rules preventing direct access. Deploy Caddy (see `deploy/Caddyfile.example`) with working DNS, or Nginx + Certbot. Keep MySQL and Redis private; perform backups and test restore. Set `TRUSTED_PROXY_PROTO_HOPS=1` **only** when the trusted local reverse proxy overwrites `X-Forwarded-Proto` and no user can connect directly to WSGI. Do not trust forwarded host headers. Reuse secure secret/JWT key material safely during migration and manage Alembic upgrades. Test certificate renewal, login, cookies, API, finance, printing, and old devices before switching DNS.

`TRUSTED_PROXY_PROTO_HOPS` defaults to 0. `HSTS_MAX_AGE` defaults to 86400 in production, 0 in development and tests. `HSTS_MAX_AGE=0` disables HSTS if required for staging.

## Tests
```bash
node --test tests/browser_compat.test.cjs tests/barpro_async.test.cjs
python -m pytest -q tests/test_transport_browser_compat.py tests/test_config.py tests/test_auth.py
```
Test a modern browser, Samsung SM-T900 Android 5.1.1/Chrome 95, another legacy WebView and mobile viewports. A real-device financial smoke test is mandatory before relying on a legacy device in production.
