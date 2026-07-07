// postprocess.js - the post-processing Look Lab. A modal panel opened via
// emit('open','postprocess'). The user browses 26 steps (each with a precomputed
// sample thumbnail), builds an ordered chain, sees a live preview against the
// selected asset's chosen image, and either runs a one-click preset or the chain.
import { api } from './api.js';
import { state, on, emit, toast } from './state.js';

// step-name -> category grouping, for scannable browsing
const GROUPS = [
  ['Cleanup', ['trim', 'bg_remove', 'crop_square', 'resize', 'sharpen', 'adjust', 'drop_shadow', 'outline', 'frame', 'upscale']],
  ['Retro / Pixel', ['dither', 'pixelate', 'palette_map', 'scanlines']],
  ['Illustration', ['toon', 'duotone', 'halftone']],
  ['FX', ['grain', 'chromatic', 'glow', 'vignette', 'sepia', 'invert']],
  ['Game-tech', ['normal_map']],
  ['Vector', ['vectorize']],
];

const PRESETS = [
  ['game_sprite', 'Game sprite'],
  ['gameboy', 'Game Boy'],
  ['pixel_sprite', 'Pixel sprite'],
  ['comic', 'Comic'],
  ['retro_crt', 'Retro CRT'],
  ['to_vector', 'To vector'],
  ['hi_res', 'Hi-res'],
];

// module-local view state
let steps = [];         // [{name,description,params,available,note}]
let stepMap = {};       // name -> step
let samples = {};       // stepName -> url
let chain = [];         // [{step, params}]
let previewSeq = 0;     // guards against stale async previews

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function srcOf() { return '/outputs/' + state.current + '/chosen.png'; }

// ---- open / close -----------------------------------------------------------

function modalEl() { return document.getElementById('modal'); }

function close() {
  const m = modalEl();
  if (m) { m.innerHTML = ''; m.classList.remove('open'); }
  document.removeEventListener('keydown', onKey);
}

function onKey(e) { if (e.key === 'Escape') { close(); } }

async function open() {
  const m = modalEl();
  if (!m) { return; }
  document.addEventListener('keydown', onKey);
  m.classList.add('open');

  if (!state.current) {
    m.innerHTML = `<div class="box"><div class="hd"><b>Post-processing</b>`
      + `<button class="btn sm ghost" id="ppx">&#10005;</button></div>`
      + `<div class="bd"><div class="faint">Pick a hero and choose a winner first.</div></div></div>`;
    const x = m.querySelector('#ppx'); if (x) { x.onclick = close; }
    return;
  }

  // fresh chain each open
  chain = [];
  m.innerHTML = `<div class="box"><div class="hd"><b>Look Lab</b>`
    + ` <span class="faint">${esc(state.current)}</span>`
    + `<button class="btn sm ghost" id="ppx">&#10005;</button></div>`
    + `<div class="bd"><div class="faint"><span class="spinner"></span> Loading steps...</div></div></div>`;
  m.querySelector('#ppx').onclick = close;

  try {
    const [st, sm] = await Promise.all([api.ppSteps(), api.ppSamples()]);
    steps = (st && st.steps) || st || [];
    if (!Array.isArray(steps) && steps) { steps = Object.values(steps); }
    stepMap = {};
    steps.forEach((s) => { stepMap[s.name] = s; });
    samples = (sm && sm.samples) || {};
    renderBody();
  } catch (e) {
    const bd = m.querySelector('.bd');
    if (bd) { bd.innerHTML = `<div class="faint">Could not load steps: ${esc(e.message)}</div>`; }
  }
}

// ---- body layout ------------------------------------------------------------

function renderBody() {
  const m = modalEl(); if (!m) { return; }
  const bd = m.querySelector('.bd'); if (!bd) { return; }

  // any steps not covered by a group land under "Other"
  const seen = new Set();
  GROUPS.forEach(([, names]) => names.forEach((n) => seen.add(n)));
  const other = steps.map((s) => s.name).filter((n) => !seen.has(n));
  const groups = other.length ? GROUPS.concat([['Other', other]]) : GROUPS;

  const gallery = groups.map(([label, names]) => {
    const tiles = names.filter((n) => stepMap[n]).map((n) => stepCard(stepMap[n])).join('');
    if (!tiles) { return ''; }
    return `<div class="col" style="gap:6px"><div class="h">${esc(label)}</div>`
      + `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(130px,1fr))">${tiles}</div></div>`;
  }).join('');

  bd.innerHTML = `<div class="row" style="gap:16px;align-items:flex-start">`
    + `<div class="col" style="flex:2;min-width:0;gap:16px">${gallery}</div>`
    + `<div class="col" id="ppside" style="flex:1;min-width:240px;gap:12px"></div>`
    + `</div>`;

  bd.querySelectorAll('[data-add]').forEach((b) => { b.onclick = () => addStep(b.dataset.add); });
  renderSide();
}

function stepCard(s) {
  const thumb = samples[s.name]
    ? `<img src="${esc(samples[s.name])}" alt="" style="aspect-ratio:1;object-fit:cover;width:100%">`
    : `<div style="aspect-ratio:1;display:grid;place-items:center" class="faint">no sample</div>`;
  const note = s.available === false
    ? `<div class="faint" style="font-size:11px">${esc(s.note || 'unavailable')}</div>`
    : '';
  const dis = s.available === false ? ' disabled' : '';
  const style = s.available === false ? 'opacity:.5' : '';
  return `<button class="tile" data-add="${esc(s.name)}"${dis} style="${style}text-align:left">`
    + thumb
    + `<div style="padding:6px"><b style="font-size:13px">${esc(s.name)}</b>`
    + `<div class="faint" style="font-size:11px">${esc(s.description || '')}</div>${note}</div></button>`;
}

// ---- side: presets, chain, preview -----------------------------------------

function renderSide() {
  const side = document.getElementById('ppside'); if (!side) { return; }

  const presetBtns = PRESETS.map(([name, label]) =>
    `<button class="btn sm" data-preset="${esc(name)}">${esc(label)}</button>`).join(' ');

  const chainRows = chain.length
    ? chain.map((c, i) => chainRow(c, i)).join('')
    : `<div class="faint">Click a step to add it to the chain.</div>`;

  side.innerHTML = `
    <div class="card"><div class="h">Presets</div>
      <div class="faint" style="font-size:11px;margin-bottom:6px">One-click chains, queued as a job.</div>
      <div class="row" style="flex-wrap:wrap;gap:6px">${presetBtns}</div></div>
    <div class="card"><div class="h">Chain</div>
      <div class="col" style="gap:8px">${chainRows}</div>
      <div class="row" style="gap:6px;margin-top:10px">
        <button class="btn" id="pprun"${chain.length ? '' : ' disabled'}>Run this chain</button>
        <button class="btn ghost sm" id="ppclear"${chain.length ? '' : ' disabled'}>Clear</button>
      </div></div>
    <div class="card"><div class="h">Preview</div>
      <div id="pppreview" style="display:grid;place-items:center;min-height:120px">
        <img src="${esc(srcOf())}" alt="" style="max-width:100%;border-radius:4px">
      </div></div>`;

  side.querySelectorAll('[data-preset]').forEach((b) => { b.onclick = () => runPreset(b.dataset.preset); });
  side.querySelectorAll('[data-up]').forEach((b) => { b.onclick = () => move(+b.dataset.up, -1); });
  side.querySelectorAll('[data-down]').forEach((b) => { b.onclick = () => move(+b.dataset.down, 1); });
  side.querySelectorAll('[data-rm]').forEach((b) => { b.onclick = () => removeStep(+b.dataset.rm); });
  side.querySelectorAll('[data-pk]').forEach((inp) => { inp.oninput = () => editParam(+inp.dataset.pk, inp.dataset.pn, inp); });

  const run = side.querySelector('#pprun'); if (run) { run.onclick = runChain; }
  const clr = side.querySelector('#ppclear'); if (clr) { clr.onclick = () => { chain = []; renderSide(); }; }

  refreshPreview();
}

function chainRow(c, i) {
  const s = stepMap[c.step] || { params: {} };
  const schema = (s.params && s.params.p) || {};
  const paramFields = Object.entries(schema).map(([pn, def]) => {
    const val = c.params[pn];
    const cur = val == null ? def.default : val;
    if (def.type === 'bool' || def.type === 'boolean') {
      const on = cur === true || cur === 'true';
      return `<label class="faint" style="font-size:11px;display:flex;align-items:center;gap:4px">`
        + `<input type="checkbox" data-pk="${i}" data-pn="${esc(pn)}"${on ? ' checked' : ''}> ${esc(pn)}</label>`;
    }
    const type = (def.type === 'int' || def.type === 'float' || def.type === 'number') ? 'number' : 'text';
    const stepAttr = def.type === 'float' ? ' step="any"' : '';
    return `<label class="faint" style="font-size:11px;display:flex;align-items:center;gap:4px">${esc(pn)}`
      + `<input type="${type}"${stepAttr} data-pk="${i}" data-pn="${esc(pn)}" value="${esc(cur == null ? '' : cur)}"`
      + ` style="width:64px"></label>`;
  }).join('');

  return `<div class="box" style="padding:6px">
    <div class="row" style="justify-content:space-between;align-items:center">
      <b style="font-size:13px">${i + 1}. ${esc(c.step)}</b>
      <span class="row" style="gap:2px">
        <button class="btn sm ghost" data-up="${i}"${i === 0 ? ' disabled' : ''} title="up">&#9650;</button>
        <button class="btn sm ghost" data-down="${i}"${i === chain.length - 1 ? ' disabled' : ''} title="down">&#9660;</button>
        <button class="btn sm ghost" data-rm="${i}" title="remove">&#10005;</button>
      </span></div>
    ${paramFields ? `<div class="row" style="flex-wrap:wrap;gap:6px;margin-top:4px">${paramFields}</div>` : ''}
  </div>`;
}

// ---- chain mutation ---------------------------------------------------------

function addStep(name) {
  const s = stepMap[name];
  if (!s || s.available === false) { return; }
  chain.push({ step: name, params: {} });
  renderSide();
}

function removeStep(i) { chain.splice(i, 1); renderSide(); }

function move(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= chain.length) { return; }
  const t = chain[i]; chain[i] = chain[j]; chain[j] = t;
  renderSide();
}

function editParam(i, pn, inp) {
  const c = chain[i]; if (!c) { return; }
  const s = stepMap[c.step] || { params: {} };
  const def = ((s.params && s.params.p) || {})[pn] || {};
  let v;
  if (def.type === 'bool' || def.type === 'boolean') { v = inp.checked; }
  else if (def.type === 'int') { v = inp.value === '' ? null : parseInt(inp.value, 10); }
  else if (def.type === 'float' || def.type === 'number') { v = inp.value === '' ? null : parseFloat(inp.value); }
  else { v = inp.value; }
  if (v == null || (typeof v === 'number' && isNaN(v))) { delete c.params[pn]; }
  else { c.params[pn] = v; }
  refreshPreview();
}

// ---- live preview -----------------------------------------------------------

// Run the whole chain step-by-step against chosen.png. Each step feeds the next
// (dataUrl becomes the src). Show the final image, or the source when empty.
async function refreshPreview() {
  const box = document.getElementById('pppreview'); if (!box) { return; }
  const seq = ++previewSeq;

  if (!chain.length) {
    box.innerHTML = `<img src="${esc(srcOf())}" alt="" style="max-width:100%;border-radius:4px">`;
    return;
  }
  box.innerHTML = `<div class="faint"><span class="spinner"></span> previewing...</div>`;

  let src = srcOf();
  try {
    for (const c of chain) {
      const r = await api.ppPreview(c.step, src, c.params || {});
      if (seq !== previewSeq) { return; } // superseded by a newer edit
      if (r && r.dataUrl) { src = r.dataUrl; }
      else if (r && r.svg) {
        // vector output: render inline SVG as the final result
        if (seq !== previewSeq) { return; }
        box.innerHTML = `<div style="max-width:100%;overflow:auto">${r.svg}</div>`;
        return;
      }
    }
    if (seq !== previewSeq) { return; }
    box.innerHTML = `<img src="${esc(src)}" alt="" style="max-width:100%;border-radius:4px">`;
  } catch (e) {
    if (seq !== previewSeq) { return; }
    box.innerHTML = `<div class="faint">Preview failed: ${esc(e.message)}</div>`;
  }
}

// ---- run --------------------------------------------------------------------

function runChain() {
  if (!chain.length) { return; }
  api.postprocess(state.current, chain.map((c) => ({ step: c.step, params: c.params || {} })))
    .then(() => { toast('Post-processing queued'); close(); })
    .catch((e) => toast('Could not queue: ' + e.message, 'bad'));
}

function runPreset(name) {
  api.postprocess(state.current, null, name)
    .then(() => { toast('Post-processing queued'); close(); })
    .catch((e) => toast('Could not queue: ' + e.message, 'bad'));
}

// ---- mount ------------------------------------------------------------------

export function mount() {
  on('open', (n) => { if (n === 'postprocess') { open(); } });
}
