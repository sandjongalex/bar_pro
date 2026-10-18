/* BAR_PRO color mode: local-device clock (18:00–05:59) with a saved manual override. */
(() => {
  'use strict';

  const STORAGE_KEY = 'bar-pro:color-mode';
  const root = document.documentElement;

  function readPreference() {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      return saved === 'dark' || saved === 'light' ? saved : 'auto';
    } catch (_) {
      return 'auto'; // Private mode / storage unavailable.
    }
  }

  function timeBasedTheme() {
    const hour = new Date().getHours(); // Device-local hour, not server time.
    return hour >= 18 || hour < 6 ? 'dark' : 'light';
  }

  let preference = readPreference();

  function applyTheme() {
    const theme = preference === 'auto' ? timeBasedTheme() : preference;
    root.setAttribute('data-bs-theme', theme);
    root.setAttribute('data-theme-preference', preference);

    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#121212' : '#f5f7f6');

    document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
      const dark = theme === 'dark';
      button.setAttribute('aria-pressed', String(dark));
      button.setAttribute('aria-label', dark ? 'Activer le thème clair' : 'Activer le thème sombre');
      button.setAttribute('title', dark ? 'Passer en thème clair' : 'Passer en thème sombre');
      const icon = button.querySelector('[data-theme-icon]');
      const label = button.querySelector('[data-theme-label]');
      if (icon) icon.textContent = dark ? '☀' : '☾';
      if (label) label.textContent = dark ? 'Clair' : 'Sombre';
    });
  }

  // Applied synchronously in <head> to avoid flashing the wrong theme on load.
  applyTheme();

  function setManualTheme(next) {
    preference = next;
    try { window.localStorage.setItem(STORAGE_KEY, next); } catch (_) {}
    applyTheme();
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
      button.addEventListener('click', () => {
        const current = root.getAttribute('data-bs-theme');
        setManualTheme(current === 'dark' ? 'light' : 'dark');
      });
    });
    applyTheme();
  });

  // Auto mode follows the current device clock while the PWA stays open.
  window.setInterval(() => { if (preference === 'auto') applyTheme(); }, 60 * 1000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && preference === 'auto') applyTheme();
  });
  window.addEventListener('storage', (event) => {
    if (event.key === STORAGE_KEY || event.key === null) {
      preference = readPreference();
      applyTheme();
    }
  });
})();
