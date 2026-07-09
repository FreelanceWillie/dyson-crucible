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
    ['gen.ip_adapter', 'Use your reference images', { type: 'bool', hint: 'Off = ignore reference images, draw from your words only.' }],
    ['gen.ip_adapter_weight', 'How closely to follow your reference images', { type: 'range', min: '0', max: '1', step: '0.05', hint: 'Left = follow your words more. Right = copy your reference look more. Around the middle is usually best. Applies to new heroes.' }],
    ['engine', 'Engine', { type: 'select', opts: [
      { value: 'comfyui', label: 'ComfyUI (recommended)' },
      { value: 'diffusers', label: 'Diffusers (simple fallback)' }] }],
    ['comfyui.checkpoint', 'Art style engine (model)', { type: 'dynselect', source: 'checkpoints',
      hint: 'Pick an installed model. Download more with the button below.' }],
    ['comfyui.warm_on_boot', 'Warm up the engine at startup', { type: 'bool',
      hint: 'Start ComfyUI when the app opens so your first image is instant. Reclaim machine frees the GPU anytime.' }],
  ]],
  ['Brain', [
    ['brain', 'Brain', { type: 'select', opts: [
      { value: 'local', label: 'Local (Ollama, free)' },
      { value: 'gemini_api', label: 'Google Gemini (needs a key)' },
      { value: 'claude', label: 'Claude CLI' }] }],
    ['ollama_model', 'Local model', { type: 'dynselect', source: 'ollama',
      hint: 'Models you have pulled with Ollama.' }],
    ['gemini_model', 'Gemini model', { type: 'select', opts: [
      'gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-1.5-pro'] }],
  ]],
  ['Ranking', [
    ['rank.clip_model', 'CLIP model', { type: 'select', opts: ['ViT-B-32', 'ViT-L-14', 'ViT-H-14'] }],
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

// opts entries may be plain strings or { value, label }
function optionsHtml(opts, val) {
  return (opts || []).map((o) => {
    const v = (o && typeof o === 'object') ? o.value : o;
    const lab = (o && typeof o === 'object') ? o.label : o;
    return `<option value="${esc(v)}" ${String(val) === String(v) ? 'selected' : ''}>${esc(lab)}</option>`;
  }).join('');
}

// Fill the dynamic dropdowns (installed checkpoints / pulled local models) after
// the panel renders, keeping the saved value selected even if it is not listed.
async function populateDynSelects(box) {
  const fill = (sel, names, cur) => {
    if (!sel) { return; }
    const list = names && names.length ? names.slice() : [];
    if (cur && !list.includes(cur)) { list.unshift(cur); }
    if (!list.length) { return; }  // leave the current value as-is
    sel.innerHTML = list.map((n) => `<option value="${esc(n)}" ${n === cur ? 'selected' : ''}>${esc(n)}</option>`).join('');
  };
  const ck = box.querySelector('[data-dyn="checkpoints"]');
  if (ck) {
    try { const d = await api.checkpoints(); fill(ck, d.installed || [], ck.dataset.cur || ck.value); }
    catch (_) { /* keep current */ }
  }
  const ol = box.querySelector('[data-dyn="ollama"]');
  if (ol) {
    try { const d = await api.brainModels(); fill(ol, d.models || [], ol.dataset.cur || ol.value); }
    catch (_) { /* keep current */ }
  }
}

function fieldHtml(key, label, spec, val) {
  const id = 'set_' + key.replace(/\./g, '_');
  let control;
  if (spec.type === 'bool') {
    control = `<label class="row" style="gap:8px"><input type="checkbox" id="${id}" data-key="${key}" ${val ? 'checked' : ''}> <span>${esc(label)}</span></label>`;
    return `<div class="col" style="gap:4px">${control}${spec.hint ? `<div class="faint">${esc(spec.hint)}</div>` : ''}</div>`;
  }
  if (spec.type === 'range') {
    const v = (val == null ? spec.min || '0' : val);
    control = `<div class="row" style="gap:10px;align-items:center">
      <input type="range" id="${id}" data-key="${key}" min="${spec.min || 0}" max="${spec.max || 1}" step="${spec.step || 0.05}" value="${esc(v)}" style="flex:1"
        oninput="var o=document.getElementById('${id}_val');if(o)o.textContent=this.value">
      <span id="${id}_val" class="chip" style="min-width:38px;text-align:center">${esc(v)}</span></div>`;
    return `<div class="col" style="gap:4px"><label for="${id}" class="faint">${esc(label)}</label>${control}${spec.hint ? `<div class="faint">${esc(spec.hint)}</div>` : ''}</div>`;
  }
  if (spec.type === 'select') {
    control = `<select id="${id}" data-key="${key}" style="${ipt}">${optionsHtml(spec.opts, val)}</select>`;
  } else if (spec.type === 'dynselect') {
    // Rendered with just the current value; populated after mount from the server
    // (installed checkpoints / pulled models) so it is a pick-list, not a text box.
    const cur = val == null ? '' : String(val);
    control = `<select id="${id}" data-key="${key}" data-dyn="${esc(spec.source)}" style="${ipt}">`
      + (cur ? `<option value="${esc(cur)}" selected>${esc(cur)}</option>` : '<option value="">loading...</option>')
      + '</select>';
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
    else if (n.type === 'number' || n.type === 'range') { const v = n.value.trim(); out[key] = v === '' ? null : Number(v); }
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

      <div class="h">Art style engine</div>
      <div class="row" style="justify-content:space-between;align-items:center">
        <div class="col" style="gap:2px"><span class="faint">The model that draws. Pick one that fits your subject (toon, realistic, fantasy...).</span></div>
        <button class="btn sm" id="btnCheckpoints">Choose model</button>
      </div>

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

      <div class="h">Updates</div>
      <div class="row" style="justify-content:space-between;align-items:center">
        <div class="col" style="gap:2px"><span id="verLine" class="faint">Checking version...</span></div>
        <button class="btn sm" id="btnUpdate">Check for updates</button>
      </div>

      <div class="h">Feature packs</div>
      <div class="faint">Optional extras. Download only what you want, when you want it.</div>
      <div class="col" id="featurePacks" style="gap:10px"><div class="faint">Checking...</div></div>

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
  box.querySelector('#btnCheckpoints').onclick = () => { emit('open', 'checkpoints'); };
  populateDynSelects(box);

  renderFeaturePacks(box);
  renderVersion(box);
}

// ----- updates ---------------------------------------------------------------
async function renderVersion(box) {
  const line = box.querySelector('#verLine');
  const btn = box.querySelector('#btnUpdate');
  if (!line || !btn) { return; }
  let v;
  try { v = await api.version(); } catch (_) { line.textContent = 'Version check unavailable.'; return; }
  const base = 'Version ' + (v.version || '?') + (v.date ? ' (' + v.date + ')' : '');
  if (v.update_available) {
    line.innerHTML = base + ` <span class="chip" style="color:var(--accent);border-color:var(--accent)">${v.behind} update(s) available</span>`;
    btn.textContent = 'Update now';
  } else {
    line.textContent = base + ' - up to date';
  }
  btn.onclick = async () => {
    btn.disabled = true; btn.textContent = 'Updating...';
    let r;
    try { r = await api.update(); } catch (e) { toast('Update failed: ' + e.message, 'bad'); btn.disabled = false; btn.textContent = 'Update now'; return; }
    if (r.changed) {
      line.innerHTML = `Updated ${r.from} &rarr; ${r.to}. <b>Restart the app to apply.</b>`;
      toast('Updated. Restart the app to apply.', 'good');
    } else {
      toast('Already up to date.', 'good');
      btn.disabled = false; btn.textContent = 'Check for updates';
    }
  };
}

// ----- feature packs (optional capability groups) ----------------------------
let _packPoll = null;
async function renderFeaturePacks(box) {
  const host = box.querySelector('#featurePacks');
  if (!host) { return; }
  let data;
  try { data = await api.capabilities(); }
  catch (e) { host.innerHTML = `<div class="faint">Could not check feature packs (${e.message}).</div>`; return; }
  const groups = data.groups || {};
  const prog = data.progress || {};
  host.innerHTML = Object.keys(groups).map((gid) => {
    const g = groups[gid];
    const p = prog[gid];
    let right;
    if (g.installed) {
      right = `<span class="chip" style="color:var(--good);border-color:var(--good)">&#10003; Installed</span>`;
    } else if (p && !p.done) {
      const last = (p.log && p.log.length) ? p.log[p.log.length - 1] : 'Installing...';
      right = `<span class="faint" data-prog="${gid}">${last.replace(/</g, '&lt;')}</span>`;
    } else {
      right = `<button class="btn sm" data-unlock="${gid}">Unlock</button>`;
    }
    return `<div class="row" style="justify-content:space-between;align-items:flex-start;gap:10px">
      <div class="col" style="gap:2px"><b>${g.title}</b><span class="faint" style="font-size:12px">${g.why}</span></div>
      <div>${right}</div></div>`;
  }).join('');

  host.querySelectorAll('[data-unlock]').forEach((b) => {
    b.onclick = async () => {
      const gid = b.getAttribute('data-unlock');
      b.disabled = true; b.textContent = 'Starting...';
      try { await api.installCapability(gid); toast('Downloading ' + gid + '...', 'good'); }
      catch (e) { toast('Unlock failed: ' + e.message, 'bad'); b.disabled = false; b.textContent = 'Unlock'; return; }
      pollPacks(box);
    };
  });

  // if anything is mid-install, keep polling
  const busy = Object.keys(prog).some((k) => prog[k] && !prog[k].done);
  if (busy) { pollPacks(box); }
}

function pollPacks(box) {
  if (_packPoll) { return; }
  _packPoll = setInterval(async () => {
    const live = document.getElementById('featurePacks');
    if (!live || !box.classList.contains('on')) { clearInterval(_packPoll); _packPoll = null; return; }
    let data;
    try { data = await api.capabilities(); } catch (_) { return; }
    const prog = data.progress || {};
    const busy = Object.keys(prog).some((k) => prog[k] && !prog[k].done);
    // update inline progress text
    Object.keys(prog).forEach((gid) => {
      const el = live.querySelector(`[data-prog="${gid}"]`);
      const p = prog[gid];
      if (el && p && p.log && p.log.length) { el.textContent = p.log[p.log.length - 1]; }
    });
    if (!busy) { clearInterval(_packPoll); _packPoll = null; renderFeaturePacks(box); }
  }, 1500);
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

  const actionFor = (name) => {
    const n = (name || '').toLowerCase();
    if (n.includes('comfyui')) { return `<button class="btn sm" data-act="comfyui">Start ComfyUI</button>`; }
    if (n.includes('brain') || n.includes('ollama')) { return `<button class="btn sm" data-act="ollama">Start Ollama</button>`; }
    return '';
  };
  const rows = checks.map((c) => {
    if (c.ok) { return `<div class="check"><span class="ok">&#10003;</span> <div>${esc(c.name)}</div></div>`; }
    return `<div class="check"><span class="no">&#10007;</span> <div>
      <b>${esc(c.name)}</b>
      ${c.detail ? `<div class="faint">${esc(c.detail)}</div>` : ''}
      ${c.fix ? `<div style="margin-top:4px">${esc(c.fix)}</div>` : ''}
      ${actionFor(c.name) ? `<div style="margin-top:6px">${actionFor(c.name)}</div>` : ''}
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
  banner.querySelectorAll('[data-act]').forEach((b) => {
    b.onclick = async () => {
      b.disabled = true; b.textContent = 'Working...';
      try {
        const r = b.dataset.act === 'comfyui' ? await api.startComfyui() : await api.startOllama();
        toast(r.detail || 'Done');
        setTimeout(renderDoctor, 4000); // give it a moment to come up, then re-check
      } catch (e) { toast('Could not start it: ' + e.message, 'bad'); b.disabled = false; b.textContent = 'Retry'; }
    };
  });
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

let _lastEngine = null;
export function mount() {
  const btn = document.getElementById('btnSettings');
  if (btn) { btn.onclick = openSettings; }
  on('open', (what) => { if (what === 'settings') { openSettings(); } });
  renderDoctor();
  // Live-refresh the Doctor when the engine state changes (e.g. warming -> ready),
  // so the "am I stuck?" banner reflects reality without opening Settings.
  on('resources', (r) => {
    const st = r && r.engine && r.engine.state;
    if (st !== _lastEngine) { _lastEngine = st; renderDoctor(); }
  });
}
