// rail.js - the left column: New Hero, the category tree (collapsible, nested),
// the assets living under each category, and per-node controls (add sub, edit
// settings, delete). Owns #rail. Renders on 'state'; every mutation calls
// refreshState() so the tree + assets refresh from the server.
import { api } from './api.js';
import { state, on, toast, refreshState, selectAsset, selectCategory, askModal } from './state.js';

const el = () => document.getElementById('rail');
const collapsed = {}; // path -> true when a node is folded shut

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

// ---- modal (settings editor) --------------------------------------------
function closeModal() {
  const m = document.getElementById('modal');
  if (!m) { return; }
  m.classList.remove('on');
  m.innerHTML = '';
  document.removeEventListener('keydown', onEsc);
}
function onEsc(e) { if (e.key === 'Escape') { closeModal(); } }
function openModal(title, bodyHtml, wire) {
  const m = document.getElementById('modal');
  if (!m) { return; }
  m.innerHTML = `<div class="box"><div class="hd">${esc(title)}<span style="flex:1"></span>` +
    `<button class="btn sm ghost" data-x>&#10005;</button></div>` +
    `<div class="bd">${bodyHtml}</div></div>`;
  m.classList.add('on');
  m.querySelector('[data-x]').onclick = closeModal;
  document.addEventListener('keydown', onEsc);
  if (wire) { wire(m); }
}

function editCategory(node) {
  const s = node.settings || {};
  const val = (v) => esc(v == null ? '' : v);
  openModal('Edit category: ' + node.path, `
    <div class="col" style="gap:12px">
      <div class="faint">Everything inside this category inherits these settings unless it overrides them.</div>
      <label class="col" style="gap:4px"><span class="h">Style prompt</span>
        <textarea data-f="style_prompt" placeholder="e.g. gritty low-poly, muted palette">${val(s.style_prompt)}</textarea></label>
      <label class="col" style="gap:4px"><span class="h">Negative</span>
        <textarea data-f="negative" placeholder="things to avoid">${val(s.negative)}</textarea></label>
      <label class="col" style="gap:4px"><span class="h">Reference set</span>
        <input data-f="reference_set" value="${val(s.reference_set)}" placeholder="name of a reference set"></label>
      <div class="faint">Drop style images in references/${esc(node.path)}/ to teach this category its look.</div>
      <label class="col" style="gap:4px"><span class="h">IP adapter weight</span>
        <input data-f="ip_adapter_weight" type="number" step="0.05" min="0" max="1" value="${val(s.ip_adapter_weight)}" placeholder="0.5"></label>
      <label class="col" style="gap:4px"><span class="h">Note</span>
        <textarea data-f="note" placeholder="reminder for yourself">${val(s.note)}</textarea></label>
      <div class="row" style="gap:8px;justify-content:flex-end">
        <button class="btn ghost" data-cancel>Cancel</button>
        <button class="btn primary" data-save>Save</button>
      </div>
    </div>`, (m) => {
    m.querySelector('[data-cancel]').onclick = closeModal;
    m.querySelector('[data-save]').onclick = () => {
      const g = (f) => { const e = m.querySelector(`[data-f="${f}"]`); return e ? e.value.trim() : ''; };
      const w = g('ip_adapter_weight');
      const settings = {
        style_prompt: g('style_prompt'),
        negative: g('negative'),
        reference_set: g('reference_set'),
        ip_adapter_weight: w === '' ? null : Number(w),
        note: g('note'),
      };
      api.catUpdate(node.path, settings)
        .then(() => { closeModal(); toast('Category saved'); return refreshState(); })
        .catch((e) => toast('Could not save: ' + e.message, 'bad'));
    };
  });
}

function deleteCategory(node) {
  openModal('Delete category: ' + node.path, `
    <div class="col" style="gap:12px">
      <div>Delete <b>${esc(node.path)}</b>. What should happen to what is inside it?</div>
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <button class="btn" data-keep>Keep contents (move up)</button>
        <button class="btn bad" data-nuke>Delete everything inside</button>
      </div>
      <div class="row" style="justify-content:flex-end"><button class="btn ghost" data-cancel>Cancel</button></div>
    </div>`, (m) => {
    m.querySelector('[data-cancel]').onclick = closeModal;
    const del = (cascade) => api.catDelete(node.path, cascade)
      .then(() => { closeModal(); toast('Category deleted'); return refreshState(); })
      .catch((e) => toast('Could not delete: ' + e.message, 'bad'));
    m.querySelector('[data-keep]').onclick = () => del(false);
    m.querySelector('[data-nuke]').onclick = () => del(true);
  });
}

// ---- mutations ----------------------------------------------------------
async function addSub(parentPath) {
  const vals = await askModal({
    title: 'New category', submitLabel: 'Add category',
    fields: [{ label: 'Name of the new category', placeholder: 'e.g. Frost, Enemies, UI', required: true }],
  });
  if (!vals || !vals[0]) { return; }
  const path = (parentPath ? parentPath + '/' : '') + vals[0];
  api.catNew(path, parentPath || null)
    .then(() => { toast('Category added'); return refreshState(); })
    .catch((e) => toast('Could not add: ' + e.message, 'bad'));
}
async function newHero() {
  const vals = await askModal({
    title: 'New Hero', submitLabel: 'Create hero',
    fields: [
      { label: 'What is this hero called?', placeholder: 'e.g. Frost Knight', required: true },
      { label: 'Describe it in plain words', placeholder: 'a cute but evil frost warlock, glowing eyes', multiline: true },
    ],
  });
  if (!vals || !vals[0]) { return; }
  const cat = state.currentCategory || '';
  api.newHero(vals[0], vals[1], cat)
    .then(() => refreshState())
    .then(() => selectAsset(vals[0]))
    .catch((e) => toast(/exists/i.test(e.message || '') ? 'A hero with that name already exists.' : ('Could not create: ' + e.message), 'bad'));
}

// ---- rendering ----------------------------------------------------------
function assetTile(a) {
  const t = document.createElement('button');
  t.className = 'row asset';
  t.style.cssText = 'gap:8px;width:100%;text-align:left;padding:5px 6px;background:transparent;border:1px solid transparent;border-radius:8px;align-items:center';
  const thumb = a.thumb
    ? `<img src="${esc(a.thumb)}" alt="" style="width:28px;height:28px;border-radius:6px;object-fit:cover;background:#0a0c11">`
    : `<span style="width:28px;height:28px;border-radius:6px;display:grid;place-items:center;background:var(--panel2)" class="faint">${a.candidateCount || 0}</span>`;
  t.innerHTML = `${thumb}<span style="flex:1;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.name)}</span>` +
    `<span class="chip" title="candidates">${a.candidateCount || 0}</span>`;
  if (state.current === a.name) { t.style.background = 'var(--panel2)'; }
  t.onclick = () => selectAsset(a.name);
  return t;
}

function nodeEl(node, depth) {
  const wrap = document.createElement('div');
  wrap.className = 'catnode';
  const isCollapsed = !!collapsed[node.path];
  const kids = node.children || [];
  const assets = (state.assets || []).filter((a) => a.category === node.path);
  const hasChildren = kids.length > 0 || assets.length > 0;

  const row = document.createElement('div');
  row.className = 'row catrow';
  row.tabIndex = 0;
  row.style.cssText = 'gap:4px;align-items:center;padding:4px 4px;border-radius:8px;cursor:pointer;' +
    'padding-left:' + (4 + depth * 12) + 'px';
  if (state.currentCategory === node.path) { row.style.background = 'var(--panel2)'; }

  const caret = hasChildren ? (isCollapsed ? '&#9656;' : '&#9662;') : '&#8226;';
  row.innerHTML =
    `<span class="caret" style="width:14px;text-align:center;color:var(--faint)">${caret}</span>` +
    `<span style="flex:1;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(node.name || node.path)}</span>` +
    `<span class="ctl" style="opacity:0;display:flex;gap:2px">` +
    `<button class="btn sm ghost" data-add title="Add subcategory">+</button>` +
    `<button class="btn sm ghost" data-edit title="Edit settings">&#9998;</button>` +
    `<button class="btn sm ghost" data-del title="Delete">&#128465;</button>` +
    `</span>`;

  const ctl = row.querySelector('.ctl');
  const showCtl = () => { ctl.style.opacity = '1'; };
  const hideCtl = () => { ctl.style.opacity = '0'; };
  row.addEventListener('mouseenter', showCtl);
  row.addEventListener('mouseleave', hideCtl);
  row.addEventListener('focus', showCtl);
  row.addEventListener('blur', hideCtl);

  // caret toggles fold; the rest of the row selects the category
  row.querySelector('.caret').onclick = (e) => {
    e.stopPropagation();
    collapsed[node.path] = !collapsed[node.path];
    render();
  };
  row.onclick = () => selectCategory(node.path);
  const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
  row.querySelector('[data-add]').onclick = stop(() => addSub(node.path));
  row.querySelector('[data-edit]').onclick = stop(() => editCategory(node));
  row.querySelector('[data-del]').onclick = stop(() => deleteCategory(node));
  wrap.appendChild(row);

  if (!isCollapsed) {
    const body = document.createElement('div');
    body.className = 'col';
    body.style.cssText = 'gap:1px;padding-left:' + (10 + depth * 12) + 'px';
    kids.forEach((c) => body.appendChild(nodeEl(c, depth + 1)));
    assets.forEach((a) => body.appendChild(assetTile(a)));
    wrap.appendChild(body);
  }
  return wrap;
}

function render() {
  const r = el();
  if (!r) { return; }
  const tree = state.tree || [];
  const assets = state.assets || [];
  const knownPaths = new Set();
  (function walk(ns) { ns.forEach((n) => { knownPaths.add(n.path); walk(n.children || []); }); })(tree);
  const orphans = assets.filter((a) => !a.category || !knownPaths.has(a.category));

  r.innerHTML = `
    <div class="col" style="gap:10px">
      <button class="btn primary" id="rail-newhero">&#127917; New hero</button>
      <div class="row" style="gap:6px;align-items:center">
        <span class="h" style="flex:1;margin:0">Categories</span>
        <button class="btn sm ghost" id="rail-newcat" title="New top-level category">+ Category</button>
      </div>
      <div id="rail-tree" class="col" style="gap:1px"></div>
    </div>`;

  r.querySelector('#rail-newhero').onclick = newHero;
  r.querySelector('#rail-newcat').onclick = () => addSub(null);

  const host = r.querySelector('#rail-tree');
  if (!tree.length && !orphans.length) {
    host.innerHTML = '<div class="faint">No categories yet. Add one to organise your heroes.</div>';
  }
  tree.forEach((n) => host.appendChild(nodeEl(n, 0)));

  if (orphans.length) {
    const grp = document.createElement('div');
    grp.className = 'catnode';
    grp.innerHTML = '<div class="row" style="gap:4px;padding:4px"><span style="width:14px;text-align:center;color:var(--faint)">&#8226;</span><span class="h" style="margin:0;flex:1">Uncategorized</span></div>';
    const body = document.createElement('div');
    body.className = 'col';
    body.style.cssText = 'gap:1px;padding-left:10px';
    orphans.forEach((a) => body.appendChild(assetTile(a)));
    grp.appendChild(body);
    host.appendChild(grp);
  }
}

export function mount() {
  on('state', render);
  on('select', render);
  on('selectCat', render);
  render();
}
