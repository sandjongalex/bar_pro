/* Install affordance only. No sales, permissions or business data are changed. */
(() => {
  'use strict';
  let deferredPrompt = null;
  const standalone = () =>
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true ||
    document.referrer.startsWith('android-app://');

  const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isAndroid = () => /Android/i.test(navigator.userAgent);
  const supported = () => window.isSecureContext && (isIOS() || isAndroid() || 'BeforeInstallPromptEvent' in window || !!deferredPrompt);

  const buttons = () => document.querySelectorAll('[data-pwa-install]');
  function refresh() {
    const available = !standalone() && supported();
    buttons().forEach(button => { button.hidden = !available; });
  }
  function instructions() {
    if (isIOS()) {
      return '<p>Dans Safari, touchez <strong>Partager</strong> (carré avec une flèche), puis <strong>Sur l’écran d’accueil</strong> et <strong>Ajouter</strong>.</p><p>Si cette option n’apparaît pas, ouvrez le site dans Safari.</p>';
    }
    if (isAndroid()) {
      return '<p>Ouvrez le menu <strong>⋮</strong> de votre navigateur, puis choisissez <strong>Installer l’application</strong> ou <strong>Ajouter à l’écran d’accueil</strong>.</p><p>Si l’option n’est pas proposée, vérifiez que le site est chargé en HTTPS.</p>';
    }
    return '<p>Dans Chrome ou Edge, utilisez l’icône d’installation dans la barre d’adresse ou le menu du navigateur pour installer Bar Manager Pro.</p>';
  }
  function help() {
    const target = document.getElementById('pwaInstallHelpText');
    const modal = document.getElementById('pwaInstallHelp');
    if (!target || !modal) return;
    target.innerHTML = instructions();
    if (window.bootstrap?.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(modal).show();
    } else {
      alert(target.textContent);
    }
  }
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    deferredPrompt = event;
    refresh();
  });
  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    refresh();
  });
  window.matchMedia('(display-mode: standalone)').addEventListener?.('change', refresh);
  document.addEventListener('DOMContentLoaded', () => {
    refresh();
    buttons().forEach(button => button.addEventListener('click', async () => {
      if (standalone()) return refresh();
      if (!deferredPrompt) return help();
      const prompt = deferredPrompt;
      deferredPrompt = null;
      try {
        await prompt.prompt();
        await prompt.userChoice;
      } catch (error) {
        console.warn('PWA installation prompt unavailable:', error);
        help();
      }
      refresh();
    }));
  });
})();
