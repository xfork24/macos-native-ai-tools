# md2epub — MinerU 输出 → EPUB3 转换器

将 MinerU（hybrid 后端）输出的 markdown 转换为高质量、高兼容性的 EPUB3 电子书。
公式支持 **MathML / SVG / PNG** 三种可切换的渲染方案（兼容性金字塔），
覆盖广泛的阅读器软硬件。

## 使用 `md2epub.py`

`md2epub.py` 是推荐的统一入口。它读取 MinerU 输出，自动生成书籍配置和章节
结构，然后生成 MathML、外链 SVG、PNG 三版 EPUB，并在交付成品前完成内容
覆盖和 EPUB 结构验收。整个流程不调用 LLM，也不访问网络。

### 1. 准备运行环境

以下命令均在本目录 `md2epub/` 中执行：

```bash
source ../.venv/bin/activate
uv pip install -r ../requirements.txt
npm install
```

外部命令依赖可通过 Homebrew 安装：

```bash
brew install pandoc librsvg poppler
```

完整转换需要：

- `pandoc`：生成 EPUB 和解析 Markdown；
- `node` 与本目录的 `mathjax-full`：渲染 SVG 公式；
- `rsvg-convert`（由 `librsvg` 提供）：将公式转为 PNG；
- `pdftoppm`（由 `poppler` 提供）：从原 PDF 生成封面；
- Python 包 `PyYAML` 和 `Pillow`。

运行前可检查入口和参数：

```bash
python md2epub.py --help
```

### 2. 检查 MinerU 输入目录

命令的位置参数必须是 MinerU 的 `hybrid_auto` 目录，而不是它的上一级书籍目录。
典型目录如下：

```text
某本书/
└── hybrid_auto/
    ├── 某本书.md
    ├── 某本书_content_list.json
    ├── 某本书_content_list_v2.json
    ├── 某本书_middle.json
    ├── 某本书_origin.pdf
    └── images/
```

规则和降级行为：

- 目录内必须能确定唯一的 Markdown 文件；存在多个候选时不会猜测，需要通过
  `--config` 的 `md_glob` 明确指定；
- 优先读取与 Markdown 同名前缀的 `_content_list.json`，其次读取
  `_content_list_v2.json`；缺少两者时仍可转换，但标题恢复能力会降低并产生警告；
- `_origin.pdf` 用于生成封面；缺少时 EPUB 仍可生成，但报告会注明没有封面；
- Markdown 中引用的本地图片必须存在；远程图片不会下载，缺图会使构建失败。

### 3. 首次完整转换

路径含空格或中文时必须加引号：

```bash
BOOK_DIR="/Users/you/Books/某本书/hybrid_auto"
python md2epub.py "$BOOK_DIR"
```

默认 `--math all`，依次生成三版：

```text
某本书.mathml.epub
某本书.svg.epub
某本书.png.epub
```

默认输出到 `hybrid_auto` 的上一级，即书籍目录。每一版先在临时目录中生成，
只有通过自动验收后才替换同名成品；某一版失败时，之前通过验收的旧 EPUB 会
保留。

### 4. 常用命令

只分析输入并生成配置，不构建 EPUB：

```bash
python md2epub.py "$BOOK_DIR" --analyze-only
```

只生成或重跑一个公式版本：

```bash
python md2epub.py "$BOOK_DIR" --math mathml
python md2epub.py "$BOOK_DIR" --math svg
python md2epub.py "$BOOK_DIR" --math png
```

将 EPUB、缓存和报告全部输出到另一个目录：

```bash
python md2epub.py "$BOOK_DIR" --out-dir "/Users/you/Books/转换结果"
```

应用书籍专属覆盖配置：

```bash
python md2epub.py "$BOOK_DIR" --config custom.json
```

参数说明：

| 参数 | 默认值 | 作用 |
|---|---|---|
| `hybrid_dir` | 必填 | MinerU 的 `hybrid_auto` 目录 |
| `--math all` | `all` | 依次生成 MathML、SVG、PNG 三版 |
| `--math mathml` |  | 仅生成阅读器原生 MathML 版 |
| `--math svg` |  | 仅生成外链 SVG 公式版 |
| `--math png` |  | 仅生成兼容性最高的公式位图版 |
| `--config FILE` | 无 | 用 JSON 覆盖自动识别结果 |
| `--out-dir DIR` | `hybrid_dir` 上一级 | 指定 EPUB、缓存和报告的共同输出根目录 |
| `--analyze-only` | 关闭 | 只分析、标准化并生成配置和报告 |

### 5. 覆盖自动配置

覆盖顺序固定为：`book_config.template.json` → 自动识别结果 → `--config`
指定的 JSON。通常只写需要修改的字段：

```json
{
  "book_name": "用于文件名的书名",
  "md_glob": "唯一的源文件.md",
  "metadata": {
    "title": "电子书标题",
    "author": ["作者"],
    "translator": ["译者一", "译者二"],
    "reviewer": ["主审"]
  },
  "text_replacements": [
    ["确定存在的 OCR 原文", "修正后的文字"]
  ],
  "png_px_per_ex": 8.0,
  "png_display_canvas_px": 720
}
```

注意：

- `metadata.identifier` 默认由源 Markdown 内容生成稳定 UUID，重跑不会改变；
- `text_replacements` 是精确字面替换，只应处理已经核对过的 OCR 错误；
- `png_display_canvas_px` 增大时，弱 CSS 阅读器中的行间公式会变小；减小时会变大；
- 含 `chapter_regex` 的旧版完整配置会进入兼容流程，用于复现已经校准的数学
  分析三册。新书应优先使用自动分析加少量覆盖字段。

### 6. 自动转换的详细流程

一次完整运行按以下顺序执行：

1. 检查目录、Python 包及当前公式模式需要的外部命令；
2. 获取输出目录锁，避免两个进程同时改写同一本书的缓存和成品；
3. 配对 Markdown、MinerU 内容列表、原始 PDF 和图片；
4. 分析页面与内容块，保留前言等前置内容，删除有明确页面证据的纸质目录，
   合并被拆开的章号与章名，并从目录和页眉证据恢复缺失标题；
5. 生成 `normalized.md` 和 `book_config.generated.json`，同时记录每次结构修改的
   原页码、原行号和理由；
6. 在渲染分支之前建立独立内容基线，统计代码块、插图、表格、章节和公式出现
   次数；围栏代码、缩进代码和行内代码不会参与标题或公式识别；
7. 分别构建 MathML、外链 SVG 和 PNG 版。公式按 LaTeX、实际行内/行间样式及
   渲染版本缓存；PNG 从不可变原始位图派生，重跑不会反复裁剪或降低画质；
8. 检查 ZIP、XML、manifest、spine、目录与锚点、媒体格式、图片哈希、代码块
   哈希、表格和公式覆盖；PNG 版额外检查空列表项、孤立公式及异常尺寸；
9. 验收通过后原子替换成品，最后写入汇总报告。无法渲染的 OCR 公式保留原文
   并作为警告，不会静默丢失。

### 7. 输出文件

默认输出结构：

```text
某本书/
├── hybrid_auto/                         # MinerU 原始输出，不会被修改
├── 某本书.mathml.epub
├── 某本书.svg.epub
├── 某本书.png.epub
└── md2epub-build/
    ├── book_config.generated.json       # 最终生效的完整配置
    ├── analysis.json                    # 自动判断、修改证据和内容基线
    ├── normalized.md                    # 自动整理后的 Markdown
    ├── metadata.yaml                    # EPUB 元数据
    ├── preprocessed.mathml.md
    ├── preprocessed.svg.md
    ├── preprocessed.png.md
    ├── math_cache.json                  # MathJax 公式缓存
    ├── png_cache/                       # 不可变原图及最终公式位图
    ├── failures.mathml.log
    ├── failures.svg.log
    ├── failures.png.log
    ├── validation.json                  # 每一项自动验收结果
    ├── report.json                      # 机器可读总报告
    └── report.md                        # 建议首先阅读的人类可读报告
```

部分中间文件只在对应模式运行后出现。

### 8. 查看结果和退出状态

首先打开 `md2epub-build/report.md`：

- `success`：所有请求的版本构建成功且没有警告；
- `success_with_warnings`：所有请求的版本通过验收，但存在公式原文回退、无封面
  或不可安全拆分的宽公式等非阻塞问题；
- `failed`：至少一个请求的版本未通过构建或结构验收。

进程退出码：成功及带警告成功为 `0`，失败为 `1`，用户中断为 `130`。
进一步排查时按此顺序查看：

1. `report.md`：版本结果和警告摘要；
2. `analysis.json`：删除目录、恢复标题等自动判断的证据；
3. `failures.<模式>.log`：公式原文、章节位置和上下文；
4. `validation.json`：失败的具体结构或内容覆盖检查。

也可独立复验成品：

```bash
python validate.py "/输出目录/某本书.png.epub"
python audit_png.py "/输出目录/某本书.png.epub" \
  --config "/输出目录/md2epub-build/book_config.generated.json"
```

### 9. 重跑、缓存与常见问题

- 直接重复同一命令即可重跑。有效 MathJax 和 PNG 缓存会复用，源 Markdown、
  图片及 MinerU JSON 不会被修改；
- 改变 `png_display_canvas_px` 时无需手动删除缓存，最终 PNG 会从不可变原图重新
  派生；
- 报错“需要唯一 Markdown”时，在覆盖配置中设置精确的 `md_glob`；
- 报错“源图片缺失”时，检查 Markdown 的相对路径和 `hybrid_auto/images/`；
- 报错“已有转换任务正在运行”时，等待同一输出目录中的任务结束；不要同时对
  同一本书启动两个转换进程；
- `success_with_warnings` 不表示内容丢失。无法渲染的公式会保留原始 TeX，具体
  位置在对应的 `failures.<模式>.log`；
- 自动验收负责可检测的结构、内容覆盖及已知 PNG 排版缺陷。交付到特定硬件前，
  仍建议在目标阅读器打开封面、目录、代码页和含复杂公式的一章进行最终目视检查。

## 旧版底层入口

`convert.py` 仍可配合 `book_config_xinjiang_v1.json`、
`book_config_xinjiang_v2.json`、`book_config_xinjiang_v3.json` 使用，以复现已经
校准的数学分析三册。新书应使用 `md2epub.py`，因为底层入口不会自动生成配置、
内容基线和统一验收报告。

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

三版内容完全相同（同一预处理、同一目录）。在目标设备上各试一章后选用；
iReader 这类设备直接用 png 版。

终验（2026-09，掌阅 iReader 本地 + 掌阅 KOReader 双端）：mathml 版掌阅
显示 LaTeX 原文 / KOReader 完美；svg 版掌阅空白 / KOReader 完美；**png 版
双端基本完美**——画布定标与高公式改排一次校准到位，三版表现与金字塔
设计完全一致。

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
下对**按 px 档显示的行内位图**清晰（设备实测），32 色调色板 PNG（含 tRNS
透明，PNG 1.0 基础规范，Qt/MuPDF 等引擎均支持）比灰度+alpha 小约 4×
——45 MB → 14.8 MB，画质经放大对比无可见损失（e-ink 面板物理上只有
16 级灰度）。另修复：96 个 `width="100%"` 拉伸表格公式（分段函数等）
此前误用嵌套 viewBox（宽度=1）推算宽高比，被栅格成 8px 畸图——现改为
rsvg 实测墨迹宽高比（探测宽度无关，稳定 ±1%）。

**行间公式按双倍密度栅格**（`DISPLAY_RASTER_SCALE=4`，第二册设备轮次）：
画布定标把 display 位图拉到约全屏宽（掌阅按纵横比）或 ex 盒宽（完整引擎）
——720px 的画布位图铺到 1404–1872 物理像素的面板上被上采样约 2×，
设备回报"行间公式模糊"。display 公式改按 32px/ex 栅格、画布位图加倍到
1440px：CSS 几何完全不变（画布 ex 值与密度无关，掌阅字形校准、全部标记
尺寸原样），只是像素密度翻倍——227–300ppi 面板上 1:1 或降采样显示。
行内位图维持 2×（按 px 档显示，本就够密）。改密度需删
`md2epub-build/png_cache/` 重栅（衬垫裁剪逻辑按当前密度识别旧画布）。

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
统一透明画布上（校准宽 `DISPLAY_CANVAS_PX=720`——按 2× 密度计的像素值，
物理位图为 1440px，见"双倍密度栅格"节）：

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

**高行内公式自动改排独立行**（`PROMOTE_INLINE_EX=4.8` + `PROMOTE_INLINE_W_EX=36`）：
行内位图无论如何标记都逃不出掌阅的 ~44px 行钳制；高于 4.8ex、或宽于整行
（≥36ex——任何换行引擎都只能把它压宽显示）的行内公式在 markdown 层改排为
独立居中行，走画布定标的 display 路径（标准数学排版对高/长公式的处理方式）。
保护：出现在标题/表格/引用/列表行的公式不改排（拆行会破坏该块，且其行内
出现会引用到衬垫位图——整键保持行内）；改排时紧跟公式的标点（`。，；` 等）
随行迁移，置于公式段后的居中 `p.math-tail`（段内任何文本节点都会使段落
退出纯图片排版，不能同段）。

**编号公式整体改排**（`_reflow_numbered_math_lines`，第二册设备轮次）：
"(1) $…$;"、"(ii) $…$"、"3. $…$" 这类纯编号公式行在纸质排版里是编了号的
行间公式，留在 markdown 里会被 pandoc 解析成 `<ol>` 列表——项是否纯图片取
决于尾标点落点，弱引擎上相邻项尺寸互不相干（掌阅把纯图片 li 按位图纵横比
拉满宽、带文本的钉 px 档；高项又被改排成满宽 display 拆散列表、留下空
`<li>`——设备回报"带编号的行内公式忽大忽小"即此）。处理：整行改排为独立
display 行、编号嵌入公式（`\text{(1)}\quad …`，编号统一转 ASCII 半角）——
每条走同一画布定标，原书编号不经 pandoc 重新编号，列表无从拆散。OCR 把
编号和公式拆成两行的（"(C)" 裸行 + 空行 + 孤立公式行——裸编号行会被
pandoc 解析成空列表项）先合并再改排。列表/编号行上的行内公式不参与智能
样式翻转（`_LIST_LINE_RE` 覆盖 `(N)`/`（N）`/`a.`/`A)`/罗马数字标记），
历史构建翻转过的在重跑时回退为行内样式。

**孤立行内公式并回原段**（`_merge_orphan_inline_lines`）：OCR 把句子尾巴
（如 (v) 项末尾的 `$B = 0.$`）单独拆成一段时，该段只含一个行内公式图
片——掌阅按"纯图片段落"规则将其按纵横比拉到约全屏宽，2ex 的小公式被
撑大数倍（设备回报"B = 0 显示太大"）。纸书里它是上一行的句尾：整行只有
行内公式 token 的段落并回上一文本行（标题/表格/引用行不并）。

**超宽公式行级改排**（`WIDE_SPLIT_EX=46`，第二册设备轮次加固）：画布定标下
宽于 45ex 画布的 display 公式不衬垫、CSS 盒按纵横比缩到 45ex——60ex 的公式
字形只有正文 0.75×、78ex 只剩 0.58×（设备红框回报"某公式显示异常"）。现按
**行结构**改排：源 `\begin{array}{r l}` 推导链、既有 aligned 改排产物、裸
多行体先拆成行（嵌套环境如行列式整键不动），每行按最宽行字符比例估宽，
超过 `WIDE_SPLIT_LINE_EX`(44ex) 的行在**深度 0 关系符**（`= < >` 与命令形式
`\leq`/`\geqslant`/`\Rightarrow`…；花括号/`\left(` 组内的不算）处断行，重组为
`\begin{aligned}`（续行以关系符开头、`&` 对齐行内首个关系符，标准数学续排
样式），新 LaTeX 重新渲染。切点落在未闭合 `\left` 组内时按组深补发幻影
定界符（上行尾 `\right.`、续行首 `\left.`，原书自己的续排写法）；裸尺寸
修饰 `\Big`/`\big` 不配对、不计深度（曾把每个收尾 `\Big)` 当开组计数，
多发出不成对幻影 → MathJax 拒渲，退回 TeX 原文）。**两轮收敛**：改排后按
实测渲染宽度复测，仍超宽的再切一轮（修正字符比例估宽的偏差）；重组输出
经 `\left`/`\right` 计数平衡校验，不平衡即放弃改排（缩盒位图好过 TeX 原文）。
不可拆的（嵌套行列式、无深度 0 关系符的单项）保留等比缩盒兜底。第二册
255 处超宽，行级改排 291 处后仅剩 80 处不可拆（51 处在 45–50ex 不可见带、
15 处 50–55ex 轻微、14 处 >55ex 明显，多数为行列式）。好引擎同样受益
（窄屏上长公式不再溢出）。

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

**行内公式 px 高度分档**（`INLINE_PX_BANDS`，2026-09 第二册设备轮次）：px
兜底高度若按墨迹高度连续取值，ex 盲引擎（掌阅）上相邻公式盒高在 12–24px
间随机跳变——同一编号列表内"忽大忽小"（设备回报）。改为按墨迹高度分四档
（边界 1.8/2.65/3.5ex → px = px_per_ex×{1.9, 2.4, 3.0, 4.0}，即 15/19/24/32px），
近高等公式盒高完全一致；vertical-align 的 px 兜底按同伸缩因子缩放，基线
不漂移。ex 真值不动，完整引擎零变化。替代了早期的 `INLINE_BOOST` 放大
（分档的高档已覆盖其意图，且消除了 3ex 处的 1.3× 突变）。

**超宽不可拆 display 等比缩盒**：双钉宽高的盒子超出列宽时 KOReader 按列宽
钳宽、保高——水平压扁（设备回报"某些公式被水平压缩"，双端可见）。对宽于
画布（45ex）且行级改排拆不动的公式（嵌套行列式、无深度 0 关系符的单项），
把盒宽收到画布、高度按纵横比等比缩小：任何引擎下不畸变、不溢出，字形
等比缩小（好于压扁）。svg 模式的 display 盒同规则处理。

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
   （`\bf`→`\mathbf` 等）→ 连续薄空距（`\,\,\,…` OCR 空白噪声，曾栅格出
   3097ex 宽的退化盒）收敛为单个 `\,` → 剔除 OCR 混入公式的版面式号
   `\tag{…}`（MathJax 对 `\tag` 产出无根 viewBox 的 100% 宽全行表格，
   探针按内部比例渲染、位图坍缩成竖条；texmath 则直接拒绝转换）→
   按书字面替换（`text_replacements`）
   → 切除前言/纸质目录 → 删页眉标记 → 标题重分级 → 垃圾 sub/sup 清理 →
   断言（公式计数不变、标题骨架精确匹配）
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
