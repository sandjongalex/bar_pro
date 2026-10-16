/* Install support only: never cache data or interfere with forms. */
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', {scope: '/'}).catch(() => {
      // Installation is optional. Normal web access keeps working.
    });
  });
}
