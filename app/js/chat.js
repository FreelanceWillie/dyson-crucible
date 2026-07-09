// chat.js - the right-column two-scope chat. Owns #chatpanel.
// Scope is 'asset' when an asset is selected, else 'global' (the control panel).
// The user talks; the server replies and may kick off an explore/refine/generate.
import { api } from './api.js';
import { state, on, emit, toast, refreshState, setView } from './state.js';

const el = () => document.getElementById('chatpanel');

// in-memory copy of the current conversation, kept in sync with the server log
let log = [];
let autoGen = true;
let busy = false;

function scope() { return state.current ? 'asset' : 'global'; }

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// turn a server chat row into { role, text }; tolerate a few shapes
function normalize(row) {
  if (!row) { return null; }
  const role = row.role || (row.user ? 'user' : row.assistant ? 'assistant' : 'assistant');
  const text = row.text != null ? row.text
    : row.message != null ? row.message
    : row.content != null ? row.content
    : row.user != null ? row.user
    : row.assistant != null ? row.assistant : '';
  if (text === '' && !row.role) { return null; }
  return { role: role === 'user' ? 'user' : 'assistant', text: String(text) };
}

function bubble(m) {
  const mine = m.role === 'user';
  const align = mine ? 'flex-end' : 'flex-start';
  const bg = mine ? 'var(--sun)' : 'var(--panel2)';
  const fg = mine ? '#241400' : 'var(--text, inherit)';
  return `<div style="display:flex;justify-content:${align}">
    <div style="max-width:82%;padding:8px 11px;border-radius:12px;background:${bg};color:${fg};
      border:1px solid var(--line);white-space:pre-wrap;word-break:break-word;font-size:14px;line-height:1.4">
      ${esc(m.text)}</div></div>`;
}

function render() {
  const p = el(); if (!p) { return; }
  const asset = state.current;
  const head = asset
    ? `Chat: ${esc(asset)}`
    : 'Control panel';
  const hint = asset
    ? `Notes here shape this hero. Ask for changes or new takes.`
    : `Command anything, or ask how-to questions (FAQ). Try "make a frost mage" or "how do I find a style".`;
  const body = log.length
    ? log.map(bubble).join('')
    : `<div class="faint" style="margin:auto 0">No messages yet. Say what you want below.</div>`;

  p.innerHTML = `
    <div class="row" style="justify-content:space-between;margin-bottom:2px;align-items:center">
      <b style="font-size:14px">${head}</b>
      <span class="row" style="gap:6px;align-items:center">
        <span class="chip">${asset ? 'asset' : 'global'}</span>
        ${log.length ? '<button class="btn sm ghost" id="chatclear" title="Clear this conversation">Clear</button>' : ''}
      </span>
    </div>
    <div class="faint" style="margin-bottom:8px">${hint}</div>
    <div id="chatlog" style="flex:1 1 auto;overflow:auto;display:flex;flex-direction:column;gap:8px;
      padding:2px 2px 8px">${body}</div>
    <div class="col" style="gap:6px;margin-top:8px">
      <label class="check" style="font-size:13px">
        <input type="checkbox" id="chatgen" ${autoGen ? 'checked' : ''}>
        <span>Auto-generate after each note</span>
      </label>
      <div class="row" style="gap:6px;align-items:flex-end">
        <textarea id="chatinput" rows="2" placeholder="Type a note or question. Enter to send, Shift+Enter for a new line."
          style="flex:1 1 auto;resize:vertical;min-height:40px;max-height:160px;padding:8px 10px;
          border:1px solid var(--line);border-radius:9px;background:var(--panel2);color:inherit;
          font:inherit"></textarea>
        <button class="btn primary" id="chatsend" ${busy ? 'disabled' : ''}>Send</button>
      </div>
    </div>`;

  const clr = p.querySelector('#chatclear');
  if (clr) { clr.onclick = () => clearChat(); }
  const gen = p.querySelector('#chatgen');
  if (gen) { gen.onchange = () => { autoGen = gen.checked; }; }
  const ta = p.querySelector('#chatinput');
  const send = p.querySelector('#chatsend');
  if (send && ta) { send.onclick = () => submit(ta.value); }
  if (ta) {
    ta.onkeydown = (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(ta.value); }
    };
    ta.focus();
  }
  scrollDown();
}

function scrollDown() {
  const l = document.getElementById('chatlog');
  if (l) { l.scrollTop = l.scrollHeight; }
}

// append a message to the visible log without a full re-render (keeps input focus)
function append(m) {
  if (!m || m.text === '') { return; }
  log.push(m);
  const l = document.getElementById('chatlog');
  if (l) {
    // clear the "no messages" placeholder on first real message
    if (log.length === 1) { l.innerHTML = ''; }
    l.insertAdjacentHTML('beforeend', bubble(m));
    scrollDown();
  } else {
    render();
  }
}

async function loadHistory() {
  const sc = scope();
  const name = state.current || '';
  try {
    const res = await api.chatHistory(sc, name);
    const rows = (res && (res.history || res.messages || res.log)) || [];
    log = rows.map(normalize).filter(Boolean);
  } catch (_) {
    // no history endpoint data; start empty rather than erroring the panel
    log = [];
  }
  render();
}

async function clearChat() {
  const sc = scope();
  try {
    await api.chatClear(sc, state.current);
    log = [];
    render();
    toast('Chat cleared');
  } catch (e) {
    toast('Could not clear: ' + (e && e.message ? e.message : 'error'), 'bad');
  }
}

async function submit(raw) {
  const message = (raw || '').trim();
  if (!message || busy) { return; }
  const sc = scope();
  busy = true;

  // optimistic echo
  append({ role: 'user', text: message });
  const ta = document.getElementById('chatinput');
  if (ta) { ta.value = ''; }
  const send = document.getElementById('chatsend');
  if (send) { send.disabled = true; }

  const context = { asset: state.current, category: state.currentCategory, scope: sc };
  try {
    const res = await api.command(message, context, autoGen) || {};

    // surface the assistant's words: prefer reply, fall back to a summary
    const reply = res.reply || res.summary || res.message || '';

    if (res.directions || (res.job && res.kind === 'explore') || res.kind === 'new_category') {
      // an explore / new-category: a mood board is being built
      toast('Building a mood board');
      if (reply) { append({ role: 'assistant', text: reply }); }
      setView('explore', res);
    } else if (res.brief) {
      // a refine: brief updated on the asset
      toast('Updated the brief');
      if (reply) { append({ role: 'assistant', text: reply }); }
      await refreshState();
    } else if (res.job) {
      // a plain generate
      toast('Generating');
      append({ role: 'assistant', text: reply || 'Generating new candidates.' });
    } else {
      // a pure reply / FAQ answer
      append({ role: 'assistant', text: reply || 'Done.' });
    }
  } catch (e) {
    append({ role: 'assistant', text: 'Could not do that: ' + (e && e.message ? e.message : 'error') });
    toast('Command failed: ' + (e && e.message ? e.message : 'error'), 'bad');
  } finally {
    busy = false;
    const s = document.getElementById('chatsend');
    if (s) { s.disabled = false; }
    // reload so persisted history (server-side) stays the source of truth
    await loadHistory();
  }
}

export function mount() {
  on('select', () => loadHistory());
  on('view', () => loadHistory());
  loadHistory();
}
