// checkpoints.js - "Art style engine" picker. Shows a small curated catalog of
// base models (the single biggest quality lever), one-click install with live
// progress, and pick which installed model draws. Opened via emit('open','checkpoints').
import { api } from './api.js';
import { on, toast } from './state.js';

const MINE = 'checkpoints';
let data = { catalog: [], installed: [], active: '', progress: {} };
let poll = null;

function modalEl() { return document.getElementById('modal'); }

function open() {
  const m = modalEl(); if (!m) { return; }
  render();
  load();
  const esc = (e) => { if (e.key === 'Escape') { close(); } };
  document.addEventListener('keydown', esc);
  m._escHandler = esc;
}

function close() {
  const m = modalEl(); if (!m) { return; }
  if (poll) { clearInterval(poll); poll = null; }
  if (m._escHandler) { document.removeEventListener('keydown', m._escHandler); m._escHandler = null; }
  m.innerHTML = '';
  m.classList.remove('open');
}

function load() {
  api.checkpoints()
    .then((d) => { data = d || data; render(); maybePoll(); })
    .catch((e) => toast('Could not load models: ' + e.message, 'bad'));
}

// Keep polling while any install is running, so the bars move on their own.
function maybePoll() {
  const busy = Object.values(data.progress || {}).some((p) => p && !p.done);
  if (busy && !poll) {
    poll = setInterval(() => {
      api.checkpoints().then((d) => {
        data = d || data; render();
        const still = Object.values(data.progress || {}).some((p) => p && !p.done);
        if (!still && poll) { clearInterval(poll); poll = null; }
      }).catch(() => {});
    }, 1500);
  }
}

function render() {
  const m = modalEl(); if (!m) { return; }
  m.classList.add('open');
  const cards = (data.catalog || []).map(cardHtml).join('');
  const options = (data.installed || []).map((f) =>
    `<option value="${esc(f)}" ${f === data.active ? 'selected' : ''}>${esc(f)}</option>`).join('');
  m.innerHTML = `
    <div class="box">
      <div class="hd row">
        <b>Art style engine</b>
        <span style="flex:1"></span>
        <button class="btn sm ghost" id="ck-close">&#10005;</button>
      </div>
      <div class="bd col" style="gap:14px">
        <div class="faint">This is the model that draws. Pick one that fits your subject. Each is about 2 GB and downloads once. You can switch anytime; it applies to the next batch.</div>
        <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
          <input id="ck-desc" class="in" placeholder="Not sure? Describe what you're making (e.g. cute cartoon mascot)" style="flex:1;min-width:220px">
          <button class="btn sm" id="ck-rec">Help me choose</button>
        </div>
        <div id="ck-rec-out"></div>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px">${cards}</div>
        <div class="h">Active model</div>
        <div class="row" style="gap:8px;flex-wrap:wrap">
          ${options ? `<select id="ck-active" class="in" style="flex:1;min-width:200px">${options}</select>`
                    : '<span class="faint">No models installed yet. Install one above.</span>'}
        </div>
        <div class="faint">Advanced: you can also drop a .safetensors file into ComfyUI's models/checkpoints folder and it will show up here.</div>
      </div>
    </div>`;

  m.querySelector('#ck-close').onclick = close;
  const recBtn = m.querySelector('#ck-rec');
  const recIn = m.querySelector('#ck-desc');
  const recOut = m.querySelector('#ck-rec-out');
  const doRec = () => {
    const desc = (recIn.value || '').trim();
    if (!desc) { recIn.focus(); return; }
    recOut.innerHTML = '<div class="row" style="gap:6px"><span class="spinner"></span> <span class="faint">Thinking...</span></div>';
    api.checkpointRecommend(desc)
      .then((r) => {
        const p = r && r.pick;
        if (!p) { recOut.innerHTML = '<div class="faint">No suggestion. Pick one below.</div>'; return; }
        const act = p.active ? '<span class="chip" style="color:var(--good);border-color:var(--good)">already active</span>'
          : p.installed ? `<button class="btn sm" id="ck-rec-use">Use ${esc(p.name)}</button>`
          : `<button class="btn sm" id="ck-rec-get">Install ${esc(p.name)}</button>`;
        recOut.innerHTML = `<div class="card col" style="gap:6px">
          <div><b>${esc(p.name)}</b> <span class="faint">${esc(r.reason || p.best_for || '')}</span></div>
          <div class="row" style="gap:6px">${act}</div></div>`;
        const u = recOut.querySelector('#ck-rec-use'); if (u) { u.onclick = () => selectByFile(p.filename); }
        const g = recOut.querySelector('#ck-rec-get'); if (g) { g.onclick = () => install(p.id); }
      })
      .catch((e) => { recOut.innerHTML = `<div class="faint">Could not suggest: ${esc(e.message)}</div>`; });
  };
  if (recBtn) { recBtn.onclick = doRec; }
  if (recIn) { recIn.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); doRec(); } }; }
  const sel = m.querySelector('#ck-active');
  if (sel) {
    sel.onchange = () => {
      api.checkpointSelect(sel.value)
        .then(() => { data.active = sel.value; toast('Now drawing with ' + sel.value, 'good'); render(); })
        .catch((e) => toast('Could not switch: ' + e.message, 'bad'));
    };
  }
  (data.catalog || []).forEach((c) => {
    const btn = m.querySelector(`[data-install="${c.id}"]`);
    if (btn) { btn.onclick = () => install(c.id); }
    const use = m.querySelector(`[data-use="${c.id}"]`);
    if (use) { use.onclick = () => selectByFile(c.filename); }
  });
}

function cardHtml(c) {
  const prog = (data.progress || {})[c.id];
  const tags = (c.tags || []).map((t) => `<span class="chip">${esc(t)}</span>`).join(' ');
  let action;
  if (prog && !prog.done) {
    const pct = Math.round(prog.pct || 0);
    action = `<div class="col" style="gap:4px">
      <div class="row"><span class="spinner"></span> <span class="faint">Downloading ${pct}%</span></div>
      <div style="height:6px;background:var(--line);border-radius:3px;overflow:hidden"><div style="height:100%;width:${pct}%;background:var(--accent)"></div></div>
    </div>`;
  } else if (prog && prog.done && !prog.ok) {
    action = `<div class="col" style="gap:4px">
      <span class="faint" style="color:var(--bad)">Install failed. ${esc(prog.error || '')}</span>
      <button class="btn sm" data-install="${esc(c.id)}">Try again</button></div>`;
  } else if (c.active) {
    action = `<span class="chip" style="color:var(--good);border-color:var(--good)">&#10003; Active</span>`;
  } else if (c.installed) {
    action = `<button class="btn sm" data-use="${esc(c.id)}">Use this</button>`;
  } else {
    action = `<button class="btn sm" data-install="${esc(c.id)}">Install (${sizeLabel(c.size_mb)})</button>`;
  }
  return `
    <div class="card col" style="gap:8px">
      <b style="font-size:15px">${esc(c.name)}</b>
      <div class="row" style="gap:6px;flex-wrap:wrap">${tags}</div>
      <div class="faint" style="font-size:13px;flex:1">${esc(c.best_for)}</div>
      ${action}
    </div>`;
}

function install(id) {
  api.checkpointInstall(id, true)  // download, then make it the active model
    .then(() => { toast('Download started. This runs in the background.', ''); load(); })
    .catch((e) => toast('Could not start: ' + e.message, 'bad'));
}

function selectByFile(filename) {
  api.checkpointSelect(filename)
    .then(() => { data.active = filename; toast('Now drawing with ' + filename, 'good'); load(); })
    .catch((e) => toast('Could not switch: ' + e.message, 'bad'));
}

function sizeLabel(mb) { return mb ? (mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb + ' MB') : ''; }
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

export function mount() {
  on('open', (n) => { if (n === MINE) { open(); } });
}
