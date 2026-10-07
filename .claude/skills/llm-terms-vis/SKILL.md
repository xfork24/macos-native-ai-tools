---
name: llm-terms-vis
description: "Use when the user asks to regenerate, update, or view the LLM terms network visualization (llm-terms.html) — typically after the llm-terms skill appends new terms to llm-terms.md, or on demand to explore technology relationships — the session LLM semantically re-parses the markdown into the llm-terms.json data asset (types clustering, predecessor-successor and collaboration edges), then a fixed venv Python script renders the interactive D3 force-directed graph from the asset"
---

# LLM 行业术语图谱（llm-terms-vis）

把 `llm-terms.md` 术语表维护为交互式网状图谱 `llm-terms.html`：节点按生命期四色着色、大小 ∝ 相关概念个数（无向邻居数）、按类型初始聚簇；前代→后代为虚线箭头、协作为虚线，关系文字悬停显示（可切换常显）；点击节点则该节点及其关联节点圆周出现滚动的加粗虚线环、相连线加粗并显示关系文字，点击空白取消；节点可拖拽、悬停显示七维度详情。

## 架构（解析与渲染严格解耦）

```
llm-terms.md ──LLM 语义解析──▶ llm-terms.json（数据资产）──render.py（固定脚本）──▶ llm-terms.html
```

- **数据资产 `llm-terms.json`（仓库根）**：唯一契约，自包含（节点七维度全文 + 类型 + 边）。由会话 LLM 用语义能力生成与增量维护——**禁止用脚本做规则/正则解析**。将来可用其他解析方式产出同构资产，或让其他渲染器直接消费它。
- **渲染器 `scripts/render.py`（固定脚本）**：只读资产 → 派生计算（dep_count、计数）→ 输出 HTML。不读 markdown，不理解语义；资产或展示规格变了才改它。仅用标准库，必须以 `.venv/bin/python` 运行，**不得引入第三方依赖、不得污染系统环境**。

## 数据资产 schema（llm-terms.json）

```json
{
  "meta": {"source": "llm-terms.md", "generator": "LLM 语义解析（llm-terms-vis 技能维护）"},
  "stages": ["预训练", "后训练", "模型推理", "智能体"],
  "types":  [{"name": "…", "stage": "预训练"}],
  "nodes":  [{"id": "术语（唯一键，与 md 术语列一致）", "stage": "最早生命期", "stages_full": "所属领域原文",
              "type": "类型", "en": "…", "cn": "…",
              "scenario": "…", "practice": "…", "principle": "…", "related_raw": "相关技术列原文"}],
  "edges":  [{"source": "前代", "target": "后代", "kind": "succession", "also_collab": true},
             {"source": "A", "target": "B", "kind": "collaboration"}]
}
```

related_count（相关概念个数＝无向邻居数，前代/后代/协作不分方向去重）与各项计数是**派生值，由 render.py 重算**，资产中不存。

## 文件缺失处理（自愈，每次执行先探测）

**不假设 `llm-terms.md` / `llm-terms.json` / `llm-terms.html` 一直存在**：

- 仅 `llm-terms.html` 缺失 → 直接运行 render.py 重新生成；
- `llm-terms.json` 缺失、`llm-terms.md` 存在 → 对 md 做**全量**语义解析重建资产（按下方工作流程执行，范围为全表而非增量），再渲染；
- `llm-terms.md` 缺失、`llm-terms.json` 存在 → 资产自包含，先渲染恢复 html；md 由资产**反向重建**：资产各字段与 md 列一一对应（id→术语、en→英文全称、cn→中文全称、stages_full→所属领域、scenario/practice/principle/related_raw→后四列），按生命期稳定排序、套用固定表头回填（资产是 LLM 语义产物，此反向回填为机械映射，不违反「禁止脚本规则解析」）；
- md 与 json 皆缺失 → 无法凭空恢复语义，向用户说明；若当前会话上下文仍保有内容（曾完整读过或写过），则依据会话知识重建，并明示数据来源与时间点。

## 工作流程（每次执行）

1. **语义解析**：读 `llm-terms.md`（重点：新增/变更行）。对每个新术语：
   - 归入现有类型，或确属新方向时在 `types` 新建 `{name, stage}`（每生命期 ≤ 12 类）；
   - 从其「前置：/后续：」列**语义**判定前后代边（正文里的中文简称、别称、含注释的写法都要正确解析到术语 id，如「自注意力」→Self-Attention、「推理期扩展」→测试时计算）；
   - 从正文语义判定协作边（明显共事/构成/配套关系，宁缺毋滥）；
   - 检查既有边是否需要补充（新术语常是若干老术语的共同后续）。
   同一对术语若同时是前后代与密切协作，画 succession 边并加 `"also_collab": true`，**不要建两条边**；两行互相把对方列为前置时属数据矛盾，按真实技术脉络只保留一个方向。
2. **增量更新资产**：用 Edit 更新 `llm-terms.json`——追加/修改 nodes 与 edges、必要时调整 types；不重写整个文件。
3. **渲染**：`.venv/bin/python .claude/skills/llm-terms-vis/scripts/render.py`（校验失败会非零退出并指出坏边）。
4. **简报**：节点/边统计、相关概念 Top 榜（渲染日志可见）、本次类型/边调整。

## 维护纪律

- 术语 id 必须与 md「术语」列逐字一致；边端点必须是已有 id
- 资产由 LLM 维护，render.py 不回写资产；两个职责替换任一侧都不影响另一侧
- 展示层新需求（交互、样式）改 render.py 模板；语义口径变化改资产
