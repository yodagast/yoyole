/* =========================================================
   PyMall Vue 共享层
   基于 Vue 3 Global Build，提供：
   - 多语言 (composable: useI18n)
   - API 封装
   - 全局导航/登录弹窗/Toast 组件
   ========================================================= */
(function (global) {
  'use strict';

  var Vue = global.Vue;

  // ---------- 备案主体信息（工信部要求公开展示） ----------
  // 网站主办单位名称 + ICP 备案号统一在此定义，由页脚 SiteFooter 展示，
  // 避免多处硬编码导致备案信息与备案证书不一致（备案信息必须与工信部系统一致）。
  // 注意：不再写进 <title>（全称过长会被标签页截断），只出现在页脚。
  var COMPANY_NAME = '杭州余杭波动粒子信息经营部';
  var ICP_BEIAN_NO = '浙ICP备2026077052号';
  var ICP_BEIAN_URL = 'https://beian.miit.gov.cn/';

  // ---------- 公安联网备案（公安部「全国互联网安全管理服务平台」） ----------
  // 公安部要求：备案号必须链接到 beian.mps.gov.cn 查询页，
  // 且必须与官方盾牌图标一起展示（缺图标是常见不合规点）。
  var GONGAN_BEIAN_NO = '浙公网安备33011002020623号';
  var GONGAN_BEIAN_URL = 'https://beian.mps.gov.cn/#/query/webSearch?code=33011002020623';
  var GONGAN_BEIAN_ICON = '/static/img/gongan-beian.png';

  // 品牌信息：标签页标题用的短品牌名 + 首页标题。
  var BRAND_NAME = 'YOYOLE';
  var SITE_TITLE = '瑜伽与户外-YOYOLE';

  // 页面标题统一追加品牌名。
  // 历史做法是追加主办单位全称，但全称过长，浏览器标签页里会被截断成
  // 「YOYOLE | 瑜伽与户外生活 | 杭州余杭波动粒子信息经...」，页面名和品牌
  // 反而都看不全；备案主体名与两个备案号仍在页脚完整展示，备案合规不受影响。
  function siteTitle(text) {
    return text ? text + ' - ' + BRAND_NAME : SITE_TITLE;
  }

  // ---------- 翻译表（与后端 i18n.py 一致） ----------
  var TRANSLATIONS = {
    zh: {
      home: '首页', products: '全部商品', cart: '购物车', orders: '我的订单',
      login: '登录', register: '注册', logout: '退出登录', admin: '管理后台',
      search_placeholder: '搜索商品...',
      hot_categories: '热门分类', featured: '推荐商品', hot_sale: '热销商品', all_products: '全部商品',
      all_orders: '全部订单',
      add_to_cart: '加入购物车', buy_now: '立即购买', stock: '库存', sold: '已售',
      quantity: '数量', checkout: '结算', submit_order: '提交订单',
      receiver_name: '收货人', receiver_phone: '联系电话', receiver_address: '收货地址',
      remark: '备注(选填)', payment_method: '支付方式', pay_now: '立即支付',
      order_no: '订单号', order_status: '订单状态', empty_cart: '购物车是空的',
      go_shopping: '去逛逛', language: '语言', email: '邮箱', password: '密码',
      full_name: '姓名', mock_pay: '模拟支付', alipay: '支付宝', wechat: '微信支付', stripe: 'Stripe',
      pending: '待支付', paid: '已支付', shipped: '已发货', completed: '已完成',
      cancelled: '已取消', refunded: '已退款', status: '状态', actions: '操作',
      cancel_order: '取消订单', confirm_receipt: '确认收货', no_orders: '暂无订单',
      subtotal: '商品小计', shipping_fee: '运费', discount: '优惠', total: '合计',
      unit_price: '单价', delete: '删除', not_logged_in: '请先登录',
      no_account: '还没有账号？', have_account: '已有账号？',
      operation_success: '操作成功', operation_failed: '操作失败',
      loading: '加载中...', no_result: '未找到相关商品', clear_search: '清除搜索',
      search_result: '搜索结果', welcome: '欢迎', empty_cart_desc: '去挑选心仪的商品吧',
      receive_info: '收货信息', order_summary: '订单汇总', safe_pay: '安全支付',
      free_ship: '全球免运费', easy_return: '7天无理由退换', support: '在线客服 7×24',
      free_ship_desc: '满额即享免费配送', safe_pay_desc: '多重支付保障',
      easy_return_desc: '无理由退换货', support_desc: '专业客服团队',
      subscribe_title: '订阅优惠信息', subscribe_desc: '第一时间获取新品与折扣',
      subscribe_btn: '订阅', subscribe_ph: '输入您的邮箱', subscribe_ok: '订阅成功！',
      about_us: '关于 YOYOLE', about_desc: 'YOYOLE 专注瑜伽与户外生活，陪你在每一次呼吸和出发中找到力量。',
      cust_service: '客户服务', ship_info: '配送说明', return_policy: '退换政策',
      privacy: '隐私条款', contact_us: '联系我们', payment_icons: '支付宝 · 微信 · Stripe',
      need_login: '请先登录后再操作', cart_updated: '购物车已更新',
      added_to_cart: '已加入购物车', brand: '品牌', sku: '规格',
      home_new: 'MOVE WITH PURPOSE', home_title: '瑜伽与户外，向自然出发',
      home_sub: '为练习、远行与日常生活准备舒适耐用的装备',
      hero_kicker: '精选推荐',
      home2_tag: '夏日特惠', home2_title: '夏日焕新季',
      home2_sub: '潮流单品低至 5 折，全场包邮，限时开启',
      home3_tag: '新品首发', home3_title: '新品上架',
      home3_sub: '每周上新，抢先体验本季最热设计',
      shop_now: '立即选购', all_categories: '全部',
      order_detail: '订单详情', receiver: '收货人', order_items: '订单商品',
      order_time: '下单时间', pay_time: '支付时间', ship_time: '发货时间',
      shipping_fee_detail: '运费', discount_detail: '优惠', payment_amount: '实付款',
      order_logistics: '物流信息', logistics_pending: '等待发货',
      confirm: '确认', cancel: '取消', back: '返回',
      admin: '管理后台', my_orders: '我的订单',
      // ---- 我的（账户中心） ----
      my_account: '我的', my_account_center: '个人中心', my_profile: '我的资料',
      pending_orders_title: '待支付', paid_orders_title: '已支付订单',
      my_addresses_title: '我的地址', my_reviews_title: '我的评价',
      my_wishlist_title: '我的收藏', my_after_sales: '我的售后',
      edit: '编辑', save: '保存', phone: '手机号', view_all: '查看全部',
      profile_intro: '管理您的联系方式与账户偏好', account_active: '账户正常',
      email_secure_hint: '邮箱用于登录和接收订单通知，暂不支持直接修改。',
      phone_placeholder: '请输入手机号（可选）', change_password: '修改密码',
      manage_addresses: '管理收货地址', view_order_history: '查看订单记录',
      profile_load_failed: '资料加载失败，请检查网络后刷新页面。',
      confirm_delete: '确认删除该条记录？',
      approved_review: '已通过', review_pending: '审核中',
      after_sales_empty: '暂无售后记录', after_sales_tip: '如有退换货需求，请联系在线客服',
      // ---- 我的故事（账户中心 tab） ----
      my_stories: '我的故事', publish_story: '发布新故事',
      story_title_label: '标题', story_tags_label: '标签（逗号/顿号分隔，最多 6 个）',
      story_content_label: '内容', story_image_label: '配图（可选）',
      story_title_ph: '分享标题', story_content_ph: '写下你的 YOYOLE 瑜伽、户外或生活故事',
      story_tags_ph: '例如：晨练, 冥想, 露营',
      save_edit: '保存修改', submit_story: '提交分享', cancel_edit: '取消编辑',
      story_editing_tip: '正在编辑，保存后需重新审核',
      story_empty_list: '还没有投稿，点击「发布新故事」分享你的第一个故事吧。',
      status_approved: '已发布', status_rejected: '已驳回', status_pending: '待审核',
      reject_reason_label: '驳回理由：',
      story_saved_edit: '已保存，修改后需重新审核', story_submitted: '已提交，等待审核后展示',
      image_uploaded: '图片已上传', story_fill_required: '请填写标题和分享内容',
      story_edit_tip: '编辑故事', story_upload_tip: '故事图片上传',
      story_upload_fail: '图片上传失败', story_submit_fail: '提交失败',
      // ---- 登录引导 ----
      orders_login_tip: '登录后查看您的订单', cart_login_tip: '登录后查看和管理您的购物车',
      // ---- About Us ----
      about: '关于我们', brand_story: 'YOYOLE 故事', about_hero_tag: 'ABOUT YOYOLE',
      stories: '用户故事',
      about_hero_title: 'YOYOLE，让身体回到自然',
      about_hero_sub: '从瑜伽练习到山野远行，用认真设计的装备支持每一次自在出发。',
      our_story: '我们的故事', our_story_kicker: 'OUR STORY',
      our_story_p1: 'YOYOLE 创立于 2020 年，从一群热爱瑜伽与户外生活的人开始，',
      our_story_p2: '我们相信好的装备不应限制身体，而应让人与自然连接得更深。',
      mission_title: '我们的使命', mission_kicker: 'MISSION',
      mission_desc: '让每个人都能用舒适、耐用的装备亲近身体，亲近自然。',
      mission_p1: '为身体留出空间', mission_p2: '为每次出发做好准备',
      discover_title: '用户故事', discover_kicker: 'USER STORIES',
      discover_desc: '分享你的练习、远行与和自然相处的时刻。',
      discover_1_title: '瑜伽练习', discover_1_desc: '从晨间呼吸到稳定体式，找到自己的节奏',
      discover_2_title: '户外出发', discover_2_desc: '轻装走进山野，把风景带回生活',
      discover_3_title: '用户故事', discover_3_desc: '分享真实体验，与同样热爱生活的人相遇',
      // ---- 用户故事页 ----
      stories_hint: '点击故事卡片查看详情、点赞与评论',
      stories_all: '全部',
      stories_filter_hint: '按标签筛选故事',
      stories_empty: '还没有该标签的用户故事，成为第一个分享的人吧。',
      // ---- 用户故事详情页 ----
      story_detail_title: '用户故事详情',
      story_not_found: '用户故事不存在或未发布',
      back_to_stories: '返回用户故事',
      liked: '已赞', like: '点赞',
      comments_count: '共 {0} 条评论',
      comments: '评论',
      no_comments: '还没有评论，来抢沙发～',
      comment_placeholder: '说点什么…（登录后发表）',
      comment_submit: '发表',
      comment_posted: '评论已发表',
      comment_failed: '评论失败',
      milestones_title: '成长历程', milestones_kicker: 'MILESTONES',
      about_milestones_sub: '一步一步，把想法做成穿在身上的装备',
      m1_time: '2020', m1_title: '品牌创立', m1_desc: 'YOYOLE 从瑜伽与户外爱好者的真实需求出发',
      m2_time: '2022', m2_title: '走进山野', m2_desc: '推出轻量、耐用的户外出行系列',
      m3_time: '2024', m3_title: '练习相遇', m3_desc: '与更多瑜伽社群分享身体与呼吸的练习',
      m4_time: '2026', m4_title: '继续出发', m4_desc: '让自然友好的设计陪伴更多日常旅程',
      values_title: '我们的价值观', values_kicker: 'VALUES',
      about_values_sub: '支撑我们做每一个决定的三件事',
      about_story_quote: '装备是身体的延伸，而不是对身体的限制。',
      about_story_quote_by: '— YOYOLE',
      v1_title: '自然真实', v1_desc: '尊重身体感受，也尊重自然节律',
      v2_title: '简洁耐用', v2_desc: '减少多余设计，让装备经得起时间',
      v3_title: '一起成长', v3_desc: '与练习者和户外伙伴共同探索',
      about_cta_title: '把每一次出发，变成更好的自己',
      about_cta_desc: '从一块瑜伽垫到一座远山，YOYOLE 用认真设计的装备陪你练习、远行与日常。',
      about_footer_desc: 'YOYOLE 专注瑜伽与户外生活，让每一次呼吸和出发都更自在。',
      // ---- 商品详情（PDD 风格） ----
      prod_service_guarantee: '正品保障', prod_service_return: '7天无理由退货',
      prod_service_refund: '退货包运费', prod_service_ship: '极速发货',
      specifications: '规格参数', details: '商品详情', detail_desc_long: '图文详情',
      spec_brand: '品牌', spec_code: '货号', spec_fabric: '面料成分',
      spec_size: '尺码', spec_color: '颜色', spec_weight: '重量',
      select_color: '选择颜色', select_size: '选择尺码', spec: '规格',
      sold_count: '已售', bought_count: '已拼', ship_tip: '全场包邮，48小时内发货',
      size_chart: '尺码表', size_chart_preview: '尺码信息', pcs: '件',
      // ---- 收藏 ----
      wishlist: '收藏', my_wishlist: '我的收藏', added_to_wishlist: '已加入收藏',
      removed_from_wishlist: '已取消收藏', wishlist_empty: '收藏夹是空的',
      wishlist_empty_desc: '点击商品上的 ★ 收藏心仪好物',
      wishlisted: '已收藏', add_wishlist: '加入收藏', go_shopping: '去逛逛',
      clear_wishlist: '清空收藏',
      // ---- 评价 ----
      reviews: '商品评价', review_count: '条评价', write_review: '写评价',
      review_title: '评价标题', review_content: '评价内容', review_rating: '评分',
      review_submit: '提交评价', review_success: '评价提交成功',
      review_placeholder: '分享您的购物体验...', login_to_review: '登录后发表评价',
      no_reviews: '还没有评价，快来抢沙发~', review_required: '请填写评分和评价内容',
      review_submitted: '评价已提交，审核通过后展示', already_reviewed: '您已评价过该商品',
      // ---- 地址 ----
      my_addresses: '收货地址', add_address: '新增地址', address: '地址',
      addr_name: '收货人', addr_phone: '联系电话', addr_detail: '详细地址',
      addr_default: '默认地址',
      // ---- 高级筛选 ----
      filter: '筛选', min_price: '最低价', max_price: '最高价', sort_by: '排序',
      sort_default: '默认排序', sort_price_asc: '价格从低到高', sort_price_desc: '价格从高到低',
      sort_sales_desc: '销量优先', sort_newest: '新品上架', sort_favorites_desc: '收藏最多', compare: '对比', no_wishlist: '暂无收藏',
      confirm: '确定', reset: '重置',
      // ---- 邮箱验证码 ----
      verify_code: '验证码', verify_code_ph: '6 位数字验证码', send_code: '发送验证码',
      resend_in: '重新发送(', code_sent: '验证码已发送，请查收邮件', required: '不能为空',
      verify_code_required: '请先获取并填写邮箱验证码',
      code_sent_dev: '开发模式验证码',
      // ---- 忘记密码 ----
      forgot_password: '忘记密码？', reset_password: '重置密码', new_password: '新密码',
      new_password_ph: '输入新密码（至少 6 位）', back_to_login: '返回登录',
      reset_password_ok: '密码已重置，请使用新密码登录',
      confirm_password: '确认密码', confirm_password_ph: '再次输入密码',
      password_mismatch: '两次输入的密码不一致',
      invalid_email: '邮箱格式不正确', password_too_short: '密码至少 6 位',
    },
    en: {
      home: 'Home', products: 'Products', cart: 'Cart', orders: 'My Orders',
      login: 'Login', register: 'Register', logout: 'Logout', admin: 'Admin',
      search_placeholder: 'Search products...',
      hot_categories: 'Hot Categories', featured: 'Featured', hot_sale: 'Best Sellers', all_products: 'All Products',
      all_orders: 'All Orders',
      add_to_cart: 'Add to Cart', buy_now: 'Buy Now', stock: 'Stock', sold: 'Sold',
      quantity: 'Qty', checkout: 'Checkout', submit_order: 'Place Order',
      receiver_name: 'Full Name', receiver_phone: 'Phone', receiver_address: 'Address',
      remark: 'Note (Optional)', payment_method: 'Payment', pay_now: 'Pay Now',
      order_no: 'Order No.', order_status: 'Status', empty_cart: 'Your cart is empty',
      go_shopping: 'Go Shopping', language: 'Language', email: 'Email', password: 'Password',
      full_name: 'Name', mock_pay: 'Mock Pay', alipay: 'Alipay', wechat: 'WeChat', stripe: 'Stripe',
      pending: 'Pending', paid: 'Paid', shipped: 'Shipped', completed: 'Completed',
      cancelled: 'Cancelled', refunded: 'Refunded', status: 'Status', actions: 'Actions',
      cancel_order: 'Cancel', confirm_receipt: 'Confirm Receipt', no_orders: 'No orders yet',
      subtotal: 'Subtotal', shipping_fee: 'Shipping', discount: 'Discount', total: 'Total',
      unit_price: 'Unit Price', delete: 'Delete', not_logged_in: 'Please login',
      no_account: 'No account?', have_account: 'Already have an account?',
      operation_success: 'Success', operation_failed: 'Failed',
      loading: 'Loading...', no_result: 'No products found', clear_search: 'Clear Search',
      search_result: 'Search Results', welcome: 'Welcome', empty_cart_desc: 'Find something you love',
      receive_info: 'Shipping Info', order_summary: 'Order Summary', safe_pay: 'Secure Payment',
      free_ship: 'Free Shipping', easy_return: '7-Day Returns', support: '24/7 Support',
      free_ship_desc: 'Free delivery over threshold', safe_pay_desc: 'Multiple payment options',
      easy_return_desc: 'Hassle-free returns', support_desc: 'Professional support team',
      subscribe_title: 'Subscribe & Save', subscribe_desc: 'Get new arrivals and exclusive offers',
      subscribe_btn: 'Subscribe', subscribe_ph: 'Enter your email', subscribe_ok: 'Subscribed!',
      about_us: 'About YOYOLE', about_desc: 'YOYOLE creates thoughtful gear for yoga, outdoor living, and everyday movement.',
      cust_service: 'Customer Service', ship_info: 'Shipping Info', return_policy: 'Return Policy',
      privacy: 'Privacy Policy', contact_us: 'Contact Us', payment_icons: 'Alipay · WeChat · Stripe',
      need_login: 'Please login first', cart_updated: 'Cart updated',
      added_to_cart: 'Added to cart', brand: 'Brand', sku: 'SKU',
      home_new: 'MOVE WITH PURPOSE', home_title: 'Yoga and outdoor, closer to nature',
      home_sub: 'Comfortable, durable gear for practice, travel, and everyday movement',
      hero_kicker: 'FEATURED',
      home2_tag: 'SUMMER SALE', home2_title: 'Summer Refresh Season',
      home2_sub: 'Up to 50% off selected styles · free shipping sitewide',
      home3_tag: 'NEW COLLECTION', home3_title: 'New Arrivals',
      home3_sub: 'New drops every week — be first to the season’s hottest designs',
      shop_now: 'Shop Now', all_categories: 'All',
      order_detail: 'Order Detail', receiver: 'Receiver', order_items: 'Items',
      order_time: 'Order Time', pay_time: 'Paid At', ship_time: 'Shipped At',
      shipping_fee_detail: 'Shipping Fee', discount_detail: 'Discount', payment_amount: 'Paid Amount',
      order_logistics: 'Logistics', logistics_pending: 'Awaiting Shipment',
      confirm: 'Confirm', cancel: 'Cancel', back: 'Back',
      admin: 'Admin', my_orders: 'My Orders',
      // ---- 我的（账户中心） ----
      my_account: 'My Account', my_account_center: 'Account Center', my_profile: 'My Profile',
      pending_orders_title: 'Pending Payment', paid_orders_title: 'Paid Orders',
      my_addresses_title: 'My Addresses', my_reviews_title: 'My Reviews',
      my_wishlist_title: 'My Wishlist', my_after_sales: 'After-sales',
      edit: 'Edit', save: 'Save', phone: 'Phone', view_all: 'View All',
      profile_intro: 'Manage your contact details and account preferences', account_active: 'Active account',
      email_secure_hint: 'Your email is used for sign-in and order updates and cannot be changed here.',
      phone_placeholder: 'Enter phone number (optional)', change_password: 'Change password',
      manage_addresses: 'Manage addresses', view_order_history: 'View order history',
      profile_load_failed: 'Unable to load your profile. Check your connection and refresh.',
      confirm_delete: 'Confirm delete this record?',
      approved_review: 'Approved', review_pending: 'Pending',
      after_sales_empty: 'No after-sales records', after_sales_tip: 'For returns or exchanges, please contact support',
      // ---- My stories (account tab) ----
      my_stories: 'My Stories', publish_story: 'Publish a Story',
      story_title_label: 'Title', story_tags_label: 'Tags (comma separated, max 6)',
      story_content_label: 'Content', story_image_label: 'Image (optional)',
      story_title_ph: 'Story title', story_content_ph: 'Share your YOYOLE yoga, outdoor or life story',
      story_tags_ph: 'e.g. morning practice, camp',
      save_edit: 'Save Changes', submit_story: 'Submit Story', cancel_edit: 'Cancel Edit',
      story_editing_tip: 'Editing — story will need re-review after saving',
      story_empty_list: 'No stories yet. Click “Publish a Story” to share your first one.',
      status_approved: 'Published', status_rejected: 'Rejected', status_pending: 'Pending',
      reject_reason_label: 'Reason: ',
      story_saved_edit: 'Saved — story will need re-review', story_submitted: 'Submitted — pending review before publishing',
      image_uploaded: 'Image uploaded', story_fill_required: 'Please fill in title and content',
      story_edit_tip: 'Edit story', story_upload_tip: 'Story image upload',
      story_upload_fail: 'Image upload failed', story_submit_fail: 'Submit failed',
      // ---- 登录引导 ----
      orders_login_tip: 'Log in to view your orders', cart_login_tip: 'Log in to view and manage your cart',
      // ---- About Us ----
      about: 'About Us', brand_story: 'Our YOYOLE Story', about_hero_tag: 'ABOUT YOYOLE',
      stories: 'Stories',
      about_hero_title: 'YOYOLE brings the body back to nature',
      about_hero_sub: 'Thoughtful gear for yoga practice, outdoor adventures, and a life in motion.',
      our_story: 'Our Story', our_story_kicker: 'OUR STORY',
      our_story_p1: 'Founded in 2020, YOYOLE began with people who love yoga and the outdoors.',
      our_story_p2: 'We believe good gear should free the body and deepen our connection with nature.',
      mission_title: 'Our Mission', mission_kicker: 'MISSION',
      mission_desc: 'Help every user discover unique and fun products, and support artists and designers.',
      mission_p1: 'Make great design visible', mission_p2: 'Bring great works to the world',
      discover_title: 'User Stories', discover_kicker: 'USER STORIES',
      discover_desc: 'Share your practice, your journeys, and moments with nature.',
      discover_1_title: 'Yoga Practice', discover_1_desc: 'From morning breath to steady poses, find your rhythm',
      discover_2_title: 'Outdoor Escapes', discover_2_desc: 'Travel light into the wild, bring scenery back to life',
      discover_3_title: 'User Stories', discover_3_desc: 'Share real experiences and meet like-minded people',
      // ---- User stories page ----
      stories_hint: 'Click a story card to view details, like and comment',
      stories_all: 'All',
      stories_filter_hint: 'Filter stories by tag',
      stories_empty: 'No stories with this tag yet. Be the first to share one.',
      // ---- Story detail page ----
      story_detail_title: 'Story Detail',
      story_not_found: 'Story not found or not published',
      back_to_stories: 'Back to Stories',
      liked: 'Liked', like: 'Like',
      comments_count: '{0} comments',
      comments: 'Comments',
      no_comments: 'No comments yet. Be the first!',
      comment_placeholder: 'Say something… (log in to comment)',
      comment_submit: 'Post',
      comment_posted: 'Comment posted',
      comment_failed: 'Failed to post comment',
      milestones_title: 'Milestones', milestones_kicker: 'MILESTONES',
      about_milestones_sub: 'Step by step, turning ideas into gear you can wear',
      m1_time: '2020', m1_title: 'Founded', m1_desc: 'PyMall launched with 50 designers on board',
      m2_time: '2022', m2_title: 'Global Expansion', m2_desc: 'Serving customers in 30+ countries',
      m3_time: '2024', m3_title: '1M Members', m3_desc: '1 million members, leading community',
      m4_time: '2026', m4_title: 'Keep Innovating', m4_desc: 'AI-powered recommendations & immersive shopping',
      values_title: 'Our Values', values_kicker: 'VALUES',
      about_values_sub: 'Three things behind every decision we make',
      about_story_quote: 'Gear should extend the body, never confine it.',
      about_story_quote_by: '\u2014 YOYOLE',
      v1_title: 'Creativity First', v1_desc: 'Respect every expression of creativity',
      v2_title: 'Sincere Service', v2_desc: 'User-centric, sincere and responsible',
      v3_title: 'Global Vision', v3_desc: 'Connecting artists and enthusiasts worldwide',
      about_cta_title: 'Turn every departure into a better you',
      about_cta_desc: 'From a yoga mat to a distant mountain, YOYOLE\u2019s thoughtfully designed gear accompanies you through practice, travel, and everyday life.',
      about_footer_desc: 'PyMall is a global designer toy platform connecting artists and fans.',
      // ---- Product detail (PDD style) ----
      prod_service_guarantee: 'Authentic Guarantee', prod_service_return: '7-Day Free Returns',
      prod_service_refund: 'Return Shipping Covered', prod_service_ship: 'Fast Shipping',
      specifications: 'Specifications', details: 'Product Details', detail_desc_long: 'Gallery Details',
      spec_brand: 'Brand', spec_code: 'Item No.', spec_fabric: 'Fabric',
      spec_size: 'Size', spec_color: 'Color', spec_weight: 'Weight',
      select_color: 'Select Color', select_size: 'Select Size', spec: 'Spec',
      sold_count: 'Sold', bought_count: 'Group-Bought', ship_tip: 'Free shipping, ships within 48h',
      size_chart: 'Size Chart', size_chart_preview: 'Size Info', pcs: 'pcs',
      // ---- 收藏 ----
      wishlist: 'Wishlist', my_wishlist: 'My Wishlist', added_to_wishlist: 'Added to wishlist',
      removed_from_wishlist: 'Removed from wishlist', wishlist_empty: 'Your wishlist is empty',
      wishlist_empty_desc: 'Tap the ★ on any product to save it', wishlisted: 'Saved',
      add_wishlist: 'Add to Wishlist', go_shopping: 'Start Shopping',
      clear_wishlist: 'Clear Wishlist',
      // ---- 评价 ----
      reviews: 'Reviews', review_count: 'reviews', write_review: 'Write a Review',
      review_title: 'Review Title', review_content: 'Review Content', review_rating: 'Rating',
      review_submit: 'Submit Review', review_success: 'Review submitted',
      review_placeholder: 'Share your shopping experience...', login_to_review: 'Log in to review',
      no_reviews: 'No reviews yet. Be the first!',
      review_required: 'Please add a rating and some content',
      review_submitted: 'Review submitted, will show after approval', already_reviewed: 'You have already reviewed this product',
      // ---- 地址 ----
      my_addresses: 'Shipping Addresses', add_address: 'Add Address', address: 'Address',
      addr_name: 'Full Name', addr_phone: 'Phone', addr_detail: 'Address Details',
      addr_default: 'Default',
      // ---- 高级筛选 ----
      filter: 'Filter', min_price: 'Min Price', max_price: 'Max Price', sort_by: 'Sort By',
      sort_default: 'Default', sort_price_asc: 'Price: Low to High', sort_price_desc: 'Price: High to Low',
      sort_sales_desc: 'Best Selling', sort_newest: 'New Arrivals', sort_favorites_desc: 'Most Wished', compare: 'Compare', no_wishlist: 'No wishlist items',
      confirm: 'Apply', reset: 'Reset',
      // ---- Email verification code ----
      verify_code: 'Verification Code', verify_code_ph: '6-digit code',
      send_code: 'Send Code', resend_in: 'Resend in (',
      code_sent: 'Verification code sent, please check your email',
      required: 'is required', verify_code_required: 'Please request and enter the verification code',
      code_sent_dev: 'Dev mode code',
      // ---- Forgot password ----
      forgot_password: 'Forgot password?', reset_password: 'Reset Password', new_password: 'New Password',
      new_password_ph: 'Enter new password (min 6 chars)', back_to_login: 'Back to login',
      reset_password_ok: 'Password reset, please login with new password',
      confirm_password: 'Confirm Password', confirm_password_ph: 'Enter password again',
      password_mismatch: 'Passwords do not match',
      invalid_email: 'Invalid email format', password_too_short: 'Password must be at least 6 characters',
    },
  };

  // ---------- 语言管理 ----------
  function getLang() {
    var lang = localStorage.getItem('lang');
    if (!lang) {
      var m = document.cookie.match(/(?:^|; )lang=([^;]+)/);
      lang = m ? m[1] : 'zh';
    }
    return lang === 'zh' || lang === 'en' ? lang : 'zh';
  }

  function setLang(lang) {
    localStorage.setItem('lang', lang);
    document.cookie = 'lang=' + lang + '; path=/; max-age=31536000';
  }

  // ---------- Token ----------
  function getToken() {
    var tok = localStorage.getItem('access_token');
    if (!tok) {
      var m = document.cookie.match(/(?:^|; )access_token=([^;]+)/);
      tok = m ? decodeURIComponent(m[1]) : null;
    }
    return tok;
  }
  function setToken(tok) {
    localStorage.setItem('access_token', tok);
    document.cookie = 'access_token=' + encodeURIComponent(tok) + '; path=/; max-age=604800';
  }
  function clearToken() {
    localStorage.removeItem('access_token');
    document.cookie = 'access_token=; path=/; max-age=0';
  }

  /**
   * 读取后台（管理员）token。
   * 前台页面用于「后台预览」：管理员已登录时可用后台接口预览未上架/未过审商品。
   * 口径与 common.js 的 App.getAdminToken 一致（localStorage → cookie 回退）。
   */
  function getAdminToken() {
    try {
      var tok = localStorage.getItem('admin_token');
      if (!tok) {
        var m = document.cookie.match(/(?:^|; )admin_token=([^;]+)/);
        tok = m ? decodeURIComponent(m[1]) : null;
      }
      return tok;
    } catch (e) {
      return null;
    }
  }

  // ---------- API ----------
  function api(path, options) {
    options = options || {};
    var headers = options.headers || {};
    headers['Content-Type'] = 'application/json';
    if (options.token) {
      headers['Authorization'] = 'Bearer ' + options.token;
    }
    if (options.noLang) {
      // 无 lang 参数
    } else {
      path += (path.indexOf('?') >= 0 ? '&' : '?') + 'lang=' + getLang();
    }
    return fetch(path, {
      method: options.method || 'GET',
      headers: headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (res.ok) return data;
        // 401 且携带 token：说明凭证已失效/过期，立即清除，避免"假登录"死循环
        if (res.status === 401 && options.token) {
          clearToken();
          store.token = null;
        }
        var err = new Error(data.detail || data.message || ('HTTP ' + res.status));
        err.status = res.status;
        throw err;
      });
    });
  }

  // ---------- 工具 ----------
  function money(v) {
    var n = Number(v);
    if (isNaN(n)) return '¥0.00';
    return '¥' + n.toFixed(2);
  }

  // ---------- 全局共享 Store ----------
  // 简单的响应式状态（供 Vue 3 reactive 使用）
  var store = Vue.reactive({
    lang: getLang(),
    token: getToken(),
    adminToken: null,
    cartCount: 0,
    wishlistCount: 0,
    authModal: false,
    authMode: 'login', // 'login' | 'register' | 'reset'
    cartItems: [],
  });

  // ---------- I18n Composable ----------
  function t(key) {
    var table = TRANSLATIONS[store.lang] || TRANSLATIONS.zh;
    return table[key] !== undefined ? table[key] : (TRANSLATIONS.zh[key] || key);
  }

  // x 替换占位符：tt('xx {0} {1}', a, b)
  function tt(key) {
    var s = t(key);
    for (var i = 1; i < arguments.length; i++) {
      s = s.replace('{' + (i - 1) + '}', arguments[i]);
    }
    return s;
  }

  function switchLang(lang) {
    if (lang === store.lang) return;   // 语言未变则无需刷新
    setLang(lang);
    store.lang = lang;
    // 刷新页面：让所有页面数据（标题、about 页 state、用户故事等）按新语言完整重载，
    // 避免只更新 store.lang 导致 setup 阶段已初始化的文案残留旧语言
    setTimeout(function () { location.reload(); }, 60);
  }

  function localName(i18nObj, fallback) {
    if (!i18nObj) return fallback || '';
    return i18nObj[store.lang] || i18nObj.zh || i18nObj.en || fallback || '';
  }

  // ---------- 认证 ----------
  // 登录/登出/注册 的事件回调（页面可注册以刷新登录态的个性化数据，如「我的投稿」）
  var _authListeners = [];
  function onAuthChange(fn) {
    if (typeof fn === 'function') _authListeners.push(fn);
    return function () {
      var i = _authListeners.indexOf(fn);
      if (i > -1) _authListeners.splice(i, 1);
    };
  }
  function _notifyAuthChange() {
    _authListeners.forEach(function (fn) { try { fn(); } catch (e) {} });
  }

  function login(email, password) {
    return api('/api/auth/login', {
      method: 'POST', noLang: true,
      body: { email: email, password: password },
    }).then(function (data) {
      setToken(data.access_token);
      store.token = data.access_token;
      store.authModal = false;
      refreshCart();
      refreshWishlist();
      _notifyAuthChange();
      return data;
    });
  }

  function register(body) {
    if (body && !body.language) body.language = store.lang;
    return api('/api/auth/register', {
      method: 'POST', noLang: true, body: body,
    }).then(function (data) {
      setToken(data.access_token);
      store.token = data.access_token;
      store.authModal = false;
      refreshCart();
      refreshWishlist();
      _notifyAuthChange();
      return data;
    });
  }

  function logout() {
    clearToken();
    store.token = null;
    store.cartCount = 0;
    store.wishlistCount = 0;
    _notifyAuthChange();
    location.href = '/';
  }

  function refreshCart() {
    if (!getToken()) {
      store.cartCount = 0;
      return Promise.resolve(store.cartCount);
    }
    return api('/api/cart', { token: getToken() }).then(function (data) {
      var n = data.items ? data.items.reduce(function (s, i) { return s + i.quantity; }, 0) : 0;
      store.cartCount = n;
      return n;
    }).catch(function () { store.cartCount = 0; return 0; });
  }

  // 刷新收藏数量
  function refreshWishlist() {
    if (!getToken()) {
      store.wishlistCount = 0;
      return Promise.resolve(0);
    }
    return api('/api/wishlist', { token: getToken() }).then(function (data) {
      var items = data.items || data || [];
      store.wishlistCount = Array.isArray(items) ? items.length : 0;
      return store.wishlistCount;
    }).catch(function () { store.wishlistCount = 0; return 0; });
  }

  // 立即购买：商品卡片快捷购买（自动取第一个有货 SKU 加入购物车并跳转结算）
  function buyNow(p) {
    if (!getToken()) {
      openAuth('login');
      return;
    }
    api('/api/products/' + p.id).then(function (detail) {
      var skus = (detail.skus || []).filter(function (s) { return s.is_active && s.available_stock > 0; });
      if (!skus.length) {
        toast(t('sku'), 'error');
        return null;
      }
      return api('/api/cart/items', {
        method: 'POST', token: getToken(),
        body: { sku_id: skus[0].id, quantity: 1 },
      });
    }).then(function (data) {
      if (data) location.href = '/cart.html';
    }).catch(function (e) {
      toast(e.message, 'error');
    });
  }

  // ---------- SVG 图标库（替代 emoji） ----------
  var ICONS = {
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    cart: '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/>',
    user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    truck: '<path d="M15 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.62l-3.48-4.35a1 1 0 0 0-.78-.38H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
    box: '<path d="m7.5 4.27 9 5.15"/><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
    wallet: '<path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4Z"/>',
    location: '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    star: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    review: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    headset: '<path d="M3 11h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5Z"/><path d="M21 11h-3a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-5Z"/><path d="M21 16v2a4 4 0 0 1-4 4h-5"/><path d="M3 16v2a4 4 0 0 0 4 4h2"/>',
    chevronRight: '<path d="m9 18 6-6-6-6"/>',
    arrowRight: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>',
    save: '<path d="M15.2 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8.8Z"/><path d="M15 3v6h6"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/>',
    pencil: '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
    trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    mail: '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    phone: '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
    shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
    refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    card: '<rect width="20" height="14" x="2" y="5" rx="2"/><line x1="2" x2="22" y1="10" y2="10"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    bag: '<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/>',
    zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    tag: '<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r=".5"/>',
    settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    filter: '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    home: '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    fire: '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3 1.072-2.143 2.5-3 4.5-4.5 2 4 1 7.5-1 9a5 5 0 0 1-8-5c-.5 1-1 3 1 6z"/>',
    plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
    close: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
    bulb: '<path d="M9 18h6"/><path d="M10 22h4"/><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1.23.47 2.34 1.5 3.5.76.76 1.22 1.52 1.41 2.5"/>',
    palette: '<circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/>',
    handshake: '<path d="m11 17 2 2a1 1 0 1 0 3-3"/><path d="m14 14 2.5 2.5a1 1 0 1 0 3-3l-3.88-3.88a3 3 0 0 0-4.24 0l-.88.88a1 1 0 1 1-3-3l2.81-2.81a5.79 5.79 0 0 1 7.06-.87l.47.28a2 2 0 0 0 1.42.25L21 4"/><path d="m21 3 1 11h-2"/><path d="M3 3 2 14l6.5 6.5a1 1 0 1 0 3-3"/><path d="M3 4h8"/>',
  };

  // 图标组件
  var BaseIcon = {
    props: {
      name: { type: String, required: true },
      size: { type: [Number, String], default: 18 },
      fill: { type: Boolean, default: false },
    },
    template: `<svg :width="size" :height="size" viewBox="0 0 24 24"
      :fill="fill ? 'currentColor' : 'none'"
      :stroke="fill ? 'none' : 'currentColor'"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
      style="flex-shrink:0;vertical-align:middle" v-html="path"></svg>`,
    computed: {
      path: function () { return ICONS[this.name] || ''; },
    },
  };

  // ---------- 全局组件 ----------
  // 顶部服务条
  var TopBar = {
    template: `
      <div class="topbar">
        <div class="topbar-inner">
          <span><base-icon name="truck" :size="14"></base-icon> {{ t('free_ship') }} · {{ t('easy_return') }}</span>
        </div>
      </div>`,
    setup() {
      return { t };
    },
  };

  // 主导航
  var MainNav = {
    props: ['active'],
    template: `
      <nav class="main-nav">
        <div class="nav-inner">
          <div class="brand" @click="goHome">
            <img class="brand-logo" src="/static/img/yoyole-logo.svg" alt="YOYOLE">
          </div>
          <div class="nav-links">
            <a href="/" class="link" :class="{active: active==='home'}">{{ t('home') }}</a>
            <a href="/products.html" class="link" :class="{active: active==='products'}">{{ t('products') }}</a>
            <a href="/stories.html" class="link" :class="{active: active==='stories'}">{{ t('stories') }}</a>
            <a href="/about.html" class="link" :class="{active: active==='about'}">{{ t('about') }}</a>
          </div>
          <div class="nav-actions">
            <button class="nav-icon-button" type="button" :aria-label="t('search')" :title="t('search')" @click="toggleSearch">
              <base-icon name="search" :size="21"></base-icon>
            </button>
            <div class="nav-search" v-if="searchOpen">
              <input :placeholder="t('search_placeholder')" v-model="kw"
                     @keyup.enter="onSearch" @input="onKWChange" @focus="onKWFocus" @blur="onKWBlur" />
              <button class="btn-search" @click="onSearch">{{ t('search') }}</button>
              <div class="ac-panel" v-if="suggestions.length && acOpen">
                <div class="ac-item" v-for="s in suggestions" :key="s.id" @mousedown.prevent="pickSuggestion(s)">
                  <img v-if="s.main_image" :src="s.main_image" style="width:34px;height:34px;object-fit:cover;border-radius:6px">
                  <div style="flex:1;min-width:0">
                    <div class="ac-name">{{ s.display_name || s.sku_code }}</div>
                  </div>
                  <span style="color:var(--jjs-primary);font-weight:600;white-space:nowrap">{{ money(s.base_price) }}</span>
                </div>
              </div>
            </div>
            <a href="/cart.html" class="cart-link" :title="t('cart')" :aria-label="t('cart')"><base-icon name="cart" :size="21"></base-icon>
              <span class="cart-badge" v-if="cartCount > 0">{{ cartCount }}</span>
            </a>
            <a href="/account.html" class="account-link" :title="t('my_account')" :aria-label="t('my_account')" @click.prevent="loggedIn ? goAccount() : openAuth('login')"><base-icon name="user" :size="21"></base-icon></a>
            <button class="lang-switch" type="button" :title="t('language')" :aria-label="t('language')"
                    @click="switchLang(lang === 'zh' ? 'en' : 'zh')">{{ lang === 'zh' ? '中' : 'EN' }}</button>
          </div>
        </div>
      </nav>`,
    setup(props) {
      const state = Vue.reactive({
        kw: '',
        searchOpen: false,
        suggestions: [],
        acOpen: false,
        acTimer: null,
      });
      Vue.onMounted(() => {
        refreshCart();
        refreshWishlist();
      });
      function goHome() { location.href = '/'; }
      function goOrders() { location.href = '/orders.html'; }
      function goAccount() { location.href = '/account.html'; }
      function toggleSearch() { state.searchOpen = !state.searchOpen; }
      function onSearch() {
        if (state.kw && state.kw.trim()) {
          location.href = '/products.html?q=' + encodeURIComponent(state.kw.trim());
        }
      }
      function onKWChange() {
        clearTimeout(state.acTimer);
        if (!state.kw || !state.kw.trim()) { state.suggestions = []; return; }
        state.acTimer = setTimeout(() => {
          api('/api/products/autocomplete?q=' + encodeURIComponent(state.kw.trim()) + '&limit=5')
            .then(function (data) {
              var list = Array.isArray(data) ? data : ((data && data.items) || []);
              state.suggestions = list.map(function (s) {
                return {
                  id: s.id,
                  sku_code: s.sku_code,
                  display_name: s.name_zh || s.sku_code,
                  main_image: s.main_image,
                  base_price: s.base_price,
                };
              });
              state.acOpen = state.suggestions.length > 0;
            })
            .catch(function () { state.suggestions = []; });
        }, 300);
      }
      function onKWFocus() {
        if (state.suggestions.length) state.acOpen = true;
      }
      function onKWBlur() {
        setTimeout(function () { state.acOpen = false; }, 150);
      }
      function pickSuggestion(s) {
        clearTimeout(state.acTimer);
        state.acOpen = false;
        location.href = '/products.html?id=' + s.id;
      }
      return {
        t, tt, money,
        lang: Vue.computed(() => store.lang), cartCount: Vue.computed(() => store.cartCount),
        wishlistCount: Vue.computed(() => store.wishlistCount),
        loggedIn: Vue.computed(() => !!store.token),
        searchOpen: Vue.computed(() => state.searchOpen),
        suggestions: Vue.computed(() => state.suggestions),
        acOpen: Vue.computed(() => state.acOpen),
        kw: Vue.computed({
          get: () => state.kw,
          set: (v) => { state.kw = v; },
        }),
        switchLang, openAuth, onSearch, onKWChange, onKWFocus, onKWBlur, pickSuggestion, toggleSearch, goHome, goOrders, goAccount,
      };
    },
  };

  // 登录/注册弹窗
  var AuthModal = {
    template: `
      <div class="modal-mask" v-if="store.authModal" @click.self="close">
        <div class="modal">
          <button class="modal-close" @click="close">×</button>
          <h3>{{ title }}</h3>
          <div class="form-group">
            <label>{{ t('full_name') }}</label>
            <input type="text" v-model="form.fullName" v-if="store.authMode==='register'"
                   :placeholder="t('full_name')" />
          </div>
          <div class="form-group">
            <label>{{ t('email') }}</label>
            <input type="email" v-model="form.email" placeholder="email@example.com" />
          </div>
          <div class="form-group" v-if="store.authMode==='login'">
            <label>{{ t('password') }}</label>
            <input type="password" v-model="form.password" placeholder="********"
                   @keyup.enter="submit" />
          </div>
          <div class="form-group" v-if="store.authMode==='register'">
            <label>{{ t('password') }}</label>
            <input type="password" v-model="form.password" placeholder="********" />
          </div>
          <div class="form-group" v-if="store.authMode==='register'">
            <label>{{ t('confirm_password') }}</label>
            <input type="password" v-model="form.confirmPassword" :placeholder="t('confirm_password_ph')"
                   @keyup.enter="submit" />
          </div>
          <div class="form-group" v-if="store.authMode==='reset'">
            <label>{{ t('new_password') }}</label>
            <input type="password" v-model="form.newPassword" :placeholder="t('new_password_ph')" />
          </div>
          <div class="form-group" v-if="store.authMode==='reset'">
            <label>{{ t('confirm_password') }}</label>
            <input type="password" v-model="form.confirmPassword" :placeholder="t('confirm_password_ph')"
                   @keyup.enter="submit" />
          </div>
          <div class="form-group" v-if="store.authMode==='register' || store.authMode==='reset'">
            <label>{{ t('verify_code') }}</label>
            <div style="display:flex;gap:8px">
              <input type="text" v-model="form.code" :placeholder="t('verify_code_ph')"
                     style="flex:1" @keyup.enter="submit" />
              <button class="btn btn-outline btn-sm" type="button" @click="sendCode"
                      :disabled="cooldown > 0" style="white-space:nowrap">
                {{ cooldown > 0 ? t('resend_in') + cooldown + 's)' : t('send_code') }}
              </button>
            </div>
          </div>
          <div class="modal-actions">
            <button class="btn btn-primary btn-block" @click="submit">
              {{ submitLabel }}
            </button>
          </div>
          <div class="modal-switch">
            <span v-if="store.authMode==='login'">
              <a @click="store.authMode='reset'">{{ t('forgot_password') }}</a>
              <span style="margin:0 6px">|</span>{{ t('no_account') }}
              <a @click="store.authMode='register'">{{ t('register') }}</a>
            </span>
            <span v-else-if="store.authMode==='register'">{{ t('have_account') }}
              <a @click="store.authMode='login'">{{ t('login') }}</a>
            </span>
            <span v-else>
              <a @click="store.authMode='login'">{{ t('back_to_login') }}</a>
            </span>
          </div>
        </div>
      </div>`,
    setup() {
      const form = Vue.reactive({ email: '', password: '', fullName: '', code: '', newPassword: '', confirmPassword: '' });
      const cooldown = Vue.ref(0);
      let timer = null;

      // 模式切换时清空相关字段
      function watchMode(mode) {
        form.code = '';
        form.confirmPassword = '';
        if (mode !== 'reset') form.newPassword = '';
      }
      Vue.watch(() => store.authMode, (m) => watchMode(m));

      const title = Vue.computed(function () {
        if (store.authMode === 'register') return t('register');
        if (store.authMode === 'reset') return t('reset_password');
        return t('login');
      });
      const submitLabel = Vue.computed(function () {
        if (store.authMode === 'register') return t('register');
        if (store.authMode === 'reset') return t('reset_password');
        return t('login');
      });

      function close() { store.authModal = false; }

      // 发送验证码前校验：邮箱 + 密码（两次一致）
      function validateBeforeSendCode() {
        if (!form.email) { toast(t('email') + ' ' + t('required'), 'error'); return false; }
        var emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRe.test(form.email)) { toast(t('invalid_email'), 'error'); return false; }
        if (store.authMode === 'register') {
          if (!form.password) { toast(t('password') + ' ' + t('required'), 'error'); return false; }
          if (form.password.length < 6) { toast(t('password_too_short'), 'error'); return false; }
          if (form.password !== form.confirmPassword) { toast(t('password_mismatch'), 'error'); return false; }
        } else if (store.authMode === 'reset') {
          if (!form.newPassword || form.newPassword.length < 6) { toast(t('new_password_ph'), 'error'); return false; }
          if (form.newPassword !== form.confirmPassword) { toast(t('password_mismatch'), 'error'); return false; }
        }
        return true;
      }

      function sendCode() {
        if (cooldown.value > 0) return;
        // 邮箱/密码全部校验通过后才发送验证码
        if (!validateBeforeSendCode()) return;
        cooldown.value = 60;
        if (timer) clearInterval(timer);
        timer = setInterval(function () {
          cooldown.value--;
          if (cooldown.value <= 0) clearInterval(timer);
        }, 1000);
        var purpose = store.authMode === 'reset' ? 'reset' : 'register';
        api('/api/auth/send-code', {
          method: 'POST', noLang: true,
          body: { email: form.email, purpose: purpose },
        }).then(function (data) {
          if (data && data.debug_code) {
            form.code = data.debug_code;
            toast(t('code_sent_dev') + '：' + data.debug_code, 'success');
          } else {
            toast(t('code_sent'), 'success');
          }
        }).catch(function (e) {
          cooldown.value = 0;
          toast(e.message, 'error');
        });
      }

      function submit() {
        if (!form.email) { toast(t('email') + ' ' + t('required'), 'error'); return; }
        if (store.authMode === 'login') {
          if (!form.password) { toast(t('password') + ' ' + t('required'), 'error'); return; }
          login(form.email, form.password)
            .then(function () { toast(t('operation_success'), 'success'); })
            .catch(function (e) { toast(e.message, 'error'); });
        } else if (store.authMode === 'register') {
          if (!form.password) { toast(t('password') + ' ' + t('required'), 'error'); return; }
          if (form.password !== form.confirmPassword) { toast(t('password_mismatch'), 'error'); return; }
          if (!form.code) { toast(t('verify_code_required'), 'error'); return; }
          register({ email: form.email, password: form.password, full_name: form.fullName, code: form.code })
            .then(function () { toast(t('operation_success'), 'success'); })
            .catch(function (e) { toast(e.message, 'error'); });
        } else { // reset
          if (!form.newPassword || form.newPassword.length < 6) { toast(t('new_password_ph'), 'error'); return; }
          if (form.newPassword !== form.confirmPassword) { toast(t('password_mismatch'), 'error'); return; }
          if (!form.code) { toast(t('verify_code_required'), 'error'); return; }
          api('/api/auth/reset-password', {
            method: 'POST', noLang: true,
            body: { email: form.email, code: form.code, new_password: form.newPassword },
          }).then(function (data) {
            toast(data.message || t('reset_password_ok'), 'success');
            store.authMode = 'login';
            form.password = '';
            form.newPassword = '';
            form.confirmPassword = '';
            form.code = '';
          }).catch(function (e) { toast(e.message, 'error'); });
        }
      }
      return { store, t, form, cooldown, title, submitLabel, close, submit, sendCode };
    },
  };

  // Toast
  function toast(msg, type) {
    var el = document.createElement('div');
    el.className = 'toast ' + (type || '');
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 2500);
  }

  function openAuth(mode) {
    store.authMode = mode || 'login';
    store.authModal = true;
  }

  // ---------- 页脚公共组件 ----------
  var SiteFooter = {
    // 订阅优惠信息不再单独占一条通栏色带，而是作为页脚最后一列，
    // 紧邻「支付方式」右侧，避免通栏区块两侧大片留白、也让页脚更紧凑。
    template: `
      <footer class="footer">
        <div class="footer-inner">
          <div class="footer-col">
            <h4>{{ t('about_us') }}</h4>
            <p>{{ t('about_desc') }}</p>
            <a href="/about.html" class="footer-link">{{ t('brand_story') }} <base-icon name="chevronRight" :size="14"></base-icon></a>
          </div>
          <div class="footer-col">
            <h4>{{ t('cust_service') }}</h4>
            <ul>
              <li>{{ t('ship_info') }}</li>
              <li>{{ t('return_policy') }}</li>
              <li>{{ t('privacy') }}</li>
            </ul>
          </div>
          <div class="footer-col">
            <h4>{{ t('contact_us') }}</h4>
            <ul>
              <li><base-icon name="mail" :size="13"></base-icon> support@pymall.com</li>
              <li><base-icon name="phone" :size="13"></base-icon> 400-888-8888</li>
            </ul>
          </div>
          <div class="footer-col">
            <h4>{{ t('payment_method') }}</h4>
            <ul><li>{{ t('payment_icons') }}</li></ul>
          </div>
          <div class="footer-col footer-news">
            <h4><base-icon name="mail" :size="14"></base-icon> {{ t('subscribe_title') }}</h4>
            <p>{{ t('subscribe_desc') }}</p>
            <div class="footer-news-form">
              <input :placeholder="t('subscribe_ph')" v-model="email" @keyup.enter="sub" />
              <button @click="sub">{{ t('subscribe_btn') }}</button>
            </div>
          </div>
        </div>
        <!--
          备案信息栏（工信部 + 公安部法定要求）：
          1. ICP 备案号必须展示，且必须可点击跳转至工信部备案系统 beian.miit.gov.cn；
          2. 必须同时展示网站主办单位名称（公司主体全称）；
          3. 公安联网备案号必须与官方盾牌图标一起展示，
             且链接到公安部「全国互联网安全管理服务平台」查询页（带 code 参数）。
          该行为法定强制展示内容，不参与中英文切换，两种语言下保持中文原文。
        -->
        <div class="copyright">
          <span class="copyright-org">© 2026 ${COMPANY_NAME} 版权所有</span>
          <a class="beian-link" href="${ICP_BEIAN_URL}" target="_blank" rel="noopener noreferrer"
             title="工业和信息化部政务服务平台"><base-icon name="shield" :size="12"></base-icon>${ICP_BEIAN_NO}</a>
          <a class="gongan-link" href="${GONGAN_BEIAN_URL}" target="_blank" rel="noreferrer noopener"
             title="全国互联网安全管理服务平台"><img class="gongan-icon" src="${GONGAN_BEIAN_ICON}"
             alt="公安备案图标" width="20" height="20" loading="lazy">${GONGAN_BEIAN_NO}</a>
        </div>
      </footer>`,
    setup() {
      const state = Vue.reactive({ email: '' });
      function sub() {
        if (!state.email) { toast(t('subscribe_ph'), 'error'); return; }
        var emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRe.test(state.email)) { toast(t('invalid_email'), 'error'); return; }
        api('/api/subscribe', { method: 'POST', noLang: true, body: { email: state.email } })
          .then(function (d) {
            toast(d.message || t('subscribe_ok'), 'success');
            state.email = '';
          })
          .catch(function (e) { toast(e.message, 'error'); });
      }
      // 用可写 computed 暴露 email：v-model 需要能写回，
      // 只给 getter 的 computed 是只读的，输入值永远进不到 state.email，
      // 点「订阅」必然报「请输入您的邮箱」（历史 bug）。
      const emailModel = Vue.computed({
        get: function () { return state.email; },
        set: function (v) { state.email = v; },
      });
      return { t, email: emailModel, sub };
    },
  };

  // ---------- 导出 ----------
  global.PyMall = {
    Vue: Vue,
    store: store,
    TRANSLATIONS: TRANSLATIONS,
    getLang: getLang,
    setLang: setLang,
    switchLang: switchLang,
    onAuthChange: onAuthChange,
    t: t,
    tt: tt,
    localName: localName,
    api: api,
    money: money,
    // 备案主体信息（页脚 + 页面标题共用）
    COMPANY_NAME: COMPANY_NAME,
    BRAND_NAME: BRAND_NAME,
    SITE_TITLE: SITE_TITLE,
    ICP_BEIAN_NO: ICP_BEIAN_NO,
    ICP_BEIAN_URL: ICP_BEIAN_URL,
    GONGAN_BEIAN_NO: GONGAN_BEIAN_NO,
    GONGAN_BEIAN_URL: GONGAN_BEIAN_URL,
    GONGAN_BEIAN_ICON: GONGAN_BEIAN_ICON,
    siteTitle: siteTitle,
    getToken: getToken,
    setToken: setToken,
    clearToken: clearToken,
    getAdminToken: getAdminToken,
    login: login,
    register: register,
    logout: logout,
    refreshCart: refreshCart,
    refreshWishlist: refreshWishlist,
    buyNow: buyNow,
    toast: toast,
    openAuth: openAuth,
    icons: ICONS,
    components: {
      BaseIcon: BaseIcon,
      TopBar: TopBar,
      MainNav: MainNav,
      AuthModal: AuthModal,
      SiteFooter: SiteFooter,
    },
  };

  // 页面入口统一挂载
  global.mountPyMall = function (options) {
    var app = Vue.createApp(options);
    app.component('base-icon', BaseIcon);
    app.component('top-bar', TopBar);
    app.component('main-nav', MainNav);
    app.component('auth-modal', AuthModal);
    app.component('site-footer', SiteFooter);
    // 全局注入
    app.config.globalProperties.$t = t;
    app.config.globalProperties.$tt = tt;
    app.config.globalProperties.$money = money;
    app.mount('#app');
  };

})(window);