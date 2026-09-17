# md2epub — MinerU 输出 → EPUB3 转换器

将 MinerU（hybrid 后端）输出的 markdown 转换为高质量、高兼容性的 EPUB3 电子书。
公式支持 **MathML / SVG / PNG** 三种可切换的渲染方案（兼容性金字塔），
覆盖广泛的阅读器软硬件。

## 用法

```bash
python3 convert.py <hybrid_output_dir> [--math mathml|svg|png|both]   # 默认 both = mathml+svg
python3 validate.py <book.epub>                                       # 12 项结构校验
```

构建产物（缓存、预处理 md、失败清单、epub）一律生成在**书籍项目目录**——即
`<hybrid_output_dir>` 的上一级——的 `md2epub-build/` 子目录，不在本仓库（缓存和
位图是书籍专属的大文件）。诊断脚本（`build_imgtest*.py`、
`flatten_svg.py --selftest`）从同一位置读取缓存：默认当前书目录，或
`MD2EPUB_PROJECT` 环境变量 / 首个 CLI 参数覆盖。

### 按书配置

`--config` 指定书籍专属启发式与断言（默认 `book_config.json` = 数学分析第三版上册；
`book_config_xinjiang_v1.json` = 数学分析新讲第一册）。可覆盖项：章节识别
（`chapter_regex`）、正文起点注入 H1（`body_start_h1`，用于篇标题页只存在于扫描图
而 MinerU 未输出标题的情况）、TOC 级 §/附录保留前缀（`keep_h2_prefixes` 列表）、
垃圾标题模式、期望计数（H1/§/公式数，全部精确断言）、`book_name`（epub 文件名，
缺省取 md 文件名）、png 尺寸兜底系数 `png_px_per_ex`（默认 8.0，设备校准用）、
以及内嵌 `metadata`（书名/作者/uuid，转储到构建目录供 pandoc）。
预处理基线在删除前言/纸质目录**之后**统计（被删部分可能含公式，如"符号说明"页）。

依赖：pandoc ≥ 3.x、node（svg/png 模式，需先在本目录 `npm install`）、
rsvg-convert（png 模式，`brew install librsvg`）、PyYAML + PIL、pdftoppm、xmllint。

## 三种公式模式（兼容性金字塔）

**所有公式一律独立文件引用**：svg/png 两种模式的每个公式都是
`<img src="../media/eq-{hash}.svg|.png"/>`，XHTML 中零内联 `<svg>`、零
`data:` URI。原因：格式转换器（Kindle azw3 via calibre 等）**丢弃内联
`<svg>` 元素但原样传递被引用的媒体文件**——外链引用是跨格式存活的关键。

| | mathml | svg（外链） | png（外链位图） |
|---|---|---|---|
| 体积 | 0.8 MB | 10.2 MB | 6.6 MB |
| 渲染 | 阅读器原生 MathML | `<img>` 引用 `EPUB/media/*.svg` | `<img>` 引用 2× 32 色调色板 PNG |
| 适用 | Apple Books、Thorium、Calibre 等支持 MathML 的阅读器 | 支持外链 SVG 的现代阅读器（Calibre/KOReader） | **不支持 SVG 的引擎（掌阅 iReader 等国产电纸书；Kindle 转换后）——位图是 EPUB 强制支持项，必然能显示** |
| 缺点 | 绝大多数电纸书不支持（会显示成 LaTeX 原文） | iReader 完全不渲染 SVG（连纯 path 都不画）；Kindle 转换器对 .svg 支持不稳 | 放大有像素；完整 CSS 引擎上尺寸仍随字号缩放 |

三版内容完全相同（同一预处理、同一目录：11 个顶层条目 / 43 个文件）。
在目标设备上各试一章后选用；iReader 这类设备直接用 png 版。

实测（2026-09）：iReader 电纸书自带阅读器——mathml 版公式显示为 LaTeX 原文
（引擎无 MathML 支持，降级读出 annotation）；svg 版公式空白；**测试 3 矩阵
（8 行逐变量隔离）证明连手写的最简单纯 path svg 都不渲染——引擎完全没有
SVG 光栅化能力**（文件/内嵌/ex 单位/xlink/currentColor 皆无关），"压平 SVG"
方案（flatten_svg.py，已实现并像素级验证）因此搁置；Calibre/KOReader 上
三版均基本正常（少量公式异常待后续修复）。png 版第一版公式过大——根因是
样式表里的裸 `img{height:auto}` 规则：iReader 不支持 ex 单位，丢弃公式 img
的 style 后级联落到 height:auto，回到位图固有 3× 尺寸。修复：png 版专用
类作用域样式表 `epub-png.css`（无任何裸 img 规则），公式 img 保留原生
width/height 属性兜底，插图挂 `figure-img` 类并写入原生尺寸。

png 位图编码（`RASTER_SCALE=2, RASTER_COLORS=32`）：2× 超采样在电纸书 ppi
下清晰（设备实测），32 色调色板 PNG（含 tRNS 透明，PNG 1.0 基础规范，
Qt/MuPDF 等引擎均支持）比灰度+alpha 小约 4×——45 MB → 14.8 MB，画质经
放大对比无可见损失（e-ink 面板物理上只有 16 级灰度）。另修复：96 个
`width="100%"` 拉伸表格公式（分段函数等）此前误用嵌套 viewBox（宽度=1）
推算宽高比，被栅格成 8px 畸图——现改为 rsvg 实测墨迹宽高比（探测宽度
无关，稳定 ±1%）。

### png 模式：跨设备尺寸兜底（px 先行 / ex 兜底双单位级联）

设备事实：掌阅 iReader 等弱 CSS 引擎**忽略 ex 单位**（整条声明丢弃 → 公式
尺寸落回原生 width/height 属性、行内 vertical-align 完全丢失）；Kindle azw3
同理只认 px。而 Calibre/KOReader 等完整引擎上 ex 随字号缩放是既有优点，
不能破坏。方案是每条尺寸声明写两遍，**px 在前、ex 在后**：

```html
<img class="math-inline" … height="16"
     style="height:16px;height:2.034ex;vertical-align:-4px;vertical-align:-0.464ex"/>
<img class="math-display-img" src="…eq-….png" width="360" height="18"
     style="width:360px;width:45ex;height:18px;height:2.262ex"/>
```

（display 的宽度是**画布盒**：`max(画布ex, 内容宽ex)` × 内容高 ex——见下节。）

级联规则天然分流：完整引擎 ex 覆盖 px（字号缩放保留，好平台零回归）；
ex 盲引擎丢弃 ex 声明、px 生效——与原生属性同源同值，任何兜底路径渲染
同一尺寸，行内对齐也首次在掌阅生效。行间公式宽高都写双单位（同因子
缩放，纵横比恒定；不用 `height:auto`——它在 ex 盲引擎上是合法关键字，
会回到位图固有 2× 尺寸）。px 值 = ex × `png_px_per_ex`（默认 8，即 16px
正文；按书/按设备可在 config 覆盖，校准方法见下）。

**行间公式 = 画布定标（canvas scheme）**（2026-09，掌阅本地 + 掌阅 KOReader
双端实测，测试5/6/7 + 全书实证）：

- 掌阅本地引擎把**高于 ~44 CSS px 的行内级公式图片一律钳到一行文字高**，
  与标记写法完全无关（钉宽高 / 只写宽 / 各类包裹 / 抬行框——全部被钳）
- 但**纯图片段落**（裸 `<p><img/>`，段内无任何文本节点——连 ZWSP 都不行）
  按**位图纵横比拉到约全屏宽**渲染：绝对 px 属性/样式完全不参与
  （测试7：5 种容器 × 属性宽 138–408px × 3 档缩放，渲染结果全部相同偏大）
- KOReader 与完整引擎按 ex 样式缩放，一切正常

既然纯图片段落的**唯一尺寸杠杆是位图自身几何**，就把 display 位图居中放到
统一透明画布上（`DISPLAY_CANVAS_PX=720` 位图 px）：

- **掌阅**：位图被拉到屏宽 → 字符高 ≈ 屏宽 × 内容高 / 720 位图 px，全部
  display 公式统一校准为正文 1–1.3 倍（此前窄公式被拉 4–5 倍）
- **KOReader/完整引擎**：ex 声明保持真值，画布只是无形衬垫；img 盒宽取
  `max(画布ex, 内容宽ex)`（720/16=45ex）保证纵横比贴合衬垫后位图——超大
  字号时按列宽收缩 ~0.75×，设备实测回报"偏小但可接受"
- **校准旋钮** `png_display_canvas_px`（按书可配）：字符偏大→调大画布，
  偏小→调小。重衬垫幂等：宽于内容的位图即旧画布衬垫（衬垫位图恒为整画布
  宽、内容居中），先居中裁回内容再按新画布重衬，双向可逆
- 宽于画布的内容不衬垫（拉宽本就是其自然尺寸）；行内位图永不衬垫

（曾尝试把高公式按低墨迹行切成 ≤44px 逐条小图绕过钳制——**已放弃**：
约 1/4 的切割落在构造内部接缝（分数间隙、上下标、求和限），字符被切开，
两台阅读器上均出现字符错位与白色横纹；切条方案及 `eq-*-p*.png` 缓存已
由 `render_pngs` 一次性清理。）

**高行内公式自动改排独立行**（`PROMOTE_INLINE_EX=4.8`）：行内位图无论如何
标记都逃不出掌阅的 ~44px 行钳制，高于 4.8ex 的行内公式（本书 107 处——
恰是被压得看不清的那批）在 markdown 层改排为独立居中行，走画布定标的
display 路径（标准数学排版对高公式的处理方式）。保护：出现在标题/表格/
引用/列表行的公式不改排（拆行会破坏该块，且其行内出现会引用到衬垫位图
——整键保持行内，本书实测仅 1 处因此保留行内）；改排时紧跟公式的标点
（`。，；` 等）随行迁移，置于公式段后的居中 `p.math-tail`（段内任何文本
节点都会使段落退出纯图片排版，不能同段；本书 0 处带标点）。

**超宽单行公式自动改排**（`WIDE_SPLIT_EX=46`）：57ex 的长推导链被宽度框
等比缩到 ~83%，公式内字符过小看不清。超过 46ex 的行间公式在**深度 0 的
关系符**（`= ≤ ⇒ …`，花括号/`\left(` 组内的不算）处自动断行改排为
`\begin{aligned}` 多行（续行以关系符开头、`&` 对齐，标准数学续排样式），
新 LaTeX 重新渲染——每行 ≤44ex，字符接近正文字号；好引擎同样受益
（窄屏上长公式不再溢出）。本书 52 处改排，全部编译成功。

**单符号公式直接输出 HTML 文本**（`_text_symbol_html`）：设备实测弱引擎对
行内图片有**最小高度拉升**（约一行文字高），`x`、`y`、`α` 这类单符号位图
（10–13px）被拉到 ~28px——过大过粗。单字母/希腊字母（可带撇号和单层
上下标）不再走位图，直接输出 `<span class="msym">α</span><sub>n</sub>`
（CSS 斜体衬线，被丢弃时退化为正文文本，尺寸恒正确）。本书 1252 处
转文本。

**行内公式只钉 height 属性**（px 封顶 `FALLBACK_BOX_PX` 的 44px，即掌阅
钳制线）：不写 width——KOReader 等引擎把钉了宽高的行内图塞进剩余行宽时
**压宽保高**（纵横比破坏），只钉高时任何引擎都按位图比例推出宽，纵横比
恒不破。

**复杂行内公式 px 放大**（`INLINE_BOOST=1.3`，`h≥3ex` 触发）：分数、
带限求和等行内公式的 px 兜底高度放大 1.3 倍（封顶 44px），内部字符在
px-only 引擎上可读；ex 真值不动，完整引擎零变化。本书 1143 处放大。

OPF 同时带 EPUB2 遗产标记（`<meta name="cover">` + `<guide>` cover/toc
reference）——老引擎与 calibre 的封面嗅探走这条路；epubcheck 会警告
guide 已弃用，无害。

**校准（公式显示测试4.epub，`build_imgtest4.py`）**：A 现状基线 / B 双单位
级联 / C 纯 px（隔离"引擎认不认 CSS px"）/ D B+`align="absmiddle"` 属性 /
E7–E10 系数 k / F 宽公式溢出。设备回报（2026-09，掌阅）：**A–E 全部正常**
→ 双单位级联正式采用，k 保持默认 8，`align` 属性不需要（保持最简标准）；
**F1/F2 均被压扁**（后经测试5/6 定性为同一高度钳制，width-only 策略已废弃，
见上节）。后续 `build_imgtest5.py`/`build_imgtest6.py`（残留缺陷定位）确认了
钳制的纯高度相关性、KOReader 行内压宽机制与上节全部结论；
`build_imgtest7.py`（display 尺寸按规格定档）证明纯图片段落在掌阅按位图
纵横比拉至约全屏宽、绝对 px 属性不参与——由此定型画布定标方案，其 G 行
（高行内公式 1.3× 展示预览）为改排方案的直接依据。

### png 模式：按公式复杂度智能选择渲染样式

MathJax 的 display 样式（displaystyle）会把分数、求和号等放大成展示尺寸，
inline 样式（textstyle）则压缩它们；MinerU 的 `$`/`$$` 分类有噪声。
png 模式在渲染前按**实测几何尺寸**翻转离群值（`_decide_flips`）：

- 行间公式但"矮且窄"（高 ≤2.5ex 且宽 ≤25ex，如 `\tan x = x`、`F = ma`）
  → 用 inline 样式渲染，独占一行时不再臃肿（本书 251 处）
- 行内公式但"高"（高 ≥3ex，如嵌套分数、带上下限的积分、被误塞进行内的
  分段函数 `\begin{array}`）→ 用 display 样式渲染，字形放大清晰可读
  （本书 649 处；LaTeX 源显式含 `\textstyle` 的不翻转）

翻转结果持久化在 `math_cache.json`（条目带 `rendered_display` 标记），
判定幂等，重跑只重渲染增量。

## 流水线（convert.py）

1. **预处理**：NUL 字节修复 → 行内公式定界符规范化 → 旧式 LaTeX 命令修复
   （`\bf`→`\mathbf` 等 38 处）→ 切除前言/纸质目录 → 删页眉标记 →
   标题重分级（11 个 H1 / 32 个 §）→ 垃圾 sub/sup 清理 →
   断言（公式计数 2,172+6,930 不变、标题骨架精确匹配）
2. **公式层**：
   - mathml：pandoc `--mathml`（texmath 引擎）
   - svg/png：token 占位 → MathJax v3 渲染（缓存于书籍目录
     `md2epub-build/math_cache.json`）→ pandoc 之后再注入 SVG/PNG
     （SVG 不经过 markdown 解析器，规避嵌套 `<svg>` 时 pandoc html-block
     状态机的问题）
3. **装配**：mathml 由 pandoc 直接产出；svg/png 按章/节分块装配
   （单文件 55 MB 会导致 pandoc OOM，分块后每块秒级、内存安全），
   手工打包 zip（mimetype 首条 STORED，其余 DEFLATED）
4. **失败清单**：`md2epub-build/failures.log` 列出**所有**转换失败/异常——
   每条含章节定位、LaTeX 原文、上下文摘录

## 构建产物

```
<书目录>/<书名>.mathml.epub   0.8 MB   6,212 个 MathML 公式（新讲第一册）
<书目录>/<书名>.svg.epub     10.2 MB   6,212 处 <img> 引用 3,761 个外链 .svg
<书目录>/<书名>.png.epub      6.6 MB   6,212 处 <img> 引用 + 文本符号（--math png）
<书目录>/md2epub-build/failures.log   失败清单（mathml/svg 回退，均为 OCR 乱码源）
<书目录>/md2epub-build/math_cache.json  公式渲染缓存（跨运行复用，重跑秒级）
<书目录>/md2epub-build/png_cache/       公式位图缓存（跨运行复用；display 位图
                                       已衬垫至画布宽，改画布宽时自动重衬垫）
```

## 已知限制

- 54 处 OCR 乱码公式在 mathml 版按原始 TeX 文本显示（`<span class="math">`），
  svg 版显示为等宽代码（`<code class="math-raw">`）——两版均保留 LaTeX 原文便于日后修复
- MinerU `images/` 中 2,172 张公式截图未使用（MathML/SVG 已覆盖其用途）；
  119 张插图全部嵌入
- 索引部分仍为两大段 run-on 段落（OCR 原样保留）
- 夜间模式：CSS 零颜色声明，SVG 用 `fill="currentColor"`，颜色完全跟随阅读器主题

## 人工抽查建议

Apple Books / 目标设备各打开一版：封面、目录跳转（第一章 → § 内任一节）、
行间+行内公式各看一处、夜间模式下 SVG 颜色是否跟随主题。
