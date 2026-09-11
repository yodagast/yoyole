/* 商品详情页：加载详情、SKU 选择、加入购物车、立即购买 */
(function () {
  'use strict';
  var App = window.App;
  var t = App.t;

  function qs(name) {
    return new URLSearchParams(location.search).get(name);
  }

  var productId = Number(qs('id'));
  var selectedSkuId = null;

  function specText(attrs) {
    if (!attrs) return '';
    return Object.keys(attrs).map(function (k) {
      return k + ': ' + attrs[k];
    }).join(' / ');
  }

  var ESC_MAP = {
    '&': '&' + 'amp;',
    '<': '&' + 'lt;',
    '>': '&' + 'gt;',
    '"': '&' + 'quot;',
    "'": '&' + '#39;',
  };
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return ESC_MAP[c];
    });
  }

  function render(p) {
    var root = document.getElementById('detail-root');
    document.title = (p.display_name || p.sku_code) + ' - YOYOLE';
    var lang = App.getLang();

    // 主图
    var mainImg = p.main_image || 'https://picsum.photos/seed/' + p.id + '/800/500';

    // SKU 列表
    var skus = (p.skus || []).filter(function (s) { return s.is_active; });
    var skuHtml = skus.map(function (s) {
      var active = selectedSkuId === s.id ? 'active' : '';
      return '<button class="sku-option btn ' + (s.is_active ? 'btn-outline' : '') + ' ' + active + '" data-sku="' + s.id + '" data-price="' + s.price + '" data-stock="' + s.available_stock + '">'
        + '<div class="sku-spec">' + esc(specText(s.attributes) || s.sku_code) + '</div>'
        + '<div class="sku-price">' + App.money(s.price) + '</div>'
        + '<div class="sku-stock">' + t('stock') + ': ' + s.available_stock + '</div>'
        + '</button>';
    }).join('');

    var images = (p.images && p.images.length) ? p.images : [mainImg];
    var thumbHtml = images.map(function (src, i) {
      return '<div class="thumb ' + (i === 0 ? 'active' : '') + '" onclick="window.__switchImg(this, \'' + esc(src) + '\')">'
        + '<img src="' + esc(src) + '" onerror="this.src=\'https://picsum.photos/seed/' + p.id + '/100/100\'"></div>';
    }).join('');

    root.innerHTML =
      '<div class="detail-wrap">'
      + '<div class="detail-gallery">'
      + '<img id="main-img" class="detail-main-img" src="' + esc(mainImg) + '" onerror="this.src=\'https://picsum.photos/seed/' + p.id + '/800/500\'">'
      + '<div class="thumb-row">' + thumbHtml + '</div>'
      + '</div>'
      + '<div class="detail-info">'
      + '<h1 class="detail-name">' + esc(p.display_name || p.sku_code) + '</h1>'
      + '<div class="detail-meta"><span>SKU: ' + esc(p.sku_code) + '</span></div>'
      + '<div class="detail-price" id="detail-price">' + App.money(p.base_price) + '</div>'
      + '<div class="detail-desc">' + esc((p.description_i18n && (p.description_i18n[lang] || p.description_i18n.zh)) || '') + '</div>'
      + '<div class="detail-block"><div class="detail-label">' + t('sku') + '</div>'
      + '<div class="sku-list" id="sku-list">' + (skuHtml || '<div class="empty">' + t('stock') + ' 0</div>') + '</div></div>'
      + '<div class="detail-block"><div class="detail-label">' + t('quantity') + '</div>'
      + '<div class="qty-control"><button onclick="window.__qty(-1)">-</button>'
      + '<span id="qty-val">1</span><button onclick="window.__qty(1)">+</button></div></div>'
      + '<div class="detail-actions">'
      + '<button class="btn btn-primary btn-lg" id="btn-add-cart">' + t('add_to_cart') + '</button>'
      + '<button class="btn btn-outline btn-lg" id="btn-buy-now">' + t('buy_now') + '</button>'
      + '</div>'
      + '</div></div>';

    // 默认选中第一个有货 SKU
    if (!selectedSkuId && skus.length) {
      var first = skus[0];
      selectedSkuId = first.id;
    }
    markSku();
    bindSku();
    bindActions();
    updatePrice();
  }

  function markSku() {
    var list = document.getElementById('sku-list');
    if (!list) return;
    list.querySelectorAll('[data-sku]').forEach(function (b) {
      var id = Number(b.getAttribute('data-sku'));
      b.classList.toggle('active', id === selectedSkuId);
    });
  }

  function bindSku() {
    var list = document.getElementById('sku-list');
    if (!list) return;
    list.querySelectorAll('[data-sku]').forEach(function (b) {
      b.onclick = function () {
        selectedSkuId = Number(b.getAttribute('data-sku'));
        markSku();
        updatePrice();
      };
    });
  }

  function activeSkuBtn() {
    var list = document.getElementById('sku-list');
    if (!list) return null;
    var els = list.querySelectorAll('[data-sku]');
    for (var i = 0; i < els.length; i++) {
      if (Number(els[i].getAttribute('data-sku')) === selectedSkuId) return els[i];
    }
    return null;
  }

  function updatePrice() {
    var btn = activeSkuBtn();
    if (btn) {
      document.getElementById('detail-price').textContent = App.money(btn.getAttribute('data-price'));
    }
  }

  function currentQty() {
    return Number(document.getElementById('qty-val').textContent);
  }

  window.__qty = function (d) {
    var el = document.getElementById('qty-val');
    var v = Math.max(1, Number(el.textContent) + d);
    var btn = activeSkuBtn();
    if (btn) {
      var stock = Number(btn.getAttribute('data-stock'));
      v = Math.min(v, stock || 1);
    }
    el.textContent = v;
  };

  window.__switchImg = function (el, src) {
    document.getElementById('main-img').src = src;
    document.querySelectorAll('.thumb').forEach(function (x) { x.classList.remove('active'); });
    el.classList.add('active');
  };

  function ensureAuth() {
    if (!App.getToken()) {
      App.openAuth('login');
      return false;
    }
    return true;
  }

  function bindActions() {
    document.getElementById('btn-add-cart').onclick = function () {
      if (!ensureAuth()) return;
      if (!selectedSkuId) { App.toast(t('sku'), 'error'); return; }
      App.api('/api/cart/items', {
        method: 'POST', token: App.getToken(),
        body: { sku_id: selectedSkuId, quantity: currentQty() },
      }).then(function () {
        App.toast(t('operation_success'), 'success');
        App.loadCartBadge();
      }).catch(function (e) { App.toast(e.message, 'error'); });
    };

    document.getElementById('btn-buy-now').onclick = function () {
      if (!ensureAuth()) return;
      if (!selectedSkuId) { App.toast(t('sku'), 'error'); return; }
      App.api('/api/cart/items', {
        method: 'POST', token: App.getToken(),
        body: { sku_id: selectedSkuId, quantity: currentQty() },
      }).then(function () {
        location.href = '/cart.html';
      }).catch(function (e) { App.toast(e.message, 'error'); });
    };
  }

  function load() {
    if (!productId) {
      document.getElementById('detail-root').innerHTML = '<div class="empty">' + t('operation_failed') + '</div>';
      return;
    }
    document.getElementById('detail-loading').textContent = t('loading');
    App.api('/api/products/' + productId)
      .then(render)
      .catch(function (e) {
        document.getElementById('detail-root').innerHTML = '<div class="empty">' + e.message + '</div>';
      });
  }

  App.renderNav('home');
  load();
})();