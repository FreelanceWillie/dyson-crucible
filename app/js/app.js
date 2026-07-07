// app.js - boot. Dynamically imports each feature module so a missing or broken
// one is skipped (never breaks the rest). Each module exports mount() which finds
// its own region by id, renders, and subscribes to state events.

import { startPolling, refreshState, on, toast } from './state.js';

// apply saved theme/motion prefs early
const prefs = JSON.parse(localStorage.getItem('dc_prefs') || '{}');
if (prefs.theme && prefs.theme !== 'system') { document.documentElement.setAttribute('data-theme', prefs.theme); }
if (prefs.motion === 'reduce') { document.documentElement.setAttribute('data-motion', 'reduce'); }

// The feature modules. Order is not important; each is independent.
const MODULES = [
  'home', 'rail', 'asset', 'chat', 'queue',
  'explore', 'taste', 'postprocess', 'models', 'webref', 'settings', 'chrome',
];

(async function boot() {
  for (const name of MODULES) {
    try {
      const mod = await import('./' + name + '.js');
      if (typeof mod.mount === 'function') { mod.mount(); }
    } catch (e) {
      console.warn('[dc] module not loaded:', name, e && e.message);
    }
  }
  await refreshState();
  startPolling();

  // when a gen/explore/post job finishes, refresh so new images appear on their own
  on('jobdone', () => { refreshState(); });
  on('offline', () => { /* modules show their own offline states */ });
})();
