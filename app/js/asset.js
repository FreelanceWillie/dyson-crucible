// asset.js - the selected-hero panel. Owns #main only when state.view === 'asset'.
// Shows the candidate gallery (pick a winner, more-like, poses, post-process),
// the brief (prompt / negative / category / ip weight + full history), and the
// reference-image drop area used to steer blend gens. Renders on 'select' and on
// 'view' === 'asset'; re-fetches candidates live when a job for this asset finishes.
import { api } from './api.js';
import { state, on, emit, toast, refreshState, selectAsset, setView } from './state.js';

const el = () => document.getElementById('main');

// module-local view state, reset whenever a different hero is selected
let data = null;          // last /api/asset payload
let loading = false;
let errMsg = '';
let selCandidate = null;  // index of the tile shown enlarged / focused
let pendingRefs = [];     // refs uploaded this session but not yet shown by the server
let dropRole = 'style';   // role applied to the next uploaded reference

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

// pull /api/asset for the current hero, then re-render
async function load() {
  const name = state.current;
  if (!name) { data = null; render(); return; }
  loading = true; errMsg = ''; render();
  try {
    data = await api.asset(name);
    errMsg = '';
  } catch (e) {
    data = null; errMsg = e && e.message ? e.message : 'could not load';
  }
  loading = false;
  render();
}

// resolve the chosen candidate to a URL (chosen may be a url string or an index)
function chosenUrl() {
  if (!data || data.chosen == null) { return null; }
  const ch = data.chosen;
  if (typeof ch === 'string' && ch.indexOf('/') >= 0) { return ch; }
  const cands = data.candidates || [];
  const i = parseInt(ch, 10);
  return cands[i] != null ? cands[i] : (cands[0] || null);
}

function isWinner(url, i) {
  if (!data || data.chosen == null) { return false; }
  const ch = data.chosen;
  return ch === url || ch === i || ch === String(i);
}

// ---- category <select>, built from state.tree paths + Uncategorized ----
function treePaths() {
  const out = [];
  const walk = (nodes) => (nodes || []).forEach((n) => { if (n.path) { out.push(n.path); } if (n.children) { walk(n.children); } });
  walk(state.tree || []);
  return out;
}

function buildCategorySelect(current) {
  const sel = document.createElement('select');
  sel.style.maxWidth = '240px';
  const opts = ['<option value="">Uncategorized</option>']
    .concat(treePaths().map((p) => `<option value="${esc(p)}">${esc(p)}</option>`));
  sel.innerHTML = opts.join('');
  sel.value = current || '';
  sel.onchange = () => {
    api.assign(state.current, sel.value)
      .then(() => { toast('Moved to ' + (sel.value || 'Uncategorized')); return refreshState(); })
      .catch((e) => toast('Could not move: ' + e.message, 'bad'));
  };
  return sel;
}

// ---- enlarge modal via #modal (.overlay.on with a .box) ----
function showModal(url) {
  let m = document.getElementById('modal');
  if (!m) { m = document.createElement('div'); m.id = 'modal'; m.className = 'overlay'; document.body.appendChild(m); }
  m.className = 'overlay on';
  m.innerHTML = `<div class="box"><div class="hd">Preview <button class="btn sm ghost" id="modalx" style="margin-left:auto">Close</button></div>`
    + `<div class="bd" style="text-align:center"><img src="${esc(url)}" alt="candidate" style="max-width:100%;max-height:70vh;border-radius:8px"></div></div>`;
  const close = () => { m.className = 'overlay'; m.innerHTML = ''; };
  m.onclick = (e) => { if (e.target === m) { close(); } };
  const x = m.querySelector('#modalx'); if (x) { x.onclick = close; }
}

// ---- reference images: current thumbs + drop area ----
function currentRefs() {
  const fromData = (data && data.refs) || [];
  // merge server refs with any uploaded this session that the server hasn't echoed yet
  const seen = new Set(fromData.map((r) => r.path));
  return fromData.concat(pendingRefs.filter((r) => !seen.has(r.path)));
}

function persistRefs(refs) {
  return api.refs(state.current, refs.map((r) => ({ role: r.role, path: r.path })));
}

function addRef(role, path) {
  const refs = currentRefs().concat([{ role, path }]);
  pendingRefs = refs.slice();
  return persistRefs(refs)
    .then(() => { toast('Added ' + role + ' reference'); return load(); })
    .catch((e) => toast('Could not save reference: ' + e.message, 'bad'));
}

function removeRef(path) {
  const refs = currentRefs().filter((r) => r.path !== path);
  pendingRefs = refs.slice();
  persistRefs(refs)
    .then(() => { toast('Reference removed'); return load(); })
    .catch((e) => toast('Could not remove: ' + e.message, 'bad'));
}

function readAsDataUrl(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = () => rej(new Error('could not read file'));
    r.readAsDataURL(file);
  });
}

async function uploadFiles(files, role) {
  for (const f of Array.from(files)) {
    if (!f.type || f.type.indexOf('image') !== 0) { toast('Skipped ' + f.name + ' (not an image)', 'bad'); continue; }
    try {
      const dataUrl = await readAsDataUrl(f);
      const up = await api.upload(dataUrl, '_uploads', f.name);
      const path = (up && (up.path || up.file || up.url)) || '';
      if (!path) { throw new Error('upload returned no path'); }
      await addRef(role, path);
    } catch (e) {
      toast('Upload failed: ' + e.message, 'bad');
    }
  }
}

function refsBlock() {
  const box = document.createElement('div');
  box.style.marginTop = '12px';
  box.appendChild(Object.assign(document.createElement('div'), { className: 'h', textContent: 'Reference images (steer blend gens)' }));

  // drop area (drag-drop OR click to pick)
  const dz = document.createElement('div');
  dz.className = 'dz';
  dz.style.cssText = 'border:2px dashed var(--line);border-radius:10px;padding:16px;text-align:center;cursor:pointer;color:var(--dim)';
  dz.textContent = 'Drop an image here, or click to choose. It guides the look and subject of the next blend.';

  const inp = document.createElement('input');
  inp.type = 'file'; inp.accept = 'image/*'; inp.multiple = true; inp.style.display = 'none';

  dz.onclick = () => { dropRole = 'style'; inp.click(); };
  inp.onchange = () => { if (inp.files && inp.files.length) { uploadFiles(inp.files, dropRole); inp.value = ''; } };
  dz.ondragover = (e) => { e.preventDefault(); dz.style.borderColor = 'var(--sun)'; dz.style.color = 'var(--sun)'; };
  dz.ondragleave = () => { dz.style.borderColor = 'var(--line)'; dz.style.color = 'var(--dim)'; };
  dz.ondrop = (e) => {
    e.preventDefault(); dz.style.borderColor = 'var(--line)'; dz.style.color = 'var(--dim)';
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) { uploadFiles(e.dataTransfer.files, dropRole); }
  };
  box.appendChild(dz);
  box.appendChild(inp);

  // role picker: tag the upload as style or subject
  const roleRow = document.createElement('div');
  roleRow.className = 'row';
  roleRow.style.marginTop = '6px';
  roleRow.innerHTML = '<span class="faint">Tag upload as:</span>';
  const mkRole = (r, label) => {
    const b = document.createElement('button');
    b.className = 'btn sm ghost';
    b.textContent = label;
    b.onclick = () => { dropRole = r; inp.click(); };
    return b;
  };
  roleRow.appendChild(mkRole('style', 'style'));
  roleRow.appendChild(mkRole('subject', 'subject'));
  box.appendChild(roleRow);

  // current refs as thumbs with role + remove
  const refs = currentRefs();
  if (refs.length) {
    const grid = document.createElement('div');
    grid.className = 'grid';
    grid.style.marginTop = '10px';
    refs.forEach((r) => {
      const t = document.createElement('div');
      t.className = 'tile';
      t.innerHTML = `<img src="${esc(r.path)}" alt="reference" style="cursor:zoom-in">`
        + `<div class="row" style="padding:6px;justify-content:space-between">`
        + `<span class="chip">${esc(r.role || 'ref')}</span>`
        + `<a href="#" data-rm="1">remove</a></div>`;
      const img = t.querySelector('img'); if (img) { img.onclick = () => showModal(r.path); }
      const rm = t.querySelector('[data-rm]'); if (rm) { rm.onclick = (e) => { e.preventDefault(); removeRef(r.path); }; }
      grid.appendChild(t);
    });
    box.appendChild(grid);
  } else {
    box.appendChild(Object.assign(document.createElement('div'), { className: 'faint', textContent: 'No references yet. Add style or subject images to guide blends.' }));
  }
  return box;
}

// ---- LoRA picker: apply downloaded LoRAs to this hero's gens ----
function currentLoras() {
  const b = (data && data.brief) || {};
  return Array.isArray(b.loras) ? b.loras : [];
}
function saveLoras(loras) {
  return api.setLoras(state.current, loras)
    .then(() => { toast('LoRAs updated'); return load(); })
    .catch((e) => toast('Could not save LoRAs: ' + e.message, 'bad'));
}
function lorasBlock() {
  const box = document.createElement('div'); box.className = 'card'; box.style.marginTop = '10px';
  box.appendChild(Object.assign(document.createElement('div'), { className: 'h', textContent: 'LoRAs (optional style / character models)' }));
  const list = document.createElement('div'); box.appendChild(list);
  const loras = currentLoras();
  if (loras.length) {
    loras.forEach((l, i) => {
      const row = document.createElement('div'); row.className = 'row'; row.style.gap = '8px'; row.style.marginTop = '4px';
      row.innerHTML = `<span class="chip">${esc(l.name)}</span><span class="faint">weight ${esc(String(l.weight))}</span>`;
      const rm = document.createElement('button'); rm.className = 'btn sm bad'; rm.textContent = 'remove';
      rm.onclick = () => saveLoras(currentLoras().filter((_, j) => j !== i));
      row.appendChild(rm); list.appendChild(row);
    });
  } else {
    list.innerHTML = '<div class="faint">None. Add a downloaded LoRA to steer this hero.</div>';
  }
  const add = document.createElement('div'); add.className = 'row'; add.style.marginTop = '8px';
  const sel = document.createElement('select'); sel.innerHTML = '<option value="">loading installed...</option>'; sel.style.flex = '1';
  const wt = document.createElement('input'); wt.type = 'number'; wt.step = '0.1'; wt.value = '0.8'; wt.style.width = '72px'; wt.title = 'weight';
  const btn = document.createElement('button'); btn.className = 'btn sm'; btn.textContent = 'Add LoRA';
  add.appendChild(sel); add.appendChild(wt); add.appendChild(btn); box.appendChild(add);
  api.modelsInstalled().then((r) => {
    const ls = (r && r.loras) || [];
    sel.innerHTML = ls.length ? ls.map((n) => `<option>${esc(n)}</option>`).join('')
      : '<option value="">no LoRAs installed (get some in the Model Manager)</option>';
  }).catch(() => { sel.innerHTML = '<option value="">Model Manager unavailable</option>'; });
  btn.onclick = () => {
    const name = sel.value;
    if (!name) { toast('Pick a LoRA, or download one in the Model Manager'); return; }
    saveLoras(currentLoras().concat([{ name, weight: parseFloat(wt.value) || 0.8 }]));
  };
  return box;
}

// ---- brief block: readable chips + full-history <details> ----
function briefBlock() {
  const brief = (data && (data.brief || data.vector)) || {};
  const b = typeof brief === 'object' ? brief : {};
  const prompt = b.prompt || (typeof brief === 'string' ? brief : '') || '';
  const negative = b.negative || '';
  const category = (data && data.category) || state.currentCategory || '';
  const ip = b.ip_weight != null ? b.ip_weight : (b.ip_adapter_weight != null ? b.ip_adapter_weight : null);

  const box = document.createElement('div');
  box.style.marginTop = '12px';
  box.appendChild(Object.assign(document.createElement('div'), { className: 'h', textContent: 'Brief' }));

  const p = document.createElement('div');
  p.className = 'muted';
  p.textContent = prompt || 'No prompt yet. Talk to this hero to shape it.';
  box.appendChild(p);

  const chips = document.createElement('div');
  chips.className = 'row';
  chips.style.marginTop = '8px';
  const chip = (label, val) => `<span class="chip">${esc(label)}: ${esc(val)}</span>`;
  const parts = [];
  if (negative) { parts.push(chip('Negative', negative)); }
  parts.push(chip('Category', category || 'Uncategorized'));
  if (ip != null) { parts.push(chip('IP weight', ip)); }
  chips.innerHTML = parts.join('');
  box.appendChild(chips);

  // full brief + chat log + versions
  const det = document.createElement('details');
  det.style.marginTop = '8px';
  const sum = document.createElement('summary');
  sum.className = 'faint';
  sum.style.cursor = 'pointer';
  sum.textContent = 'Show full brief and history';
  det.appendChild(sum);

  const chatLog = (data && (data.chat || data.history || data.log)) || [];
  if (Array.isArray(chatLog) && chatLog.length) {
    const log = document.createElement('div');
    log.className = 'col';
    log.style.cssText = 'margin-top:8px;gap:6px;max-height:220px;overflow:auto';
    chatLog.forEach((m) => {
      const row = document.createElement('div');
      const who = esc(m.role || m.who || 'note');
      row.className = 'card';
      row.style.cssText = 'padding:8px 10px;font-size:13px';
      row.innerHTML = `<span class="faint">${who}</span> ${esc(m.text || m.message || m.content || '')}`;
      log.appendChild(row);
    });
    det.appendChild(log);
  }

  const versions = (data && data.versions) || [];
  if (Array.isArray(versions) && versions.length) {
    const vh = document.createElement('div');
    vh.className = 'h';
    vh.style.marginTop = '8px';
    vh.textContent = 'Versions';
    det.appendChild(vh);
    const vrow = document.createElement('div');
    vrow.className = 'row';
    vrow.innerHTML = versions.map((v) => `<span class="chip">${esc(typeof v === 'object' ? (v.label || v.id || v.version) : v)}</span>`).join('');
    det.appendChild(vrow);
  }

  const pre = document.createElement('pre');
  pre.className = 'faint';
  pre.style.cssText = 'white-space:pre-wrap;background:var(--panel2);padding:10px;border-radius:8px;max-height:220px;overflow:auto;margin-top:8px';
  pre.textContent = JSON.stringify((data && (data.vector || data.brief)) || {}, null, 2);
  det.appendChild(pre);

  box.appendChild(det);
  return box;
}

// ---- candidate gallery ----
function galleryBlock() {
  const gv = document.createElement('div');
  gv.className = 'card';
  gv.style.marginTop = '16px';
  const cands = (data && data.candidates) || [];
  const head = document.createElement('div');
  head.className = 'row';
  head.innerHTML = `<div class="h" style="margin:0">Candidates</div>`
    + `<span class="faint" style="margin-left:auto">${cands.length} image${cands.length === 1 ? '' : 's'}. Click to enlarge, Pick a winner.</span>`;
  gv.appendChild(head);

  if (!cands.length) {
    const empty = document.createElement('div');
    empty.className = 'card';
    empty.style.cssText = 'text-align:center;padding:24px;background:var(--panel2);margin-top:10px';
    empty.innerHTML = `<div class="faint">No candidates yet. Press Generate to make some.</div>`;
    const g = document.createElement('button'); g.className = 'btn primary'; g.style.marginTop = '10px'; g.textContent = 'Generate now';
    g.onclick = doGen;
    empty.appendChild(g);
    gv.appendChild(empty);
    return gv;
  }

  const grid = document.createElement('div');
  grid.className = 'grid';
  grid.style.marginTop = '10px';
  cands.forEach((url, i) => {
    const win = isWinner(url, i);
    const t = document.createElement('div');
    t.className = 'tile' + (win ? '' : '') + (selCandidate === i ? ' sel' : '');
    if (win) { t.style.outline = '2px solid var(--sun)'; }

    const img = document.createElement('img');
    img.src = url; img.alt = 'candidate ' + (i + 1); img.style.cursor = 'zoom-in';
    img.onclick = () => { selCandidate = i; showModal(url); };
    t.appendChild(img);

    if (win) {
      const badge = document.createElement('span');
      badge.className = 'chip';
      badge.style.cssText = 'position:absolute;top:6px;left:6px;background:var(--sun);color:#241400;font-weight:700;border:none';
      badge.textContent = 'winner';
      t.appendChild(badge);
    }

    const ov = document.createElement('div');
    ov.className = 'row';
    ov.style.cssText = 'padding:6px;gap:6px';
    const pickBtn = document.createElement('button');
    pickBtn.className = 'btn sm' + (win ? ' ghost' : '');
    pickBtn.textContent = win ? 'Chosen' : 'Pick';
    pickBtn.onclick = (e) => { e.stopPropagation(); doPick(url, i); };
    ov.appendChild(pickBtn);
    t.appendChild(ov);

    grid.appendChild(t);
  });
  gv.appendChild(grid);

  // actions row for the chosen image
  if (data && data.chosen != null) {
    const actions = document.createElement('div');
    actions.className = 'row';
    actions.style.marginTop = '12px';
    const chosen = chosenUrl();
    const chosenIdx = cands.indexOf(chosen);

    const more = document.createElement('button');
    more.className = 'btn';
    more.textContent = 'More like this';
    more.onclick = () => api.moreLike(state.current, chosen != null ? chosen : chosenIdx)
      .then(() => toast('Making more like this'))
      .then(() => load())
      .catch((e) => toast('Could not run: ' + e.message, 'bad'));

    const poses = document.createElement('button');
    poses.className = 'btn';
    poses.textContent = 'Poses';
    poses.onclick = () => {
      const raw = prompt('List poses, comma-separated (e.g. idle, walk, attack, hurt):', 'idle, walk, attack, hurt');
      if (!raw) { return; }
      const list = raw.split(',').map((s) => s.trim()).filter(Boolean);
      if (!list.length) { return; }
      api.poses(state.current, list)
        .then(() => toast('Poses queued'))
        .catch((e) => toast('Could not queue poses: ' + e.message, 'bad'));
    };

    const post = document.createElement('button');
    post.className = 'btn ghost';
    post.textContent = 'Post-process';
    post.onclick = () => emit('open', 'postprocess');

    actions.appendChild(more);
    actions.appendChild(poses);
    actions.appendChild(post);
    gv.appendChild(actions);
  }
  return gv;
}

function doGen() {
  api.gen(state.current)
    .then(() => toast('Generation queued'))
    .catch((e) => toast('Could not generate: ' + e.message, 'bad'));
}

function doPick(url, i) {
  api.pick(state.current, url != null ? url : i)
    .then(() => { toast('Winner picked'); return load(); })
    .then(() => refreshState())
    .catch((e) => toast('Could not pick: ' + e.message, 'bad'));
}

// ---- top-level render ----
function render() {
  if (state.view !== 'asset') { return; }
  const m = el();
  if (!m) { return; }
  const name = state.current;

  if (!name) {
    m.innerHTML = `<div class="card"><div class="faint">No hero selected.</div>`
      + `<button class="btn" id="ab" style="margin-top:10px">Back to heroes</button></div>`;
    const b = m.querySelector('#ab'); if (b) { b.onclick = () => setView('home'); }
    return;
  }

  if (loading && !data) {
    m.innerHTML = `<div class="card"><span class="spinner"></span> Loading ${esc(name)}...</div>`;
    return;
  }

  if (errMsg && !data) {
    m.innerHTML = `<div class="card"><div class="h" style="color:var(--bad)">Could not load ${esc(name)}</div>`
      + `<div class="faint">${esc(errMsg)}</div>`
      + `<div class="row" style="margin-top:10px"><button class="btn" id="aretry">Retry</button>`
      + `<button class="btn ghost" id="aback">Back</button></div></div>`;
    const r = m.querySelector('#aretry'); if (r) { r.onclick = load; }
    const bk = m.querySelector('#aback'); if (bk) { bk.onclick = () => setView('home'); }
    return;
  }

  m.innerHTML = '';
  const meta = (state.assets || []).find((a) => a.name === name) || {};
  const category = meta.category || (data && data.category) || '';

  // header card
  const head = document.createElement('div');
  head.className = 'card';
  const hrow = document.createElement('div');
  hrow.className = 'row';
  const back = document.createElement('button');
  back.className = 'btn sm ghost'; back.textContent = 'Back';
  back.onclick = () => setView('home');
  const title = document.createElement('h1');
  title.style.cssText = 'margin:0;font-size:22px;flex:1';
  title.textContent = name;
  const ver = document.createElement('span');
  ver.className = 'chip';
  ver.textContent = 'v' + ((data && data.latestVersion) || 1);
  hrow.appendChild(back); hrow.appendChild(title); hrow.appendChild(ver);
  head.appendChild(hrow);

  // header actions: Generate + category select
  const actions = document.createElement('div');
  actions.className = 'row';
  actions.style.marginTop = '12px';
  const genBtn = document.createElement('button');
  genBtn.className = 'btn primary'; genBtn.textContent = 'Generate';
  genBtn.onclick = doGen;
  actions.appendChild(genBtn);
  const catWrap = document.createElement('label');
  catWrap.className = 'muted';
  catWrap.style.cssText = 'display:flex;gap:8px;align-items:center;font-size:13px';
  catWrap.appendChild(document.createTextNode('Category'));
  catWrap.appendChild(buildCategorySelect(category));
  actions.appendChild(catWrap);
  head.appendChild(actions);

  head.appendChild(briefBlock());
  head.appendChild(refsBlock());
  head.appendChild(lorasBlock());
  m.appendChild(head);

  m.appendChild(galleryBlock());
}

export function mount() {
  // own #main whenever the view becomes 'asset'
  on('view', (v) => { if (v === 'asset') { render(); } });
  // a new selection resets local view state and refetches
  on('select', (name) => {
    if (!name) { return; }
    selCandidate = null; pendingRefs = []; data = null; errMsg = '';
    load();
  });
  // live: new candidates appear when a job for this asset finishes
  on('jobdone', (j) => {
    if (state.view === 'asset' && j && j.asset === state.current) { load(); }
  });
  // if we mount while already on an asset (e.g. deep entry), fetch it
  if (state.view === 'asset' && state.current) { load(); }
}
