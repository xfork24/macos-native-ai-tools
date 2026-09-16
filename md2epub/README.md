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

依赖：pandoc ≥ 3.x、node（svg/png 模式，需先在本目录 `npm install`）、
rsvg-convert（png 模式，`brew install librsvg`）、PyYAML + PIL、pdftoppm、xmllint。

## 三种公式模式（兼容性金字塔）

| | mathml | svg（内联） | png（外链位图） |
|---|---|---|---|
| 体积 | 2.0 MB | 8.1 MB | 14.8 MB |
| 渲染 | 阅读器原生 MathML | 内联 `<svg>` 元素 | `<img>` 引用 2× 32 色调色板 PNG |
| 适用 | Apple Books、Thorium、Calibre 等支持 MathML 的阅读器 | 支持内联 SVG 的现代阅读器（Calibre/KOReader 实测正常） | **不支持内联 SVG 的引擎（掌阅 iReader 等国产电纸书）——位图是 EPUB 强制支持项，必然能显示** |
| 缺点 | 绝大多数电纸书不支持（会显示成 LaTeX 原文） | iReader 等不渲染内联 SVG | 仍有体积、放大有像素；ex 单位 CSS 尺寸仍随字号缩放 |

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
<书目录>/<书名>.mathml.epub   2.0 MB   9,048 MathML 元素
<书目录>/<书名>.svg.epub      8.1 MB   9,067 个 SVG 公式
<书目录>/<书名>.png.epub     14.8 MB   9,086 个 PNG 公式（--math png）
<书目录>/md2epub-build/failures.log   失败清单：mathml 回退 54 处（0.59%）
                                      + svg/png 回退 16 处（0.18%），
                                      均为 OCR 乱码源（未闭合 \right、
                                      非法上标等），需人工核对原书
<书目录>/md2epub-build/math_cache.json  公式渲染缓存（跨运行复用，重跑秒级）
<书目录>/md2epub-build/png_cache/       公式位图缓存（跨运行复用）
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
