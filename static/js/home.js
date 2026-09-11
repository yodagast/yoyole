/* 首页脚本：分类导航、商品加载、搜索、本地化渲染（jjshouse 风格） */
(function () {
  'use strict';
  var App = window.App;
  var t = App.t;

  // 首页专属文案（中/英）
  var HOME_I18N = {
    zh: {
      topbar_left: '🌍 全球免运费 · 7 天无理由退换',
      topbar_right: '💬 在线客服 7×24 小时',
      search_placeholder: '搜索商品...',
      search_btn: '搜索',
      hero_tag: 'MOVE WITH PURPOSE',
      hero_title: '瑜伽与户外，向自然出发',
      hero_sub: '为练习、远行与日常生活准备舒适耐用的装备',
      hero_cta: '立即选购',
      hot_categories: '热门分类',
      featured: '推荐商品',
      search_result: '搜索结果',
      clear_search: '清除搜索',
      no_result: '未找到相关商品',
      brand: '品牌',
      sold: '已售',
      svc1_t: '全球免运费', svc1_d: '满额即享免费配送',
      svc2_t: '安全支付', svc2_d: '多种支付方式保障',
      svc3_t: '7 天退换', svc3_d: '无理由退换货服务',
      svc4_t: '客服支持', svc4_d: '7×24 小时在线客服',
      f_about_t: '关于 YOYOLE', f_about_d: 'YOYOLE 专注瑜伽与户外生活，让每一次呼吸和出发都更自在。',
      f_help_t: '客户服务', f_h1: '配送说明', f_h2: '退换政策', f_h3: '隐私条款',
      f_contact_t: '联系我们', f_pay_t: '支付方式', f_pay_d: '支付宝 · 微信支付 · Stripe',
    },
    en: {
      topbar_left: '🌍 Free Global Shipping · 7-Day Easy Returns',
      topbar_right: '💬 24/7 Online Support',
      search_placeholder: 'Search products...',
      search_btn: 'Search',
      hero_tag: 'MOVE WITH PURPOSE',
      hero_title: 'Yoga and outdoor, closer to nature',
      hero_sub: 'Comfortable, durable gear for practice, travel, and everyday movement',
      hero_cta: 'Shop Now',
      hot_categories: 'Hot Categories',
      featured: 'Featured Products',
      search_result: 'Search Results',
      clear_search: 'Clear Search',
      no_result: 'No products found',
      brand: 'Brand',
      sold: 'Sold',
      svc1_t: 'Free Shipping', svc1_d: 'Free delivery over threshold',
      svc2_t: 'Secure Payment', svc2_d: 'Multiple payment methods',
      svc3_t: '7-Day Returns', svc3_d: 'Hassle-free returns',
      svc4_t: 'Support', svc4_d: '24/7 online customer service',
      f_about_t: 'About YOYOLE', f_about_d: 'YOYOLE creates thoughtful gear for yoga, outdoor living, and everyday movement.',
      f_help_t: 'Customer Service', f_h1: 'Shipping Info', f_h2: 'Return Policy', f_h3: 'Privacy Policy',
      f_contact_t: 'Contact Us', f_pay_t: 'Payment', f_pay_d: 'Alipay · WeChat Pay · Stripe',
    },
  };

  function ht(key) {
    var lang = App.getLang();
    var table = HOME_I18N[lang] || HOME_I18N.zh;
    return table[key] || HOME_I18N.zh[key] || key;
  }

  var state = { categoryId: null, keyword: '' };

  // 本地化静态文案
  function localize() {
    document.title = ht('hero_title') + ' | YOYOLE';
    setText('topbar-left', ht('topbar_left'));
    setText('topbar-right', ht('topbar_right'));
    setText('search-input', ''); // placeholder 单独处理
    document.getElementById('search-input').placeholder = ht('search_placeholder');
    setText('hero-title', ht('hero_title'));
    setText('hero-sub', ht('hero_sub'));
    var cta = document.querySelector('.hero .btn');
    if (cta) cta.textContent = ht('hero_cta');
    setText('category-section-title', ht('hot_categories'));
    setText('products-title', ht('featured'));
    setText('svc1-t', ht('svc1_t')); setText('svc1-d', ht('svc1_d'));
    setText('svc2-t', ht('svc2_t')); setText('svc2-d', ht('svc2_d'));
    setText('svc3-t', ht('svc3_t')); setText('svc3-d', ht('svc3_d'));
    setText('svc4-t', ht('svc4_t')); setText('svc4-d', ht('svc4_d'));
    setText('f-about-t', ht('f_about_t')); setText('f-about-d', ht('f_about_d'));
    setText('f-help-t', ht('f_help_t'));
    setText('f-h1', ht('f_h1')); setText('f-h2', ht('f_h2')); setText('f-h3', ht('f_h3'));
    setText('f-contact-t', ht('f_contact_t'));
    setText('f-pay-t', ht('f_pay_t'));
  }
  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  // 分类导航条
  function loadCategories() {
    App.api('/api/categories').then(function (cats) {
      var nav = document.getElementById('category-nav');
      var lang = App.getLang();
      var html = '<a href="#" data-id="" class="' + (state.categoryId === null ? 'active' : '') + '">' + ht('featured') + '</a>';
      html += (cats || []).map(function (c) {
        var name = (c.name_i18n && (c.name_i18n[lang] || c.name_i18n.zh)) || c.code;
        return '<a href="#" data-id="' + c.id + '" class="' + (state.categoryId === c.id ? 'active' : '') + '">' + name + '</a>';
      }).join('');
      nav.innerHTML = html;
      nav.querySelectorAll('a[data-id]').forEach(function (a) {
        a.onclick = function (e) {
          e.preventDefault();
          state.categoryId = a.getAttribute('data-id') ? Number(a.getAttribute('data-id')) : null;
          state.keyword = '';
          document.getElementById('search-input').value = '';
          document.getElementById('search-info').style.display = 'none';
          document.getElementById('products-title').textContent = state.categoryId === null ? ht('featured') : ' ';
          loadCategories();
          loadProducts();
        };
      });

      // 分类卡片
      var grid = document.getElementById('category-grid');
      var cards = (cats || []).map(function (c) {
        var name = (c.name_i18n && (c.name_i18n[lang] || c.name_i18n.zh)) || c.code;
        var img = 'https://picsum.photos/seed/cat' + c.id + '/400/300';
        return '<div class="category-card" onclick="window.__goCategory(' + c.id + ')">'
          + '<img src="' + img + '" alt="' + name + '">'
          + '<div class="category-card-name">' + name + '</div></div>';
      }).join('');
      grid.innerHTML = cards || '<div class="empty">暂无分类</div>';
    }).catch(function (e) {
      document.getElementById('category-nav').innerHTML = '';
      document.getElementById('category-grid').innerHTML = '<div class="empty">' + e.message + '</div>';
    });
  }

  // 商品列表
  function loadProducts() {
    var box = document.getElementById('products');
    box.innerHTML = '<div class="loading">' + t('loading') + '</div>';
    var path = '/api/products';
    var params = [];
    if (state.categoryId) params.push('category_id=' + state.categoryId);
    if (state.keyword) params.push('q=' + encodeURIComponent(state.keyword));
    if (params.length) path += '?' + params.join('&');

    App.api(path).then(function (data) {
      var list = Array.isArray(data) ? data : (data.items || []);
      var lang = App.getLang();
      if (!list.length) {
        box.innerHTML = '<div class="empty">' + ht('no_result') + '</div>';
        return;
      }
      box.innerHTML = list.map(function (p) {
        var price = App.money(p.base_price);
        var sold = p.sales_count || 0;
        var tag = p.is_featured ? '<div class="product-tag">HOT</div>' : '';
        return '<div class="product-card" onclick="window.__goDetail(' + p.id + ')">'
          + '<div class="product-imgwrap">'
          + '<img class="product-image" src="' + (p.main_image || '') + '" loading="lazy" onerror="this.src=\'https://picsum.photos/seed/' + p.id + '/600/400\'">'
          + tag
          + '</div>'
          + '<div class="product-body">'
          + '<div class="product-name">' + (p.display_name || p.sku_code || '') + '</div>'
          + '<div class="product-price">' + price + '</div>'
          + '<div class="product-meta"><span>' + ht('sold') + ' ' + sold + '</span><span>' + (p.sku_code || '') + '</span></div>'
          + '</div></div>';
      }).join('');
    }).catch(function (e) {
      box.innerHTML = '<div class="empty">' + e.message + '</div>';
    });
  }

  // 搜索
  function doSearch() {
    var kw = document.getElementById('search-input').value.trim();
    state.keyword = kw;
    state.categoryId = null;
    var info = document.getElementById('search-info');
    if (kw) {
      info.style.display = 'block';
      info.innerHTML = ht('search_result') + ': “' + kw + '” '
        + '<a href="#" onclick="window.__clearSearch();return false;">(' + ht('clear_search') + ')</a>';
      document.getElementById('products-title').textContent = '';
    } else {
      info.style.display = 'none';
      document.getElementById('products-title').textContent = ht('featured');
    }
    loadCategories();
    loadProducts();
  }

  function clearSearch() {
    state.keyword = '';
    state.categoryId = null;
    document.getElementById('search-input').value = '';
    document.getElementById('search-info').style.display = 'none';
    document.getElementById('products-title').textContent = ht('featured');
    loadCategories();
    loadProducts();
  }

  function scrollToProducts() {
    document.getElementById('products-section').scrollIntoView({ behavior: 'smooth' });
  }

  // 暴露给行内 onclick
  window.doSearch = doSearch;
  window.scrollToProducts = scrollToProducts;
  window.__clearSearch = clearSearch;
  window.__goCategory = function (id) {
    state.categoryId = id;
    state.keyword = '';
    document.getElementById('search-input').value = '';
    document.getElementById('search-info').style.display = 'none';
    loadCategories();
    loadProducts();
  };
  window.__goDetail = function (id) { window.location.href = '/products.html?id=' + id; };

  // 初始化
  (function init() {
    localize();
    App.renderNav('home');
    loadCategories();
    loadProducts();
  })();
})();