// api.js - every server endpoint, wrapped. This is the contract every UI module
// uses. Same-origin fetch; each call returns parsed JSON or throws with a message.

async function req(method, path, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  let r;
  try {
    r = await fetch(path, opt);
  } catch (e) {
    throw new Error('offline');
  }
  let data = null;
  const txt = await r.text();
  try { data = txt ? JSON.parse(txt) : {}; } catch (_) { data = { raw: txt }; }
  if (!r.ok || (data && data.error)) {
    throw new Error((data && data.error) || ('HTTP ' + r.status));
  }
  return data;
}
const GET = (p) => req('GET', p);
const POST = (p, b) => req('POST', p, b);
const qs = (o) => Object.entries(o || {}).filter(([, v]) => v != null && v !== '')
  .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');

export const api = {
  // reads
  state: () => GET('/api/state'),
  asset: (name) => GET('/api/asset?' + qs({ name })),
  queue: () => GET('/api/queue'),
  resources: () => GET('/api/resources'),
  settings: () => GET('/api/settings'),
  doctor: () => GET('/api/doctor'),
  categories: () => GET('/api/categories'),
  moodboard: (name) => GET('/api/moodboard?' + qs({ name })),
  taste: (id) => GET('/api/taste?' + qs({ id })),
  presets: () => GET('/api/presets'),
  chatHistory: (scope, name) => GET('/api/chat?' + qs({ scope, name })),
  ppSteps: () => GET('/api/postprocess/steps'),
  ppSamples: () => GET('/api/postprocess/samples'),
  modelsInstalled: () => GET('/api/models/installed'),
  modelsSearch: (q, kind) => GET('/api/models/search?' + qs({ q, kind })),

  // writes
  newHero: (name, prompt, category) => POST('/api/new', { name, prompt, category }),
  command: (message, context, gen) => POST('/api/command', { message, context, gen }),
  gen: (name) => POST('/api/gen', { name }),
  pick: (name, candidate) => POST('/api/pick', { name, candidate }),
  vector: (name) => POST('/api/vector', { name }),
  moreLike: (name, candidate) => POST('/api/morelike', { name, candidate }),
  poses: (name, poses) => POST('/api/character/poses', { name, poses }),
  assign: (name, category) => POST('/api/assign', { name, category }),
  refs: (name, refs) => POST('/api/refs', { name, refs }),
  setLoras: (name, loras) => POST('/api/loras', { name, loras }),
  upload: (dataUrl, into, filename) => POST('/api/upload', { dataUrl, into, filename }),
  explore: (phrase, n, category, asset) => POST('/api/explore', { phrase, n, category, asset }),
  synthesize: (phrase, picks, category) => POST('/api/synthesize', { phrase, picks, category }),
  saveSettings: (settings) => POST('/api/settings', { settings }),
  diagnose: (error) => POST('/api/diagnose', { error }),
  webref: (query, n, into) => POST('/api/webref', { query, n, into }),
  modelDownload: (downloadUrl, kind, filename) => POST('/api/models/download', { downloadUrl, kind, filename }),
  postprocess: (name, chain, chain_name) => POST('/api/postprocess', { name, chain, chain_name }),
  ppPreview: (step, src, params) => POST('/api/postprocess/preview', { step, src, params }),
  savePreset: (p) => POST('/api/presets', p),
  deletePreset: (label) => POST('/api/presets', { delete: label }),
  // queue controls
  qPause: () => POST('/api/queue/pause'),
  qResume: () => POST('/api/queue/resume'),
  qCancel: (id) => POST('/api/queue/cancel', { id }),
  qClear: () => POST('/api/queue/clear'),
  genStop: () => POST('/api/gen/stop'),
  panic: () => POST('/api/panic'),
  freeVram: () => POST('/api/vram/free'),
  startComfyui: () => POST('/api/setup/start-comfyui'),
  startOllama: () => POST('/api/setup/start-ollama'),
  // optional feature-group installers (unlock on demand)
  capabilities: () => GET('/api/capabilities'),
  installCapability: (group) => POST('/api/capabilities/install', { group }),
  // categories
  catNew: (path, parent, settings) => POST('/api/category/new', { path, parent, settings }),
  catUpdate: (path, settings) => POST('/api/category/update', { path, settings }),
  catMove: (path, parent) => POST('/api/category/move', { path, parent }),
  catDelete: (path, cascade) => POST('/api/category/delete', { path, cascade }),
  // find a style
  tasteStart: (phrase, n) => POST('/api/taste/start', { phrase, n }),
  tasteRate: (session, path, stars) => POST('/api/taste/rate', { session, path, stars }),
  tasteNext: (session) => POST('/api/taste/next', { session }),
  tasteSave: (session, name) => POST('/api/taste/save-as-style', { session, name }),
};
