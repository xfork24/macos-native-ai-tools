#!/usr/bin/env node
'use strict';
/*
 * Render LaTeX formulas to standalone SVG strings (MathJax v3, mathjax-full).
 *
 *   node render_math.js <input.json> <output.json>
 *
 * input:  { "<hash>": { "latex": "...", "display": true|false }, ... }
 * output: { "<hash>": { "svg": "<svg …>…</svg>", "valign_ex": -0.42 }, ...,
 *           "_errors": { "<hash>": "message" } }
 *
 * The vertical-align ex-offset MathJax computes for baseline alignment is
 * extracted from the root <svg> style and reported separately, so the caller
 * can put it on a wrapper <span> instead.
 */

const fs = require('fs');
const { mathjax } = require('mathjax-full/js/mathjax.js');
const { TeX } = require('mathjax-full/js/input/tex.js');
const { SVG } = require('mathjax-full/js/output/svg.js');
const { liteAdaptor } = require('mathjax-full/js/adaptors/liteAdaptor.js');
const { RegisterHTMLHandler } = require('mathjax-full/js/handlers/html.js');
const { AllPackages } = require('mathjax-full/js/input/tex/AllPackages.js');

const [inPath, outPath] = process.argv.slice(2);
if (!inPath || !outPath) {
  console.error('usage: node render_math.js <input.json> <output.json>');
  process.exit(2);
}
const jobs = JSON.parse(fs.readFileSync(inPath, 'utf8'));

const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const html = mathjax.document('', {
  InputJax: new TeX({ packages: AllPackages }),
  OutputJax: new SVG({ fontCache: 'local' }),
});

const results = {};
const errors = {};
for (const [key, job] of Object.entries(jobs)) {
  try {
    const node = html.convert(job.latex, { display: job.display });
    const wrapped = adaptor.outerHTML(node);
    // Baseline offset lives on the <mjx-container> style — lift it onto the
    // wrapper span the Python side emits, then keep only the inner <svg>.
    let valign = 0;
    const va = wrapped.match(/vertical-align:\s*(-?[\d.]+)ex/);
    if (va) valign = parseFloat(va[1]);
    const svgMatch = wrapped.match(/<svg[\s\S]*<\/svg>/);
    if (!svgMatch) throw new Error('no <svg> in MathJax output');
    let svg = svgMatch[0];
    // The offset moves to the wrapper span — drop it from the svg root so
    // it is not applied twice.
    svg = svg.replace(/\s*style="[^"]*vertical-align:[^"]*"/, '');
    // MathJax renders TeX errors as red text instead of throwing — detect it.
    const mjxError = wrapped.match(/data-mjx-error="([^"]*)"/);
    if (mjxError) throw new Error(mjxError[1]);
    // Night-mode safety: glyph color must follow the document text color.
    if (!/fill="currentColor"/.test(svg)) {
      svg = svg.replace(/<svg /, '<svg fill="currentColor" ');
    }
    results[key] = { svg, valign_ex: valign };
  } catch (err) {
    errors[key] = String((err && err.message) || err).slice(0, 200);
  }
}
if (Object.keys(errors).length) {
  results._errors = errors;
}
fs.writeFileSync(outPath, JSON.stringify(results));
console.error(
  `render_math: ${Object.keys(results).length - Object.keys(errors).length} ok, ` +
  `${Object.keys(errors).length} failed`
);
