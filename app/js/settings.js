// settings.js - the Settings slide-over (server + client prefs) and the Doctor
// health banner. Owns #settings, #btnSettings, and #doctor. Nothing else talks
// to these; other modules just emit('open','settings') or click #btnSettings.
import { api } from './api.js';
import { state, on, emit, toast, setView } from './state.js';

const PREF_KEY = 'dc_prefs';
const DEFAULT_PREFS = { theme: 'dark', motion: 'full', confirmDelete: true, notifyDone: true };

// ----- client prefs (localStorage) -------------------------------------------
export function loadPrefs() {
  try { return Object.assign({}, DEFAULT_PREFS, JSON.parse(localStorage.getItem(PREF_KEY) || '{}')); }
  catch (_) { return Object.assign({}, DEFAULT_PREFS); }
}
function savePrefs(p) { try { localStorage.setItem(PREF_KEY, JSON.stringify(p)); } catch (_) { /* ignore */ } }

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'system') {
    const dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    root.setAttribute('data-theme', dark ? 'dark' : 'light');
  } else {
    root.setAttribute('data-theme', theme);
  }
}
function applyMotion(motion) {
  document.documentElement.setAttribute('data-motion', motion === 'reduce' ? 'reduce' : 'full');
}
// react to the OS theme flipping while 'system' is selected
if (window.matchMedia) {
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  const relay = () => { if (loadPrefs().theme === 'system') { applyTheme('system'); } };
  if (mq.addEventListener) { mq.addEventListener('change', relay); } else if (mq.addListener) { mq.addListener(relay); }
}

// ----- server settings form --------------------------------------------------
// groups -> [dotted key, label, {type, opts?, step?, hint?}]
const GROUPS = [
  ['Generation', [
    ['gen.n_candidates', 'Candidates per batch', { type: 'number', step: '1', hint: 'How many options to make each time.' }],
    ['gen.steps', 'Steps', { type: 'number', step: '1' }],
    ['gen.cfg', 'Guidance (CFG)', { type: 'number', step: '0.1' }],
    ['gen.width', 'Width', { type: 'number', step: '8' }],
    ['gen.height', 'Height', { type: 'number', step: '8' }],
    ['gen.ip_adapter', 'Use reference images (IP-Adapter)', { type: 'bool' }],
    ['engine', 'Engine', { type: 'text' }],
    ['comfyui.checkpoint', 'Model checkpoint', { type: 'text' }],
  ]],
  ['Brain', [
    ['brain', 'Brain', { type: 'select', opts: ['ollama', 'gemini'] }],
    ['ollama_model', 'Ollama model', { type: 'text' }],
    ['gemini_model', 'Gemini model', { type: 'text' }],
  ]],
  ['Ranking', [
    ['rank.clip_model', 'CLIP model', { type: 'text' }],
  ]],
  ['Vector', [
    ['vector.colors', 'Colors', { type: 'number', step: '1', hint: 'Palette size when tracing to vector.' }],
  ]],
  ['Queue', [
    ['queue.max_retries', 'Max retries', { type: 'number', step: '1' }],
    ['queue.poll_seconds', 'Poll seconds', { type: 'number', step: '1' }],
    ['queue.restart_engine_on_fail', 'Restart engine on failure', { type: 'bool' }],
  ]],
];

const ipt = 'width:100%;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 10px;color:inherit;min-height:38px';

function fieldHtml(key, label, spec, val) {
  const id = 'set_' + key.replace(/\./g, '_');
  let control;
  if (spec.type === 'bool') {
    control = `<label class="row" style="gap:8px"><input type="checkbox" id="${id}" data-key="${key}" ${val ? 'checked' : ''}> <span>${esc(label)}</span></label>`;
    return `<div class="col" style="gap:4px">${control}${spec.hint ? `<div class="faint">${esc(spec.hint)}</div>` : ''}</div>`;
  }
  if (spec.type === 'select') {
    const opts = (spec.opts || []).map((o) => `<option value="${esc(o)}" ${String(val) === o ? 'selected' : ''}>${esc(o)}</option>`).join('');
    control = `<select id="${id}" data-key="${key}" style="${ipt}">${opts}</select>`;
  } else {
    const t = spec.type === 'number' ? 'number' : 'text';
    control = `<input type="${t}" id="${id}" data-key="${key}" ${spec.step ? `step="${spec.step}"` : ''} value="${esc(val == null ? '' : val)}" style="${ipt}">`;
  }
  return `<div class="col" style="gap:4px"><label for="${id}" class="faint">${esc(label)}</label>${control}${spec.hint ? `<div class="faint">${esc(spec.hint)}</div>` : ''}</div>`;
}

function readForm(root) {
  const out = {};
  root.querySelectorAll('[data-key]').forEach((n) => {
    const key = n.dataset.key;
    if (n.type === 'checkbox') { out[key] = n.checked; }
    else if (n.type === 'number') { const v = n.value.trim(); out[key] = v === '' ? null : Number(v); }
    else { out[key] = n.value; }
  });
  return out;
}

async function renderSettings() {
  const box = document.getElementById('settings');
  if (!box) { return; }
  const prefs = loadPrefs();
  let settings = {};
  try { settings = (await api.settings()).settings || {}; }
  catch (e) { toast('Could not load settings: ' + e.message, 'bad'); }

  const groupsHtml = GROUPS.map(([title, fields]) => `
    <div class="col" style="gap:10px">
      <div class="h">${esc(title)}</div>
      ${fields.map(([k, label, spec]) => fieldHtml(k, label, spec, settings[k])).join('')}
    </div>`).join('');

  box.innerHTML = `
    <div class="col" style="height:100%;padding:14px;gap:14px;overflow:auto">
      <div class="row" style="justify-content:space-between">
        <b style="font-size:16px">Settings</b>
        <button class="btn sm ghost" id="setClose">Close</button>
      </div>
      <div class="col" style="gap:16px">${groupsHtml}</div>
      <div class="faint">Some settings apply to the next generation, not the current one.</div>
      <div class="row"><button class="btn primary" id="setSave">Save settings</button></div>

      <div class="h">This computer</div>
      <div class="col" style="gap:12px">
        <div class="col" style="gap:4px">
          <label for="prefTheme" class="faint">Theme</label>
          <select id="prefTheme" style="${ipt}">
            <option value="dark" ${prefs.theme === 'dark' ? 'selected' : ''}>Dark</option>
            <option value="light" ${prefs.theme === 'light' ? 'selected' : ''}>Light</option>
            <option value="system" ${prefs.theme === 'system' ? 'selected' : ''}>Match system</option>
          </select>
        </div>
        <label class="row" style="gap:8px"><input type="checkbox" id="prefMotion" ${prefs.motion === 'reduce' ? 'checked' : ''}> <span>Reduce motion</span></label>
        <label class="row" style="gap:8px"><input type="checkbox" id="prefConfirm" ${prefs.confirmDelete ? 'checked' : ''}> <span>Ask before deleting</span></label>
        <label class="row" style="gap:8px"><input type="checkbox" id="prefNotify" ${prefs.notifyDone ? 'checked' : ''}> <span>Notify when a batch is done</span></label>
      </div>

      <div class="h">Help</div>
      <div class="row"><button class="btn sm" id="setTutorial">Restart tutorial</button></div>
    </div>`;

  const close = () => box.classList.remove('on');
  box.querySelector('#setClose').onclick = close;

  box.querySelector('#setSave').onclick = () => {
    api.saveSettings(readForm(box))
      .then(() => toast('Settings saved', 'good'))
      .catch((e) => toast('Save failed: ' + e.message, 'bad'));
  };

  const theme = box.querySelector('#prefTheme');
  theme.onchange = () => { const p = loadPrefs(); p.theme = theme.value; savePrefs(p); applyTheme(p.theme); };
  const motion = box.querySelector('#prefMotion');
  motion.onchange = () => { const p = loadPrefs(); p.motion = motion.checked ? 'reduce' : 'full'; savePrefs(p); applyMotion(p.motion); };
  const confirmDel = box.querySelector('#prefConfirm');
  confirmDel.onchange = () => { const p = loadPrefs(); p.confirmDelete = confirmDel.checked; savePrefs(p); };
  const notify = box.querySelector('#prefNotify');
  notify.onchange = () => { const p = loadPrefs(); p.notifyDone = notify.checked; savePrefs(p); };

  box.querySelector('#setTutorial').onclick = () => { close(); emit('open', 'tutorial'); };
}

function openSettings() {
  const box = document.getElementById('settings');
  if (!box) { return; }
  renderSettings().then(() => box.classList.add('on'));
}

// ----- doctor banner ---------------------------------------------------------
async function renderDoctor() {
  const banner = document.getElementById('doctor');
  if (!banner) { return; }
  let checks = [];
  try { checks = (await api.doctor()).checks || []; }
  catch (e) { banner.innerHTML = `<div class="check"><span class="no">&#10007;</span> <div>Could not reach the app to check its health. <button class="btn sm" id="docRecheck">Re-check</button></div></div>`;
    const rb = banner.querySelector('#docRecheck'); if (rb) { rb.onclick = renderDoctor; } return; }

  const failing = checks.filter((c) => !c.ok);
  if (!failing.length) {
    banner.innerHTML = `<span class="chip" style="color:var(--good);border-color:var(--good)">&#10003; All systems ready</span>`;
    return;
  }

  const rows = checks.map((c) => {
    if (c.ok) { return `<div class="check"><span class="ok">&#10003;</span> <div>${esc(c.name)}</div></div>`; }
    return `<div class="check"><span class="no">&#10007;</span> <div>
      <b>${esc(c.name)}</b>
      ${c.detail ? `<div class="faint">${esc(c.detail)}</div>` : ''}
      ${c.fix ? `<div style="margin-top:4px">${esc(c.fix)}</div>` : ''}
    </div></div>`;
  }).join('');

  banner.innerHTML = `
    <div class="col" style="gap:6px">
      <div class="row" style="justify-content:space-between">
        <b>Let us get you set up (${failing.length} thing${failing.length === 1 ? '' : 's'} to sort)</b>
        <button class="btn sm" id="docRecheck">Re-check</button>
      </div>
      ${rows}
      <div class="faint">No rush. Fix what you can, then press Re-check. Everything else keeps working.</div>
    </div>`;
  const rb = banner.querySelector('#docRecheck'); if (rb) { rb.onclick = renderDoctor; }
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

export function mount() {
  const btn = document.getElementById('btnSettings');
  if (btn) { btn.onclick = openSettings; }
  on('open', (what) => { if (what === 'settings') { openSettings(); } });
  renderDoctor();
}
