#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm-terms 可视化渲染器（确定性脚本，仅标准库，用仓库 .venv 运行）

输入：llm-terms.json —— LLM 语义解析产出的数据资产（本脚本不读 markdown，
语义（类型聚类、前后代/协作边）全部由资产携带；脚本只做派生计算与渲染）。
输出：llm-terms.html —— 交互式 D3 力导向图（D3 v7 走 CDN，打开需联网）。

派生计算（每次渲染重算，不存在资产中）：
- related_count = 相关概念个数（无向邻居数：前代/后代/协作不分方向去重计数）→ 节点大小
- term/succession/collaboration 计数 → 标题栏

校验：边端点不存在 → 非零退出；节点类型未定义 → 告警并归「未分类」。

用法：.venv/bin/python render.py [--json PATH] [--out PATH]
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ROOT = SKILL_DIR.parent.parent.parent

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LLM 行业术语图谱</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 100%; height: 100%; overflow: hidden; font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: #f7f8fa; }
  header { position: fixed; top: 0; left: 0; right: 0; z-index: 20; background: rgba(255,255,255,.94); border-bottom: 1px solid #e3e6ea; padding: 8px 14px 6px; display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; }
  header h1 { font-size: 16px; color: #24292f; font-weight: 650; }
  header .meta { font-size: 11px; color: #8b949e; }
  .controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-left: auto; }
  .ctrl { display: flex; align-items: center; gap: 4px; font-size: 12px; color: #57606a; }
  .stage-chk { display: inline-flex; align-items: center; gap: 3px; font-size: 12px; cursor: pointer; user-select: none; }
  .stage-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  input[type="search"] { padding: 4px 8px; border: 1px solid #d0d7de; border-radius: 6px; font-size: 12px; width: 150px; outline: none; }
  input[type="search"]:focus { border-color: #4e79a7; }
  select { padding: 4px 6px; border: 1px solid #d0d7de; border-radius: 6px; font-size: 12px; background: #fff; max-width: 220px; }
  button { padding: 4px 10px; border: 1px solid #d0d7de; border-radius: 6px; font-size: 12px; background: #fff; cursor: pointer; color: #24292f; }
  button:hover { background: #eef1f4; }
  button.active { background: #4e79a7; color: #fff; border-color: #4e79a7; }
  #stats { position: fixed; top: 52px; left: 12px; z-index: 15; font-size: 11px; color: #8b949e; background: rgba(255,255,255,.85); padding: 3px 8px; border-radius: 6px; }
  #legend { position: fixed; left: 12px; bottom: 12px; z-index: 15; background: rgba(255,255,255,.92); border: 1px solid #e3e6ea; border-radius: 8px; padding: 8px 12px; font-size: 11px; color: #57606a; line-height: 1.9; }
  #legend .row { display: flex; align-items: center; gap: 6px; }
  svg { display: block; }
  .node circle { stroke: #fff; stroke-width: 1.5; cursor: grab; }
  .node.pinned circle { stroke: #24292f; stroke-width: 2; }
  .node text { font-size: 10.5px; fill: #444; pointer-events: none; text-anchor: middle; paint-order: stroke; stroke: rgba(247,248,250,.9); stroke-width: 2.5px; }
  .node.dimmed { opacity: .12; }
  .node.hidden { display: none; }
  .node.highlight circle { stroke: #24292f; stroke-width: 2.5; }
  /* 点击聚光：本节点与关联节点圆周上滚动的加粗虚线环 */
  .node .ring { fill: none; stroke: #24292f; stroke-width: 3; stroke-dasharray: 7 5; opacity: 0; pointer-events: none; }
  .node.spotlight .ring { opacity: 1; animation: ring-run .9s linear infinite; }
  @keyframes ring-run { to { stroke-dashoffset: -24; } }
  .link { stroke: #9aa0a6; stroke-opacity: .55; }
  .link-g.hidden { display: none; }
  .link.dimmed { stroke-opacity: .08; }
  /* 热线：加粗加深；前后代线叠加沿箭头方向流动的虚线动画（与聚光环同款） */
  .link-hot { stroke: #24292f !important; stroke-opacity: 1 !important; stroke-width: 2.4; }
  .link-hot.flow { stroke-dasharray: 7 5; animation: dash-flow .9s linear infinite; }
  @keyframes dash-flow { to { stroke-dashoffset: -24; } }
  .link-hit { stroke: transparent; stroke-width: 12; cursor: pointer; }
  .edge-label { font-size: 10px; fill: #6e7681; paint-order: stroke; stroke: rgba(247,248,250,.95); stroke-width: 3px; text-anchor: middle; display: none; pointer-events: none; }
  body.labels-on .edge-label { display: block; }
  .edge-label.force-show { display: block; }
  /* 详情卡片：悬停跟随、可移入、可拖动、可钉住置顶、可关闭 */
  .card { position: fixed; z-index: 1000; max-width: 430px; max-height: 62vh; overflow-y: auto; background: #fff; border: 1px solid #d0d7de; border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.14); padding: 10px 14px 12px; font-size: 12px; color: #444; line-height: 1.65; cursor: grab; }
  .card.dragging { cursor: grabbing; }
  .card.pinned { border-color: #4e79a7; box-shadow: 0 10px 28px rgba(0,0,0,.22); }
  .card-head { display: flex; align-items: flex-start; gap: 8px; }
  .card-head h2 { font-size: 15px; color: #1f2328; margin-bottom: 2px; flex: 1; }
  .cbtns { display: flex; gap: 4px; flex-shrink: 0; }
  .cbtn { padding: 1px 8px; border: 1px solid #d0d7de; border-radius: 6px; background: #fff; cursor: pointer; font-size: 11px; line-height: 1.5; color: #24292f; }
  .cbtn:hover { background: #eef1f4; }
  .cbtn.pin.on { background: #4e79a7; color: #fff; border-color: #4e79a7; }
  .card .sub { color: #8b949e; font-size: 11px; margin-bottom: 6px; }
  .card .badges { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 8px; }
  .card .badge { font-size: 10.5px; padding: 1px 8px; border-radius: 10px; color: #fff; }
  .card .badge.type { background: #6e7681; }
  .card .sec { margin-top: 7px; }
  .card .sec b { display: block; font-size: 11px; color: #4e79a7; margin-bottom: 1px; }
</style>
</head>
<body>
<header>
  <h1>LLM 行业术语图谱</h1>
  <span class="meta" id="meta"></span>
  <div class="controls">
    <input type="search" id="search" placeholder="搜索术语…（回车定位）">
    <span id="stage-filters" class="ctrl"></span>
    <select id="type-filter"></select>
    <button id="btn-labels" title="常显 / 悬停显示连线关系文字">关系标签：关</button>
    <button id="btn-relayout">重新布局</button>
    <button id="btn-reset-zoom">复位视角</button>
  </div>
</header>
<div id="stats"></div>
<div id="legend"></div>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script>
const DATA = __PAYLOAD__;
const STAGE_COLORS = { "预训练": "#4e79a7", "后训练": "#f28e2b", "模型推理": "#59a14f", "智能体": "#e15759" };
const STAGES = DATA.stages;

const W = window.innerWidth, H = window.innerHeight;
const svg = d3.select("body").append("svg").attr("width", W).attr("height", H);
const defs = svg.append("defs");
defs.append("marker").attr("id", "arrow").attr("viewBox", "0 0 10 10")
  .attr("refX", 9).attr("refY", 5).attr("markerWidth", 5.5).attr("markerHeight", 5.5)
  .attr("orient", "auto-start-reverse")
  .append("path").attr("d", "M0,0 L10,5 L0,10 z").attr("fill", "#9aa0a6");
defs.append("marker").attr("id", "arrow-hot").attr("viewBox", "0 0 10 10")
  .attr("refX", 9).attr("refY", 5).attr("markerWidth", 5.5).attr("markerHeight", 5.5)
  .attr("orient", "auto-start-reverse")
  .append("path").attr("d", "M0,0 L10,5 L0,10 z").attr("fill", "#24292f");
const viewport = svg.append("g").attr("id", "viewport");
const gEdges = viewport.append("g"), gNodes = viewport.append("g");

const nodes = DATA.nodes.map(d => ({ ...d }));
const edges = DATA.edges.map((e, i) => ({ ...e, idx: i }));

// 节点半径：6 + 3.2·√相关概念个数（上限 34，related_count 由渲染脚本派生注入）
const radius = d => Math.min(34, 6 + 3.2 * Math.sqrt(d.related_count || 0));

// 类型中心：生命期分四列，类型在列内纵向排布 → 初始聚簇
const stageTypes = {};
DATA.types.forEach(t => (stageTypes[t.stage] = stageTypes[t.stage] || []).push(t.name));
const typeCenter = {};
Object.entries(stageTypes).forEach(([st, arr], si) => {
  arr.forEach((t, i) => { typeCenter[t] = { x: W * (si + 0.5) / 4, y: H * (i + 1) / (arr.length + 1) }; });
});

// 确定性初始坐标（种子随机，重复渲染布局一致）
let seed = 42;
const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
function seedPositions() {
  nodes.forEach(n => {
    const c = typeCenter[n.type] || { x: W / 2, y: H / 2 };
    n.x = c.x + (rnd() - 0.5) * 130;
    n.y = c.y + (rnd() - 0.5) * 130;
  });
}
seedPositions();

const sim = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(edges).id(d => d.id)
    .distance(d => d.kind === "succession" ? 72 : 115)
    .strength(d => d.kind === "succession" ? 0.22 : 0.1))
  .force("charge", d3.forceManyBody().strength(d => -40 - 8 * radius(d)))
  .force("collide", d3.forceCollide().radius(d => radius(d) + 15).iterations(2))
  .force("x", d3.forceX(d => (typeCenter[d.type] || { x: W / 2 }).x).strength(0.055))
  .force("y", d3.forceY(d => (typeCenter[d.type] || { x: W / 2 }).y).strength(0.055))
  .on("tick", ticked);

// ── 连线：前代→后代带箭头虚线；协作为无箭头虚线；关系文字悬停显示可常显 ──
const linkG = gEdges.selectAll("g").data(edges).join("g").attr("class", "link-g");
const linkLine = linkG.append("line")
  .attr("class", "link")
  .attr("stroke-dasharray", "5 4")
  .attr("marker-end", d => d.kind === "succession" ? "url(#arrow)" : null);
const linkLabel = linkG.append("text")
  .attr("class", "edge-label")
  .text(d => d.kind === "succession" ? (d.also_collab ? "前代·协作" : "前代") : "协作");
const linkHit = linkG.append("line")
  .attr("class", "link-hit")
  .on("mouseenter", (e, d) => { d._hover = true; applyEdgeVisual(d); })
  .on("mouseleave", (e, d) => { d._hover = false; applyEdgeVisual(d); });
// 热线状态机：加粗/标签显示 = 聚光(_spot) OR 悬停(_hover)；
// 流动动画与深色箭头仅由聚光驱动——悬停不得覆盖或熄灭聚光状态
function applyEdgeVisual(l) {
  const hot = !!(l._spot || l._hover);
  const flow = !!l._spot && l.kind === "succession";
  linkLine.filter(x => x === l)
    .classed("link-hot", hot)
    .classed("flow", hot && flow)
    .attr("marker-end", d => d.kind !== "succession" ? null : `url(#${hot ? "arrow-hot" : "arrow"})`);
  linkLabel.filter(x => x === l).classed("force-show", hot);
}

function ticked() {
  linkLine.each(function (d) { place(this, d); });
  linkHit.each(function (d) { place(this, d); });
  linkLabel.attr("x", d => (d.source.x + d.target.x) / 2).attr("y", d => (d.source.y + d.target.y) / 2 - 3);
  nodeG.attr("transform", d => `translate(${d.x},${d.y})`);
}
// 端点收缩到圆边（箭头不被节点遮住）
function place(el, d) {
  const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
  const L = Math.hypot(dx, dy) || 1;
  const ux = dx / L, uy = dy / L;
  const rs = (radius(d.source) || 6) + 2, rt = (radius(d.target) || 6) + (d.kind === "succession" ? 7 : 2);
  d3.select(el).attr("x1", d.source.x + ux * rs).attr("y1", d.source.y + uy * rs)
    .attr("x2", d.target.x - ux * rt).attr("y2", d.target.y - uy * rt);
}

// ── 节点：生命期四色圆点 + 聚光环 + 标签；拖拽固定、双击解除 ──
const nodeG = gNodes.selectAll("g").data(nodes).join("g").attr("class", "node");
nodeG.append("circle").attr("class", "ring").attr("r", d => radius(d) + 5);
nodeG.append("circle").attr("r", radius)
  .attr("fill", d => STAGE_COLORS[d.stage] || "#999");
nodeG.append("text").attr("dy", d => radius(d) + 12).text(d => d.id);

nodeG.call(d3.drag()
  .on("start", (e, d) => { d._moved = false; if (!e.active) sim.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y; })
  .on("drag", (e, d) => { d._moved = true; d.fx = e.x; d.fy = e.y; })
  .on("end", (e, d) => {
    if (!e.active) sim.alphaTarget(0);
    if (d._moved) {
      d3.select(e.sourceEvent.target.closest("g")).classed("pinned", true);
    } else {
      d.fx = null; d.fy = null;   // 纯点击未拖动：不固定、不加黑框
    }
  }))
  .on("dblclick", (e, d) => { d.fx = null; d.fy = null; d3.select(e.currentTarget).classed("pinned", false); sim.alpha(0.3).restart(); });

// 点击聚光：本节点与直接关联节点圆周出现滚动的加粗虚线环，相连线加粗并显示
// 关系文字（前后代线沿箭头流动）；点击空白清除；再次点击同一节点也可取消
let selectedId = null;
function clearSpotlight() {
  selectedId = null;
  nodeG.classed("spotlight", false);
  edges.forEach(ed => { if (ed._spot) { ed._spot = false; applyEdgeVisual(ed); } });
}
// 解除所有固定态（黑色描边 + 冻结坐标）
function unpinAll() {
  nodeG.classed("pinned", false);
  nodes.forEach(n => { n.fx = null; n.fy = null; });
}
nodeG.on("click", (e, d) => {
  if (e.defaultPrevented) return;   // 拖拽后的 click 忽略
  e.stopPropagation();
  if (selectedId === d.id) { clearSpotlight(); return; }
  clearSpotlight();
  selectedId = d.id;
  const nbrs = new Set([d.id]);
  edges.forEach(ed => {
    if (ed.source.id === d.id || ed.target.id === d.id) {
      nbrs.add(ed.source.id); nbrs.add(ed.target.id);
      ed._spot = true;
      applyEdgeVisual(ed);
    }
  });
  nodeG.classed("spotlight", n => nbrs.has(n.id));
});
// 空白点击（非节点、非工具栏、非信息卡片的任意位置）：清聚光 + 解除全部固定态
d3.select(window).on("click", e => {
  const t = e.target;
  if (t.closest && (t.closest(".node") || t.closest("header") || t.closest(".card"))) return;
  clearSpotlight();
  unpinAll();
});

// ── 详情卡片：悬停跟随、可移入不消失、可拖动、可钉住置顶、可关闭 ──
function esc(s) { return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
let liveCard = null;      // 当前未钉住的悬浮卡（同一时刻至多一张）
let hideTimer = null;
let zLive = 1000, zPinned = 5000;

function cardBodyHTML(d) {
  const c = STAGE_COLORS[d.stage] || "#999";
  return `<div class="sub">${esc(d.cn)} ｜ ${esc(d.en)}</div>` +
    `<div class="badges"><span class="badge" style="background:${c}">${esc(d.stage)}</span>` +
    `<span class="badge type">${esc(d.type)}</span>` +
    `<span class="badge" style="background:#8b949e">相关概念 ${d.related_count}</span></div>` +
    `<div class="sec"><b>应用场景与解决的问题</b>${esc(d.scenario)}</div>` +
    `<div class="sec"><b>具体做法</b>${esc(d.practice)}</div>` +
    `<div class="sec"><b>底层原理</b>${esc(d.principle)}</div>` +
    `<div class="sec"><b>相关技术</b>${esc(d.related_raw)}</div>`;
}
function positionCard(el, x, y) {
  const w = el.offsetWidth, h = el.offsetHeight;
  if (x + w > window.innerWidth - 8) x = Math.max(8, window.innerWidth - w - 8);
  if (y + h > window.innerHeight - 8) y = Math.max(8, window.innerHeight - h - 8);
  el.style.left = x + "px"; el.style.top = y + "px";
}
function raise(el, pinned) { el.style.zIndex = pinned ? ++zPinned : ++zLive; }
function scheduleHide(st) {
  cancelHide();
  hideTimer = setTimeout(() => removeCard(st), 180);
}
function cancelHide() { if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; } }
function removeCard(st) {
  if (hideTimer) cancelHide();
  st.el.remove();
  if (liveCard === st) liveCard = null;
}
function togglePin(st) {
  st.pinned = !st.pinned;
  st.el.classList.toggle("pinned", st.pinned);
  st.el.querySelector(".pin").classList.toggle("on", st.pinned);
  raise(st.el, st.pinned);
  if (st.pinned) { cancelHide(); if (liveCard === st) liveCard = null; }
  else if (!st.el.matches(":hover")) scheduleHide(st);   // 取消钉住且鼠标不在卡上 → 淡出
}
function createCard(d, x, y) {
  const el = document.createElement("div");
  el.className = "card";
  el.innerHTML = `<div class="card-head"><h2>${esc(d.id)}</h2><span class="cbtns">` +
    `<button class="cbtn pin" title="钉住 / 取消钉住">📌</button>` +
    `<button class="cbtn close" title="关闭">✕</button></span></div><div class="card-body"></div>`;
  document.body.appendChild(el);
  const st = { el, body: el.querySelector(".card-body"), d, pinned: false };
  st.body.innerHTML = cardBodyHTML(d);
  positionCard(el, x, y);
  el.querySelector(".pin").addEventListener("click", ev => { ev.stopPropagation(); togglePin(st); });
  el.querySelector(".close").addEventListener("click", ev => { ev.stopPropagation(); removeCard(st); });
  el.addEventListener("mouseenter", () => { cancelHide(); raise(el, st.pinned); });
  el.addEventListener("mouseleave", () => { if (!st.pinned) scheduleHide(st); });
  // 卡上任意处可拖动（按钮除外）
  el.addEventListener("pointerdown", ev => {
    if (ev.target.closest("button")) return;
    raise(el, st.pinned);
    el.classList.add("dragging");
    const ox = ev.clientX - el.offsetLeft, oy = ev.clientY - el.offsetTop;
    const move = m => positionCard(el, m.clientX - ox, m.clientY - oy);
    const up = () => {
      el.classList.remove("dragging");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    ev.preventDefault();
  });
  raise(el, false);
  return st;
}

nodeG.on("mouseenter", (e, d) => {
  cancelHide();
  if (!liveCard) liveCard = createCard(d, e.clientX + 16, e.clientY + 14);
  else { liveCard.d = d; liveCard.body.innerHTML = cardBodyHTML(d); positionCard(liveCard.el, e.clientX + 16, e.clientY + 14); }
})
  .on("mousemove", e => { if (liveCard) positionCard(liveCard.el, e.clientX + 16, e.clientY + 14); })
  .on("mouseleave", e => {
    if (liveCard && e.relatedTarget && liveCard.el.contains(e.relatedTarget)) return; // 移到卡片上：不消失
    if (liveCard) scheduleHide(liveCard);
  });

// ── 缩放平移 ──
const zoom = d3.zoom().scaleExtent([0.12, 8]).on("zoom", e => viewport.attr("transform", e.transform));
svg.call(zoom).call(zoom.transform, d3.zoomIdentity);
document.getElementById("btn-reset-zoom").onclick = () => svg.transition().duration(400).call(zoom.transform, d3.zoomIdentity);

// ── 工具栏 ──
const stageOn = {}; STAGES.forEach(s => stageOn[s] = true);
const sf = d3.select("#stage-filters");
sf.selectAll("label").data(STAGES).join("label")
  .attr("class", "stage-chk")
  .html(d => `<input type="checkbox" checked><span class="stage-dot" style="background:${STAGE_COLORS[d]}"></span>${d}`)
  .on("change", (e, d) => { stageOn[d] = e.target.checked; applyFilter(); });

const typeSel = d3.select("#type-filter");
const typeCounts = {};
nodes.forEach(n => typeCounts[n.type] = (typeCounts[n.type] || 0) + 1);
const typeStage = {};
DATA.types.forEach(t => typeStage[t.name] = t.stage);
typeSel.append("option").attr("value", "all").text("全部类型");
// 类型按生命期分组展示：optgroup 组头为生命期名（不可选择），子项缩进，便于与生命期复选框交叉筛选
STAGES.concat(["其他"]).forEach(st => {
  const names = Object.keys(typeCounts)
    .filter(t => (typeStage[t] || "其他") === st)
    .sort((a, b) => typeCounts[b] - typeCounts[a]);
  if (!names.length) return;
  const og = typeSel.append("optgroup").attr("label", st);
  og.selectAll(null).data(names).join("option")
    .attr("value", d => d).text(d => `　${d}（${typeCounts[d]}）`);
});
typeSel.on("change", () => applyFilter());

const search = d3.select("#search");
let query = "";
search.on("input", () => { query = search.property("value").trim().toLowerCase(); applyFilter(); });
search.on("keydown", e => {
  if (e.key !== "Enter" || !query) return;
  const hit = nodes.find(n => (n.id + n.cn).toLowerCase().includes(query));
  if (hit) svg.transition().duration(600).call(zoom.transform, d3.zoomIdentity.translate(W / 2, H / 2).scale(1.6).translate(-hit.x, -hit.y));
});

const btnLabels = document.getElementById("btn-labels");
btnLabels.onclick = () => {
  document.body.classList.toggle("labels-on");
  const on = document.body.classList.contains("labels-on");
  btnLabels.textContent = "关系标签：" + (on ? "开" : "关");
  btnLabels.classList.toggle("active", on);
};

document.getElementById("btn-relayout").onclick = () => {
  unpinAll();
  clearSpotlight();
  seed = 42; seedPositions();
  sim.alpha(1).restart();
};

// ── 过滤与统计 ──
function applyFilter() {
  const tv = typeSel.property("value");
  nodes.forEach(n => { n._vis = stageOn[n.stage] && (tv === "all" || n.type === tv); });
  nodeG.classed("hidden", d => !d._vis)
    .classed("dimmed", d => d._vis && query && !d.id.toLowerCase().includes(query) && !d.cn.toLowerCase().includes(query));
  linkG.classed("hidden", d => !(d.source._vis && d.target._vis));
  const vn = nodes.filter(n => n._vis).length;
  const ve = edges.filter(e => e.source._vis && e.target._vis).length;
  document.getElementById("stats").textContent =
    `节点 ${vn}/${nodes.length} ｜ 连线 ${ve}/${edges.length} ｜ 节点大小 = 相关概念个数 ｜ 点击节点聚焦关联，点击空白取消`;
}

// 图例
document.getElementById("legend").innerHTML =
  STAGES.map(s => `<div class="row"><span class="stage-dot" style="background:${STAGE_COLORS[s]}"></span>${s}（${nodes.filter(n => n.stage === s).length}）</div>`).join("") +
  `<div class="row"><svg width="34" height="10"><line x1="1" y1="5" x2="33" y2="5" stroke="#9aa0a6" stroke-dasharray="5 4"/></svg>协作</div>` +
  `<div class="row"><svg width="34" height="10"><defs><marker id="lg-a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5.5" markerHeight="5.5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#9aa0a6"/></marker></defs><line x1="1" y1="5" x2="27" y2="5" stroke="#9aa0a6" stroke-dasharray="5 4" marker-end="url(#lg-a)"/></svg>前代 → 后代</div>`;

document.getElementById("meta").textContent =
  `来源 ${DATA.meta.source} ｜ ${DATA.meta.term_count} 术语 ｜ ${DATA.meta.succession_edges} 前后代 + ${DATA.meta.collaboration_edges} 协作 ｜ 渲染于 ${DATA.meta.rendered_at}`;
applyFilter();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "llm-terms.json"))
    ap.add_argument("--out", default=str(ROOT / "llm-terms.html"))
    args = ap.parse_args()

    data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    ids = [n["id"] for n in data["nodes"]]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        print(f"[error] 节点 id 重复: {dup}", file=sys.stderr)
        sys.exit(1)
    id_set = set(ids)
    type_names = {t["name"] for t in data["types"]}
    bad = [{"source": e["source"], "target": e["target"]} for e in data["edges"]
           if e["source"] not in id_set or e["target"] not in id_set]
    if bad:
        print(f"[error] 边端点不存在（共 {len(bad)} 条，示例 {bad[:3]}）", file=sys.stderr)
        sys.exit(1)
    untyped = [n["id"] for n in data["nodes"] if n["type"] not in type_names]
    if untyped:
        print(f"[warn] 以下节点类型未在 types 中定义（保持原值）: {untyped}", file=sys.stderr)
        for n in data["nodes"]:
            if n["id"] in untyped:
                n["type"] = "未分类"

    # 派生：related_count = 相关概念个数（无向邻居数，不分边类型与方向去重）
    related = {}
    for e in data["edges"]:
        related.setdefault(e["source"], set()).add(e["target"])
        related.setdefault(e["target"], set()).add(e["source"])
    for n in data["nodes"]:
        n["related_count"] = len(related.get(n["id"], ()))
    top = sorted(data["nodes"], key=lambda n: -n["related_count"])[:8]
    print("相关概念 Top 8: " + "、".join(f"{n['id']}({n['related_count']})" for n in top))

    n_succ = sum(1 for e in data["edges"] if e["kind"] == "succession")
    n_coll = len(data["edges"]) - n_succ
    payload = dict(data)
    payload["meta"] = {**data["meta"], "term_count": len(data["nodes"]),
                       "succession_edges": n_succ, "collaboration_edges": n_coll,
                       "rendered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    html = TEMPLATE.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"🎨 已生成 {args.out}（{len(ids)} 节点 / {len(data['edges'])} 连线；D3 走 CDN，打开需联网）")


if __name__ == "__main__":
    main()
