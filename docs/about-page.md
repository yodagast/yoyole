# 关于我们（品牌故事页）设计说明

前台 `static/about.html` 的设计规范、内容来源与维护方式。

## 1. 设计定位：编辑杂志风

参考独立设计师品牌故事页的排版语言（editorial / magazine），与站内商品页
（PDD 式高密度卡片）刻意区分开 —— 品牌页负责传达气质，商品页负责转化。

四条设计原则：

| 原则 | 实现 |
|---|---|
| **衬线标题 + 无衬线正文** | 标题统一走 `--ab-serif` 字体栈；正文沿用 `--font` |
| **深浅交替的区块节奏** | Hero(深) → 故事(奶油) → 价值观(深) → 历程(奶油) → CTA(深) |
| **满幅与锐角** | Hero 通栏满幅；卡片/按钮 `border-radius: 0`（商品页仍用 16px 圆角）|
| **品牌玫红只做强调** | 编号、引文竖条、年份、轴点用 `--jjs-primary`，大面积底色用奶油/近黑 |
| **底色不叠加** | 奶油区块里不再套白色面板；只有时间线卡片用白底（与模板一致）|

### 设计令牌

全部定义在 `.about-page` 作用域内，不污染站内其他页面：

```css
--ab-serif: "Songti SC", "STSong", "Noto Serif SC",
            "Source Han Serif SC", "SimSun", "Songti TC", Georgia, serif;
--ab-cream: #f7f4f1;        /* 奶油底 */
--ab-cream-deep: #efe9e3;   /* 奶油深（图片占位） */
--ab-ink: #1c1c1c;          /* 近黑（深色区块） */
--ab-line: #ddd6cf;         /* 浅色分割线 */
--ab-line-dark: rgba(255,255,255,.14);  /* 深色区块分割线 */
--ab-accent: var(--jjs-primary);        /* 品牌玫红 */
--ab-accent-deep: #8e1240;              /* 深玫红（年份） */
--ab-text-soft: #5c5751;                /* 次级正文 */
```

> 字体走**系统衬线栈**（macOS `Songti SC`、Windows `SimSun`），不引外部字体文件，
> 零额外请求；`serif` 兜底保证任何环境都不会退化成无衬线。

---

## 2. 页面结构

```
┌ Hero ───────────── 满幅品牌图 + 深色遮罩 + 居中衬线标题 + 滚动提示
├ 我们的故事 ─────── 图文双栏（左文右图，文字直接落在页面底色上）+ 玫红竖条引文
├ 我们的价值观 ───── 深色区块，居中标题 + 三栏编辑风（01 / 02 / 03）
├ 成长历程 ───────── 奶油底，中轴交替时间线（黑底卡片 + 亮玫红衬线年份）
└ 收尾 CTA ───────── 深色区块，居中衬线标题 + 奶油色锐角按钮
```

### Hero

- `min-height: clamp(440px, 62vh, 680px)`，满幅宽、`background-size: cover`
- 双层遮罩（纵向渐变 + 径向暗角），保证白色标题在任意图片上可读
- 入场 12s 轻微缩放动画；底部一条呼吸动效细线提示可滚动
- 标签用**方角描边框**（不是圆角胶囊），呼应编辑风

### 我们的故事

- 左文右图，**文字直接落在页面底色上，不做白色卡片**（与模板一致：通篇一个底色）
- 引文 `blockquote` 用**左侧玫红竖条** + 衬线字（对齐模板的引用处理）
- 窄屏（≤900px）改为单列，**图片上移到文字之前**（`order: -1`），先图后文更好读

> ⚠️ **卡片的内边距要和背景一起去掉**：
> 原先 `.ab-story-copy` 有 `clamp(30px,4vw,60px) clamp(26px,3.6vw,54px)` 的内边距，
> 靠卡片边框把它“包”住。一旦去掉白底，这 54px 横向内边距会让故事文字
> 比其它区块内缩一截，**与整页左基线对不齐**（实测需与容器内容盒齐平）。
> 所以改成只保留少量纵向微调 `clamp(4px,1.2vw,12px) 0`，图文间距改由 `gap` 承担。

### 我们的价值观

- 深色 `--ab-ink` 区块，居中衬线标题 + 小字副标题
- 三栏，用 1px 半透明细线分栏（`border-top` + 每列 `border-right/bottom`），
  末列去掉右边框
- 每列：**衬线大号编号**（`01/02/03`，由 `String(index+1).padStart(2,'0')` 生成）
  + 衬线小标题 + 次级灰色描述
- 悬停时整列轻微提亮（`rgba(255,255,255,.04)`）

### 成长历程（中轴交替时间线）

本页最具识别度的排版，要点：

```css
.ab-timeline::before { left: 50%; width: 1px; }   /* 中轴细线 */
.ab-tl-item          { width: 50%; text-align: right; }        /* 奇数项在左 */
.ab-tl-item:nth-child(even) { margin-left: 50%; text-align: left; }  /* 偶数项在右 */
.ab-tl-item:nth-child(odd)  .ab-tl-dot { right: -4.5px; }
.ab-tl-item:nth-child(even) .ab-tl-dot { left:  -4.5px; }
.ab-tl-dot { box-shadow: 0 0 0 4px var(--ab-cream); }  /* 用底色描边"挖断"轴线 */
```

- 每项**文本对齐朝向中轴**（左列右对齐、右列左对齐），视觉重心落在中线上
- 轴点用 `box-shadow` 的底色描边盖住轴线，避免出现"线与点叠在一起"的脏边
- 卡片为**黑底**（`var(--ab-ink)`，与价值观/收尾 CTA 同一深色令牌）+ 锐角 + 半透明细边框，
  悬停上浮 4px
- **≤768px 退化为左轴单列**：轴线移到左侧 4px，所有项改为左对齐
  （窄屏左右交错会显著降低可读性）

#### ⚠️ 卡片改黑底时，文字必须整体翻转

只改 `background` 不改文字是最容易踩的坑 —— 卡片内三行文字原本都是为白底准备的深色，
留在黑底上会**整片看不见**：

| 元素 | 白底时 | 黑底时 | 对比度 |
|---|---|---|---|
| `.ab-tl-year` | `--ab-accent-deep` (#8e1240) | `#ff9dbd`（亮玫红） | 1.6 : 1 → **8.1 : 1** |
| `.ab-tl-title` | 继承 `--ab-text` (#1c1c1c) | `#fff` | — → 15.7 : 1 |
| `.ab-tl-desc` | `--ab-text-soft` (#5c5751) | `rgba(255,255,255,.62)` | 与价值观区块的描述文字一致 |

亮玫红取自本页深色区块已在用的 `.ab-section--ink .ab-kicker`，保持强调色体系统一。

### 收尾 CTA

深色区块 + 居中衬线标题 + 奶油色锐角按钮（`border-radius: 0`），与 Hero 首尾呼应。

**与页脚无缝相接**：本页最后一个深色区块与页脚（`--jjs-dark: #232323`）连成一块，
不出现缝隙与色差。为此做了两件事：

```css
--ab-ink: var(--jjs-dark);          /* ① 直接复用页脚色，不硬编码另一种深色 */
.about-page .footer { margin-top: 0; }   /* ② 屏蔽页脚 40px 外边距 */
.about-page .ab-cta { padding-bottom: 0; }  /* ③ 底部留白交给页脚内边距 */
```

> ⚠️ **两个坑**（都踩过）：
> 1. `.footer` 默认有 `margin-top: 40px`。它会把父元素的背景（奶油色）露出来，
>    在两片深色之间形成一条**浅色空白带**，看起来像页面被切断。
>    站内其他页面（最后一个区块是浅色）需要这个间距，所以只能在 `.about-page` 里屏蔽。
> 2. ③ 必须写成 `.about-page .ab-cta` 而非 `.ab-cta`。断点里用的是
>    `.ab-section { padding: 66px 0 }` 简写，与 `.ab-cta` 同为 `(0,1,0)` 优先级且源码在后，
>    会直接覆盖掉单条 `padding-bottom`（窄屏又变回大片留白）。

---

## 3. 内容来源（后台「品牌管理」可编辑）

页面文案**全部由 `/api/site-content?page=about` 驱动**，重构未改变任何字段名，
后台 `admin.html?view=brand` 现有关卡照常生效：

| 字段 | 用途 |
|---|---|
| `hero_tag` / `hero_title` / `hero_subtitle` | Hero 标签 / 主标题 / 副标题 |
| `story_title` / `story_content` | 我们的故事标题与正文（**按 `\n` 拆成多段**）|
| `story.image` | 品牌图 —— 同时作为 Hero 满幅底图（可用 `story.hero_image` 单独覆盖）|
| `values_kicker` / `values_title` / `values[]` | 价值观区块标题与三栏内容 |
| `milestones_kicker` / `milestones_title` / `milestones[]` | 成长历程区块标题与时间线节点 |
| `cta_title` / `cta_desc` | 收尾 CTA 文案 |

### 默认占位图

接口异常或后台未上传图片时，用两处**不同 seed** 的占位图兜底，避免版式塌陷与两处重复：

| 位置 | 兜底 |
|---|---|
| Hero 满幅底图 | `DEFAULT_HERO_IMG`（`picsum.photos/seed/yoyole-about-hero/1600/900`）|
| 我们的故事配图 | `picsum.photos/seed/yoyole-nature/900/760` |

图片字段兼容两种存储形态：纯字符串 URL，或 `{zh, en}` 多语言对象（`pickImage()` 统一处理）。

### 页面装饰文案

不进入 CMS、属于界面文案的部分放 `pymall.js` 的 `TRANSLATIONS`，随语言切换：

| key | 位置 |
|---|---|
| `about_story_quote` / `about_story_quote_by` | 故事区引文与署名 |
| `about_values_sub` | 价值观区副标题 |
| `about_milestones_sub` | 成长历程区副标题 |

---

## 4. 响应式断点

| 断点 | 变化 |
|---|---|
| ≥1025px | 故事双栏、价值观三栏、时间线中轴交替（完整版式）|
| ≤1024px | 区块纵向留白收紧（84px → 66px）|
| ≤900px | 故事改单列且图片置顶；价值观改单栏，细线改为上下分隔 |
| ≤768px | 时间线改左轴单列；Hero 内边距收紧、隐藏滚动提示；区块留白 50px |

实测 390 / 768 / 1024 / 1440 / 1920 / 2560 均无横向滚动，时间线轴点始终精确落在中线上；
页尾接缝在全部断点下恒为 0、深色块与页脚恒为同色。

---

## 5. 测试

`test/test_pages.py` 中 5 个用例锁定本页设计，防止后续改动把版式改回去：

| 用例 | 断言 |
|---|---|
| `test_about_uses_editorial_serif_and_palette` | 衬线字体栈 + 4 个设计令牌齐全 |
| `test_about_section_rhythm_alternates` | 深浅交替节奏（深色区块 ≥ 2）|
| `test_about_timeline_alternates_around_center_line` | 中轴线 + 半宽项 + 奇偶反向 + 轴点圆点 + 窄屏退化 |
| `test_about_has_no_legacy_classnames` | 旧类名无残留 |
| `test_about_timeline_cards_are_dark_with_light_text` | 时间线卡片黑底 + 文字翻转浅色 |
| `test_about_story_sits_on_page_background` | 故事区块无白底无边框、内边距不内缩 |
| `test_about_dark_blocks_merge_with_footer` | 深色块与页脚同色、无 40px 缝隙带 |
| `test_about_keeps_cms_bindings` | 9 个 CMS 字段绑定与两处兜底图 |

`PAGE_ASSERTS["about.html"]` 同时覆盖 `about-page` / `ab-hero` / `ab-story-grid` /
`ab-values-grid` / `ab-timeline` / `ab-cta-btn` 等结构类名。

---

## 6. 已知内容问题（数据侧，非代码）

线上数据库里英文版 `story_content` 与 `values` 存在质量问题，重构**未改动**这些内容：

- `story_content.en` 是一整段、无换行 → 英文版故事只渲染 1 段（中文版正常 3 段）。
  在后台「品牌管理 → 品牌故事 → 正文（英文）」里用换行分段即可。
- `values[*].title.en` 形如 `Live & Create .`（多余空格与句点）、
  且与中文标题（平常之心 / 自然之态 / 非凡人生）语义不匹配，建议一并校对。
