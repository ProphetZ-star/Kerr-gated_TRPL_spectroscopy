'use strict';
// Minimal DOM + fake clock + fixture fetch router used to execute the real
// frontend ES modules inside node:vm. No browser, no real backend.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..', '..', '..');
const FIXTURES_PATH = process.env.KERR_FIXTURE_PATH || path.join(ROOT, 'test-output', 'data-flow-fixtures.json');
let FIXTURES = null;
function fixtures() {
  if (!FIXTURES) FIXTURES = JSON.parse(fs.readFileSync(FIXTURES_PATH, 'utf8'));
  return FIXTURES;
}
const clone = value => JSON.parse(JSON.stringify(value));

// ---------------------------------------------------------------- DOM model
const CANVAS_IDS = {
  spectrum: [820, 300], map: [760, 420], decay: [220, 420],
  analysisCurve: [860, 320], analysisResidual: [860, 130],
  calImage: [820, 300], calSpectrum: [820, 300], calMapping: [820, 250], calResidual: [820, 150],
};

function makeCanvasContext() {
  const ctx = {
    ops: [], strokes: [], path: [], dash: [], arcs: [],
    fillStyle: '', strokeStyle: '', lineWidth: 1, font: '', textAlign: 'left',
    scale() {}, save() {}, restore() {}, translate() {}, rotate() {}, drawImage(...args) { this.ops.push({op:'drawImage',args}); }, putImageData() {},
    beginPath() { this.path = []; },
    moveTo(x, y) { this.path.push([x, y]); },
    lineTo(x, y) { this.path.push([x, y]); },
    stroke() { this.strokes.push({ color: String(this.strokeStyle), dash: [...this.dash], points: this.path.map(p => [...p]) }); },
    fill() {},
    arc(...args) { this.arcs.push(args); },
    rect() {}, clip() {},
    strokeRect() {},
    setLineDash(list) { this.dash = list || []; },
    fillRect(x, y, w, h) { this.ops.push({ op: 'fillRect', color: String(this.fillStyle), x, y, w, h }); },
    fillText(text, x, y) { this.ops.push({ op: 'fillText', text: String(text), x, y, color: String(this.fillStyle) }); },
    createImageData(w, h) { return { width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }; },
  };
  return ctx;
}

class Element {
  constructor(tag = 'div') {
    this.tagName = String(tag);
    this.children = [];
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this._text = '';
    this.title = '';
    this.type = '';
    this.step = '';
    this.min = '';
    this.max = '';
    this.files = [];
    this.style = {};
    this.dataset = {};
    this.attrs = {};
    this._classes = new Set();
    this._handlers = {};
    this._rect = null;
    this._ctx = null;
    this.width = 0;
    this.height = 0;
    this.invalid = null;
  }
  get className() { return [...this._classes].join(' '); }
  set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get textContent() { return (this._text || '') + this.children.map(child => (child && child.textContent !== undefined ? child.textContent : '')).join(''); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get classList() {
    const self = this;
    return {
      add: (...names) => names.forEach(n => self._classes.add(n)),
      remove: (...names) => names.forEach(n => self._classes.delete(n)),
      toggle: (name, force) => {
        if (force === undefined) { self._classes.has(name) ? self._classes.delete(name) : self._classes.add(name); }
        else if (force) self._classes.add(name); else self._classes.delete(name);
      },
      contains: name => self._classes.has(name),
    };
  }
  append(...nodes) { for (const node of nodes) this.children.push(node); }
  add(...nodes) { this.append(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = ''; }
  get lastElementChild() { return this.children.at(-1) || null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : (k === 'id' ? this.id : null); }
  hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k); }
  addEventListener(type, fn) { (this._handlers[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this._handlers[type] = (this._handlers[type] || []).filter(f => f !== fn); }
  dispatch(type, event) { for (const fn of this._handlers[type] || []) fn(event); }
  getBoundingClientRect() { return this._rect || { width: 0, height: 0, left: 0, top: 0 }; }
  setBoundingRect(rect) { this._rect = rect; }
  getContext(kind) {
    if (kind !== '2d') return null;
    if (!this._ctx) this._ctx = makeCanvasContext();
    return this._ctx;
  }
  setCustomValidity(message) { this.invalid = message || null; }
  checkValidity() { return !this.invalid; }
  reportValidity() { return !this.invalid; }
  focus() { this._focused = true; }
  showModal() {
    // HTMLDialogElement.showModal() throws InvalidStateError when the dialog is
    // already open; mirror that so nested-modal flows are caught.
    if (this.open) { const error = new Error('InvalidStateError: Failed to execute \'showModal\' on \'HTMLDialogElement\': The element already has an open modal dialog.'); error.name = 'InvalidStateError'; throw error; }
    this.open = true;
  }
  close() { this.open = false; }
  scrollIntoView() {}
  click() {}
  matches(selector) {
    for (const part of String(selector).split(',').map(s => s.trim()).filter(Boolean)) {
      if (part.startsWith('#')) { if (this.id === part.slice(1)) return true; continue; }
      if (part.startsWith('.')) { if (this._classes.has(part.slice(1))) return true; continue; }
      const attr = part.match(/^\[([\w-]+)(?:=(.*))?\]$/);
      if (attr) {
        const value = this.dataset[attr[1]] ?? this.attrs[attr[1]];
        if (value === undefined) continue;
        if (attr[2] === undefined || String(value) === attr[2]) return true;
        continue;
      }
      if (String(this.tagName).toLowerCase() === part.toLowerCase()) return true;
    }
    return false;
  }
  querySelectorAll(selector) {
    const found = [];
    const walk = node => {
      for (const child of node.children) {
        if (child instanceof Element) {
          if (child.matches(selector)) found.push(child);
          walk(child);
        }
      }
    };
    walk(this);
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

// Form defaults copied from index.html so request() validation passes before a
// status refresh fills the fields from a loaded run.
const FORM_DEFAULTS = {
  exposure: '1.000', center: '600.0', grating: '300', slit: '200', repeats: '3',
  speedup: '50', seed: '42', framesPerPoint: '1', settle: '0.05', fault: 'none',
  faultPoint: '2', backgroundKind: 'dark', backgroundCondition: 'Synthetic shutter closed',
  savePath: 'Sample 01 TRPL', delay: '0.350', waveSelect: '600', windowSelect: '5',
  analysisWave: '600', analysisWindow: '5', fitModel: 'double', irfWidth: '.25',
  fitStart: '', fitEnd: '', calExposure: '1.0', calRowStart: '85', calRowEnd: '165',
  calOrder: '2', calConvention: 'air', openPath: '',
};
const CHECKED_DEFAULTS = { autosave: true, 'roundSelect': null };

const CSS_COLORS = {
  '--plot': '#plot', '--muted': '#muted', '--chart-grid': '#grid', '--plot-border': '#border',
  '--data': '#data', '--amber': '#amber', '--warning': '#warning', '--cursor': '#cursor',
  '--field': '#field', '--blue': '#blue', '--text': '#text', '--surface': '#surface',
  '--line': '#line', '--bg': '#bg',
};

function createHarness(options = {}) {
  const fx = fixtures();
  const state = {
    token: 'fixture-token',
    status: clone(options.status || fx.initial_status),
    records: clone(options.records || []),
    plan: clone(fx.run.plan),
    tasks: {},
    taskSeq: 0,
    calFrame: null,
    requests: [],
    errors: {},
    newRun: null,
    exportText: 'record_id,round_index\n0,0\n',
    applied: false,
    background: null,
  };

  // ------------------------------------------------------------- elements
  const elements = new Map();
  const special = {};
  const get = id => {
    if (!elements.has(id)) {
      const tag = Object.prototype.hasOwnProperty.call(CANVAS_IDS, id) ? 'canvas' : 'div';
      const element = new Element(tag);
      element.id = id;
      if (tag === 'canvas') {
        const [width, height] = CANVAS_IDS[id];
        element.setBoundingRect({ width, height, left: 0, top: 0 });
      }
      if (Object.prototype.hasOwnProperty.call(FORM_DEFAULTS, id)) element.value = FORM_DEFAULTS[id];
      if (Object.prototype.hasOwnProperty.call(CHECKED_DEFAULTS, id) && CHECKED_DEFAULTS[id] !== null) element.checked = CHECKED_DEFAULTS[id];
      elements.set(id, element);
    }
    return elements.get(id);
  };
  for (const id of Object.keys(CANVAS_IDS)) get(id);

  const makeButton = (dataset = {}) => { const b = new Element('button'); b.dataset = { ...dataset }; return b; };
  const modeButtons = [{ mode: 'scan' }, { mode: 'single' }, { mode: 'repeat' }].map(d => makeButton(d));
  modeButtons[0].classList.add('selected');
  const spectrumScaleButtons = [{ scale: 'linear' }, { scale: 'log' }].map(d => makeButton(d));
  const mapScaleButtons = [{ scale: 'linear' }, { scale: 'log' }].map(d => makeButton(d));
  const analysisScaleButtons = [{ scale: 'linear' }, { scale: 'log' }].map(d => makeButton(d));
  const calAxisButtons = [{ axis: 'pixel' }, { axis: 'wavelength' }].map(d => makeButton(d));
  const jogButtons = ['-1', '-.1', '.1', '1'].map(v => makeButton({ jog: v }));
  const connectedEls = [new Element('span'), new Element('span')];

  special.bar = new Element('div');
  special.bar.setAttribute('role', 'progressbar');
  special.workspace = new Element('main');
  special.foot = new Element('footer');
  special.data = new Element('div');
  special.badge = new Element('span');
  special.analysisResults = new Element('div');
  special.calResults = new Element('div');
  special.connected = connectedEls;

  const canvasOf = id => get(id);
  const document = {
    body: (() => { const body = new Element('body'); body.className = 'light'; return body; })(),
    activeElement: null,
    getElementById: get,
    createElement(tag) {
      const element = new Element(tag);
      if (String(tag).toLowerCase() === 'canvas') element.setBoundingRect({ width: 1024, height: 255, left: 0, top: 0 });
      this.created.push(element);
      return element;
    },
    created: [],
    createTextNode(text) { const node = new Element('#text'); node.textContent = String(text); return node; },
    querySelector(selector) {
      const s = String(selector).trim();
      if (s === '[role=progressbar]') return special.bar;
      if (s === '.workspace') return special.workspace;
      if (s === '.foot') return special.foot;
      if (s === '.data') return special.data;
      if (s === '.badge') return special.badge;
      if (s === '.analysis-results') return special.analysisResults;
      if (s === '.cal-results') return special.calResults;
      if (s.startsWith('#')) return get(s.slice(1));
      throw new Error('harness: unhandled document.querySelector(' + s + ')');
    },
    querySelectorAll(selector) {
      const s = String(selector).trim();
      if (s === '.connected') return special.connected;
      if (s === '[data-jog]') return jogButtons;
      if (s === '#mode button') return modeButtons;
      if (s === '#spectrumScale button') return spectrumScaleButtons;
      if (s === '#mapScale button') return mapScaleButtons;
      if (s === '#analysisScale button') return analysisScaleButtons;
      if (s === '#calAxis button') return calAxisButtons;
      throw new Error('harness: unhandled document.querySelectorAll(' + s + ')');
    },
  };
  // `new Option(text, value)` is used for the round selector.
  class Option extends Element {
    constructor(text = '', value = '') { super('option'); this.textContent = String(text); this.value = String(value); this.text = String(text); }
  }

  // --------------------------------------------------------------- clock
  const clock = { now: 0, seq: 0, timers: [] };
  const setTimeoutFn = (fn, ms = 0) => { const id = ++clock.seq; clock.timers.push({ id, at: clock.now + Math.max(0, Number(ms) || 0), fn, every: 0 }); return id; };
  const clearTimeoutFn = id => { clock.timers = clock.timers.filter(t => t.id !== id); };
  const setIntervalFn = (fn, ms = 1) => { const id = ++clock.seq; clock.timers.push({ id, at: clock.now + Math.max(1, Number(ms) || 1), fn, every: Math.max(1, Number(ms) || 1) }); return id; };
  const advance = async ms => {
    const target = clock.now + Number(ms);
    const pending = [];
    for (let guard = 0; guard < 200; guard++) {
      clock.timers.sort((a, b) => a.at - b.at);
      const timer = clock.timers[0];
      if (!timer || timer.at > target) break;
      clock.now = timer.at;
      if (timer.every) timer.at += timer.every; else clock.timers.shift();
      try { const r = timer.fn(); if (r && typeof r.then === 'function') pending.push(r); }
      catch (error) { pending.push(Promise.reject(error)); }
    }
    clock.now = target;
    return pending;
  };
  const settle = async (rounds = 25) => { for (let i = 0; i < rounds; i++) await new Promise(resolve => setImmediate(resolve)); };

  // ---------------------------------------------------------------- fetch
  const jsonResponse = (body, status = 200, statusText = '') => {
    const text = JSON.stringify(body);
    return new Response(text, { status, statusText, headers: { 'Content-Type': 'application/json' } });
  };
  const errorResponse = detail => jsonResponse({ detail }, 409);
  const requestLog = state.requests;
  const fetchFn = async (url, optionSet = {}) => {
    const method = (optionSet.method || 'GET').toUpperCase();
    const headers = optionSet.headers || {};
    const body = optionSet.body ? (typeof optionSet.body==='string'?JSON.parse(optionSet.body):optionSet.body) : undefined;
    const entry = { url: String(url), method, body, headers: { ...headers } };
    requestLog.push(entry);
    if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
      if (headers['X-Session-Token'] !== state.token) { entry.rejected = true; return jsonResponse({ detail: 'Missing local session token' }, 403); }
    }
    for (const [prefix, injected] of Object.entries(state.errors)) {
      if (entry.url.startsWith(prefix)) {
        if (Object.prototype.hasOwnProperty.call(injected, 'raw')) return new Response(injected.raw, { status: injected.status || 500 });
        return jsonResponse({ detail: injected.detail }, injected.status || 409);
      }
    }
    if (state.statusError && entry.url === '/api/v1/status') return jsonResponse({ detail: state.statusError }, 503);
    if (state.planError && entry.url === '/api/v1/scan/plan') return jsonResponse({ detail: state.planError }, 422);
    if (state.recordsError && /\/records\?/.test(entry.url)) return jsonResponse({ detail: state.recordsError }, 409);
    if (state.fitError && /\/fits$/.test(entry.url)) return jsonResponse({ detail: state.fitError }, 409);
    if (state.openError && entry.url === '/api/v1/runs/open') return jsonResponse({ detail: state.openError }, 409);
    if (entry.url === '/api/v1/session') return jsonResponse({ token: state.token });
    if (entry.url === '/api/v1/status') return jsonResponse(clone(state.status));
    if (entry.url === '/api/v1/output/check') return jsonResponse(clone(state.outputCheck || {path:body.path,conflicts:[],overwrite_token:null}));
    if (entry.url === '/api/v1/scan/plan' || entry.url === '/api/v1/plan') return jsonResponse(clone(state.plan));
    if (entry.url === '/api/v1/runs' && method === 'POST') {
      const run = clone(state.newRun || { ...fx.run, run_id: 'started-run-1', status: 'preparing', completed_points: 0, committed_frames: 0, acquired_frames: 0, saved: false, path: null, error: null });
      state.status = { ...clone(state.status), busy: 'scan', run };
      return jsonResponse(run);
    }
    if (entry.url.startsWith('/api/v1/runs/open')) {
      const run = clone(options.openRun || fx.run);
      state.status = { ...clone(state.status), run, busy: null };
      state.records = clone(options.openRecords || fx.records);
      return jsonResponse(run);
    }
    if (entry.url === '/api/v1/runs/import') {
      const run=clone(fx.run);state.status={...clone(state.status),run,busy:null};state.records=clone(fx.records);return jsonResponse(run);
    }
    let match = entry.url.match(/^\/api\/v1\/runs\/([^/?]+)\/(records)\?offset=(\d+)&limit=(\d+)$/);
    if (match) {
      const offset = Number(match[3]), limit = Number(match[4]);
      return jsonResponse({ records: clone(state.records.slice(offset, offset + limit)), total: state.records.length });
    }
    match = entry.url.match(/^\/api\/v1\/runs\/([^/?]+)\/export(?:\?.*)?$/);
    if (match) {
      entry.exportQuery = entry.url.split('?')[1];
      return new Response(state.exportText, { status: 200, headers: { 'Content-Type': 'text/csv' } });
    }
    match = entry.url.match(/^\/api\/v1\/runs\/([^/?]+)\/(pause|resume|stop|discard|save)$/);
    if (match) {
      const action = match[2];
      if (action === 'save') {
        state.status.run = { ...clone(state.status.run || fx.run), saved: true, path: body.path };
        return jsonResponse(clone(state.status.run));
      }
      if (action === 'discard') { state.status = { ...clone(state.status), run: null, busy: null }; return jsonResponse({ discarded: true }); }
      const run = clone(state.status.run || fx.run);
      run.status = action === 'pause' ? 'paused' : action === 'resume' ? 'running' : 'stopping';
      // The backend keeps the device marked busy until the run reaches a terminal state.
      state.status = { ...clone(state.status), run };
      return jsonResponse(run);
    }
    match = entry.url.match(/^\/api\/v1\/runs\/([^/?]+)\/fits$/);
    if (match) {
      const taskId = 'fit-task-' + (++state.taskSeq);
      state.tasks[taskId] = { status: 'running', run_id: match[1] };
      state.lastFitRequest = body;
      return jsonResponse({ task_id: taskId });
    }
    match = entry.url.match(/^\/api\/v1\/tasks\/([^/?]+)\/cancel$/);
    if(match){state.tasks[match[1]]={...(state.tasks[match[1]]||{}),status:'cancelled'};return jsonResponse({task_id:match[1],...state.tasks[match[1]]});}
    match = entry.url.match(/^\/api\/v1\/tasks\/([^/?]+)$/);
    if (match) {
      const task = state.tasks[match[1]] || { status: 'error', error: 'Task not found' };
      return jsonResponse(clone(task));
    }
    if (entry.url.startsWith('/api/v1/calibration/solutions/')) return jsonResponse(clone(fx.exported));
    if (entry.url === '/api/v1/calibration/frame') return jsonResponse(state.calFrame ? clone(state.calFrame) : null);
    if (entry.url === '/api/v1/calibration/frames') { state.calFrame = clone(fx.frame); state.records.length; return jsonResponse(clone(fx.frame)); }
    if (entry.url === '/api/v1/calibration/extract') return jsonResponse(clone(state.extracted || fx.extracted));
    if (entry.url === '/api/v1/calibration/fit') return jsonResponse(clone(fx.solution));
    if (entry.url === '/api/v1/calibration/apply') {
      state.applied = true;
      state.status = { ...clone(fx.applied_status) };
      return jsonResponse(clone(state.status));
    }
    if (entry.url === '/api/v1/camera/configure') {
      state.status.camera = { ...state.status.camera, exposure_s: body.exposure_s };
      state.status.background = null;
      return jsonResponse({ exposure_s: body.exposure_s });
    }
    if (entry.url === '/api/v1/spectrograph/config') {
      state.status = { ...clone(state.status), spectrograph: body, calibration: { calibration_id: null, source: null, wavelengths_nm: null } };
      return jsonResponse(clone(state.status));
    }
    if (entry.url === '/api/v1/backgrounds') {
      state.status.background = { spectrum: Array.from({ length: 1024 }, () => 100), exposure_s: state.status.camera.exposure_s, kind: body.kind, condition: body.condition };
      return jsonResponse({ accepted: true });
    }
    if(entry.url==='/api/v1/backgrounds/stop'){state.status.busy=null;return jsonResponse({accepted:true});}
    if (entry.url === '/api/v1/preview/start') { state.status = { ...clone(state.status), busy: 'preview' }; return jsonResponse({ accepted: true }); }
    if (entry.url === '/api/v1/preview/stop') { state.status = { ...clone(state.status), busy: null }; return jsonResponse({ accepted: true }); }
    if (entry.url === '/api/v1/stage/stop') { state.status = { ...clone(state.status), busy: null }; return jsonResponse({ accepted: true }); }
    if (entry.url === '/api/v1/stage/move') { state.status = { ...clone(state.status), busy: 'move' }; return jsonResponse({ accepted: true }); }
    if (entry.url === '/api/v1/stage/zero') return jsonResponse(clone(state.status));
    if (entry.url.startsWith('/api/v1/devices/')) return jsonResponse(clone(state.status));
    throw new Error('harness: unhandled fetch ' + method + ' ' + entry.url);
  };

  // ------------------------------------------------------------ websocket
  const sockets = [];
  class WebSocketStub {
    constructor(url) {
      this.url = url; this.readyState = 0; this.sent = [];
      sockets.push(this);
      setTimeoutFn(() => { this.readyState = 1; if (this.onopen) this.onopen({}); }, 0);
    }
    send(data) { this.sent.push(data); }
    close() { this.readyState = 3; this.closed = true; if (this.onclose) this.onclose({}); }
    emit(message) { if (this.onmessage) this.onmessage({ data: JSON.stringify(message) }); }
  }

  const location = {
    host: '127.0.0.1:8765',
    _hash: '',
    get hash() { return this._hash; },
    set hash(value) {
      const normalized = String(value).startsWith('#') ? String(value) : '#' + value;
      if (normalized === this._hash) return;
      this._hash = normalized;
      for (const listener of windowListeners.hashchange) listener({});
    },
  };
  const windowListeners = { hashchange: [] };
  const windowStub = {
    addEventListener(type, fn) { (windowListeners[type] ||= []).push(fn); },
    removeEventListener() {},
    location,
  };
  const requestAnimationFrame = fn => { fn(); return 1; };

  const context = vm.createContext({
    console,
    document,
    window: windowStub,
    location,
    fetch: fetchFn,
    WebSocket: WebSocketStub,
    Response,
    Blob,
    URL: { createObjectURL: blob => { blobs.push(blob); return 'blob:' + blobs.length; }, revokeObjectURL: () => {} },
    setTimeout: setTimeoutFn,
    clearTimeout: clearTimeoutFn,
    setInterval: setIntervalFn,
    clearInterval: clearTimeoutFn,
    requestAnimationFrame,
    devicePixelRatio: 1,
    ResizeObserver: class { constructor(cb) { this.cb = cb; } observe() {} disconnect() {} },
    getComputedStyle: () => ({ getPropertyValue: name => name==='--no-data'?CSS_COLORS['--plot']:CSS_COLORS[name] || '' }),
    crypto: { randomUUID: () => 'uuid-' + (++clock.seq).toString(16).padStart(8, '0') },
    structuredClone,
    Float32Array, Float64Array, Int32Array, Uint8ClampedArray, Object, Array, JSON, Math, Number,
    Option,
  });
  const blobs = [];

  // ------------------------------------------------------------- modules
  const moduleCache = new Map();
  async function loadModule(relative) {
    const filename = path.resolve(ROOT, relative);
    if (moduleCache.has(filename)) return moduleCache.get(filename);
    const source = new vm.SourceTextModule(fs.readFileSync(filename, 'utf8'), { context, identifier: filename });
    moduleCache.set(filename, source);
    await source.link((specifier, parent) => loadModule(path.relative(ROOT, path.resolve(path.dirname(parent.identifier), specifier))));
    await source.evaluate();
    return source;
  }

  // ------------------------------------------------------------- helpers
  const harness = {
    ROOT, fixtures: fx, clone, state, get, document, special, context, clock,
    blobs, sockets, requests: requestLog,
    loadModule,
    async loadApp() { const mod = await loadModule('frontend/app.js'); await harness.settle(); return mod.namespace; },
    async settle(rounds) { await settle(rounds); },
    async advance(ms) { const pending = await advance(ms); await settle(); for (const p of pending) await p; await settle(); },
    async tick(ms = 600) { await harness.advance(ms); },
    setStatus(patch) { state.status = { ...clone(state.status), ...patch }; },
    patchStatus(fn) { state.status = fn(clone(state.status)); },
    setRecords(records) { state.records = clone(records); },
    setValue(id, value) { get(id).value = String(value); },
    setChecked(id, value) { get(id).checked = !!value; },
    async click(id, event = {}) { const handler = get(id).onclick; if (!handler) throw new Error('no onclick handler for #' + id); return handler(event); },
    async change(id, event = {}) { const handler = get(id).onchange; if (!handler) throw new Error('no onchange handler for #' + id); return handler(event); },
    fire(id, type, event = {}) { get(id).dispatch(type, event); },
    emit(message) { const socket = sockets.at(-1); if (!socket) throw new Error('no websocket'); socket.emit(message); },
    closeSocket() { const socket = sockets.at(-1); if (socket) socket.close(); },
    ctxOf(id) { return get(id).getContext('2d'); },
    strokesOf(id) { return get(id).getContext('2d').strokes; },
    opsOf(id) { return get(id).getContext('2d').ops; },
    lastRequest(prefix) { return [...requestLog].reverse().find(r => r.url.startsWith(prefix)); },
    requestsMatching(prefix) { return requestLog.filter(r => r.url.startsWith(prefix)); },
    findStrokes(id, color) { return harness.strokesOf(id).filter(s => s.color === color); },
    createdAnchors() { return document.created.filter(el => String(el.tagName).toLowerCase() === 'a'); },
    allElements() { return [...elements.values()]; },
    allText() { return harness.allElements().map(element => element.textContent).join('\n'); },
    async navigate(hash, settleMs = 1) {
      location.hash = hash;
      await harness.settle();
      await harness.advance(settleMs);
      return location.hash;
    },
    store() { return moduleCache.get(path.resolve(ROOT, 'frontend/store.js')).namespace.store; },
  };
  return harness;
}

// Force the WebSocket handshake / timers scheduled during app startup.
async function bootApp(harness) {
  await harness.loadApp();
  await harness.advance(1);
  return harness;
}

module.exports = { createHarness, bootApp, Element, fixtures, clone, ROOT, CANVAS_IDS, FORM_DEFAULTS };
