(function () {
  'use strict';

  const CARD_SELECTOR = '.pos-product[data-product-id], .suborder-product[data-product-id]';
  let imageObserver = null;

  function installStyles() {
    if (document.getElementById('productImagesUiStyles')) return;
    const style = document.createElement('style');
    style.id = 'productImagesUiStyles';
    style.textContent = `
      .product-image-slot{position:relative;overflow:hidden;border:1px solid #dfe7e3;background:#f7faf8;color:var(--brand);display:flex;align-items:center;justify-content:center;font-weight:900}
      .product-image-slot img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;padding:3px;background:#fff;opacity:0;transition:opacity .12s ease}
      .product-image-slot.has-image img{opacity:1}
      .pos-product-avatar.product-image-slot{width:56px;height:56px;flex:0 0 56px;border-radius:12px;font-size:1rem}
      .suborder-product-photo.product-image-slot{width:100%;height:78px;border-radius:11px;margin-bottom:3px;font-size:1.15rem}
      @media(max-width:540px){.pos-product-avatar.product-image-slot{width:50px;height:50px;flex-basis:50px}.suborder-product-photo.product-image-slot{height:68px}}
    `;
    document.head.appendChild(style);
  }

  function initialFor(card) {
    const name = String(card.dataset.name || '').trim();
    return name ? name.charAt(0).toLocaleUpperCase('fr') : 'P';
  }

  function imageSlot(card) {
    let slot = card.querySelector('.pos-product-avatar');
    if (slot) {
      slot.classList.add('product-image-slot');
      return slot;
    }

    slot = document.createElement('span');
    slot.className = 'suborder-product-photo product-image-slot';
    slot.textContent = initialFor(card);
    card.insertBefore(slot, card.firstChild);
    return slot;
  }

  function startImageLoad(image) {
    const source = image.dataset.src;
    if (!source || image.getAttribute('src')) return;
    image.src = source;
    delete image.dataset.src;
  }

  function lazyObserver() {
    if (imageObserver !== null) return imageObserver;
    if (!('IntersectionObserver' in window)) return null;

    imageObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        startImageLoad(entry.target);
        observer.unobserve(entry.target);
      });
    }, {
      // Start just before the card enters the viewport so the photo is already
      // decoded when the employee scrolls to it, without fetching the whole
      // catalogue at once.
      rootMargin: '280px 0px',
      threshold: 0.01,
    });
    return imageObserver;
  }

  function attachImage(card, url) {
    if (!url || card.dataset.productImageReady === '1') return;
    card.dataset.productImageReady = '1';

    const slot = imageSlot(card);
    const image = document.createElement('img');
    image.loading = 'lazy';
    image.decoding = 'async';
    image.fetchPriority = 'low';
    image.alt = card.dataset.name ? `Photo ${card.dataset.name}` : 'Photo produit';
    image.addEventListener('load', () => slot.classList.add('has-image'), { once: true });
    image.addEventListener('error', () => {
      image.remove();
      slot.classList.remove('has-image');
      card.dataset.productImageReady = '0';
    }, { once: true });
    image.dataset.src = url;
    slot.appendChild(image);

    const observer = lazyObserver();
    if (observer) observer.observe(image);
    else startImageLoad(image);
  }

  async function setupProductImages() {
    const cards = Array.from(document.querySelectorAll(CARD_SELECTOR));
    if (!cards.length) return;

    const match = window.location.pathname.match(/\/bars\/(\d+)(?:\/|$)/);
    if (!match) return;

    installStyles();

    try {
      const response = await fetch(`/bars/${match[1]}/catalog/image-map`, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) return;
      const payload = await response.json();
      const images = payload && payload.images ? payload.images : {};

      cards.forEach((card) => {
        const productId = String(card.dataset.productId || '');
        const imageUrl = images[productId];
        if (imageUrl) attachImage(card, imageUrl);
      });
    } catch (error) {
      // Product selection remains fully usable with the existing initial-letter fallback.
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupProductImages, { once: true });
  } else {
    setupProductImages();
  }
})();
