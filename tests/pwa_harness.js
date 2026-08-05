/* A minimal browser stub for exercising web/app.js outside a browser.
 *
 * The fresh-iPhone failure was not a wrong string anywhere — it was control
 * flow: an exception escaped the module before any state could be rendered, so
 * the page kept the markup it was served with. A structural scan cannot see
 * that. Running the real file against a stubbed DOM can.
 *
 * Everything here is a stub with no network and no storage of its own. Time is
 * fake and advanced explicitly, so the bounded-timeout behaviour is tested
 * deterministically rather than by waiting.
 *
 * Usage:  node tests/pwa_harness.js <scenario>   -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const scenario = process.argv[2];
const ROOT = path.resolve(__dirname, "..");
const APP_JS = fs.readFileSync(path.join(ROOT, "web", "app.js"), "utf8");
const INDEX_HTML = fs.readFileSync(path.join(ROOT, "web", "index.html"), "utf8");

/* Element ids come from the real index.html, so the stub cannot drift into
   providing something the shipped page does not have. */
const IDS = Array.from(INDEX_HTML.matchAll(/id="([^"]+)"/g)).map((m) => m[1]);

const TOKEN = "test-token-never-in-a-url";

/* ------------------------------------------------------------------- clock */

let now = 0;
const timers = [];
let nextTimerId = 1;

function setTimeoutStub(fn, ms) {
  const id = nextTimerId++;
  timers.push({ id, at: now + (ms || 0), fn, interval: null });
  return id;
}
function setIntervalStub(fn, ms) {
  const id = nextTimerId++;
  timers.push({ id, at: now + (ms || 0), fn, interval: ms || 1 });
  return id;
}
function clearTimerStub(id) {
  const index = timers.findIndex((t) => t.id === id);
  if (index !== -1) { timers.splice(index, 1); }
}

function advance(ms) {
  const target = now + ms;
  for (let guard = 0; guard < 10000; guard += 1) {
    const due = timers.filter((t) => t.at <= target).sort((a, b) => a.at - b.at)[0];
    if (!due) { break; }
    now = due.at;
    if (due.interval) { due.at = now + due.interval; } else { clearTimerStub(due.id); }
    try { due.fn(); } catch (error) { record.timerErrors.push(String(error && error.message)); }
  }
  now = target;
}

/* -------------------------------------------------------------------- DOM */

const record = {
  fetchUrls: [],
  socketUrls: [],
  socketProtocols: [],
  consoleOutput: [],
  timerErrors: [],
  uncaught: null
};

function makeElement(id) {
  const listeners = {};
  return {
    id,
    hidden: false,
    textContent: "",
    className: "",
    value: "",
    disabled: false,
    innerHTML: "",
    dataset: {},
    style: {},
    listeners,
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener() {},
    appendChild() {},
    remove() {},
    scrollIntoView() {},
    setSelectionRange() {},
    focus() {},
    closest() { return null; },
    querySelectorAll() { return []; }
  };
}

const elements = {};
IDS.forEach((id) => { elements[id] = makeElement(id); });
/* The header starts with whatever index.html actually ships — "connecting…".
   Starting it empty would hide the very failure under test: the page keeping
   its served markup because no code ever replaced it. */
const servedConnText = (INDEX_HTML.match(/id="connText">([^<]*)</) || [null, ""])[1];
elements.connText.textContent = servedConnText;
// index.html marks these hidden in the served markup; the stub must start the
// same way or the test would be measuring its own defaults.
["setup", "app", "connBanner", "connRetry", "setupError", "storageWarning",
 "capabilitiesUnavailable", "stubBanner", "shotPanel"].forEach((id) => {
  if (elements[id]) { elements[id].hidden = true; }
});

const documentStub = {
  hidden: false,
  getElementById(id) {
    if (!elements[id]) { elements[id] = makeElement(id); }
    return elements[id];
  },
  createElement() { return makeElement("created"); },
  addEventListener() {},
  querySelectorAll() { return []; }
};

/* ---------------------------------------------------------------- storage */

function makeStorage(mode) {
  if (mode === "throws") {
    // iOS Safari with Block All Cookies / Private Browsing: touching the API
    // raises rather than returning null.
    return new Proxy({}, {
      get() { throw new Error("SecurityError: storage is not available"); },
      set() { throw new Error("SecurityError: storage is not available"); }
    });
  }
  const data = mode && mode.seed ? { "cofferdam.token": mode.seed } : {};
  return {
    getItem(key) { return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null; },
    setItem(key, value) { data[key] = String(value); },
    removeItem(key) { delete data[key]; },
    _data: data
  };
}

/* ------------------------------------------------------------- net stubs */

function jsonResponse(status, payload) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json() { return Promise.resolve(payload); }
  });
}

const STATUS_PAYLOAD = {
  service: { api_version: "1", milestone: "M2B", actions: [], event_clients: 0 },
  host: { hostname: "host", platform: "linux", capabilities: {}, notes: [] },
  applications: []
};

function makeFetch(mode) {
  return function (url) {
    record.fetchUrls.push(String(url));
    if (mode === "timeout") { return new Promise(function () { /* never settles */ }); }
    if (mode === "unauthorized") { return jsonResponse(401, { detail: "unauthorized" }); }
    if (mode === "network-error") { return Promise.reject(new TypeError("Load failed")); }
    if (String(url).indexOf("/api/actions") !== -1) { return jsonResponse(200, { actions: [] }); }
    return jsonResponse(200, STATUS_PAYLOAD);
  };
}

let lastSocket = null;

function makeWebSocket(mode) {
  function WebSocketStub(url, protocols) {
    record.socketUrls.push(String(url));
    record.socketProtocols.push(protocols);
    this.url = url;
    this.readyState = 0; // CONNECTING
    this.onopen = null;
    this.onclose = null;
    this.onerror = null;
    this.onmessage = null;
    lastSocket = this;
    const self = this;
    if (mode === "open") {
      setTimeoutStub(function () {
        self.readyState = 1;
        if (self.onopen) { self.onopen(); }
      }, 5);
    } else if (mode === "reject-4401") {
      setTimeoutStub(function () {
        self.readyState = 3;
        if (self.onclose) { self.onclose({ code: 4401 }); }
      }, 5);
    }
    // mode === "hang": never opens, never closes — the case that used to leave
    // the header on "connecting…" forever.
  }
  WebSocketStub.prototype.send = function () {};
  WebSocketStub.prototype.close = function () {
    this.readyState = 3;
    if (this.onclose) { this.onclose({ code: 1006 }); }
  };
  WebSocketStub.CONNECTING = 0;
  WebSocketStub.OPEN = 1;
  WebSocketStub.CLOSING = 2;
  WebSocketStub.CLOSED = 3;
  return WebSocketStub;
}

/* ------------------------------------------------------------- scenarios */

const SCENARIOS = {
  fresh_no_token: { storage: {}, fetch: "ok", ws: "open" },
  fresh_no_token_storage_blocked: { storage: "throws", fetch: "ok", ws: "open" },
  stored_token_valid: { storage: { seed: TOKEN }, fetch: "ok", ws: "open" },
  stored_token_rejected: { storage: { seed: TOKEN }, fetch: "unauthorized", ws: "open" },
  stored_token_status_timeout: { storage: { seed: TOKEN }, fetch: "timeout", ws: "open" },
  stored_token_ws_hangs: { storage: { seed: TOKEN }, fetch: "ok", ws: "hang" },
  stored_token_ws_rejected: { storage: { seed: TOKEN }, fetch: "ok", ws: "reject-4401" },
  storage_blocked_then_token_entered: { storage: "throws", fetch: "ok", ws: "open" }
};

const config = SCENARIOS[scenario];
if (!config) {
  process.stdout.write(JSON.stringify({ error: "unknown scenario: " + scenario }));
  process.exit(2);
}

const windowStub = {};
const sandbox = {
  window: windowStub,
  document: documentStub,
  location: { protocol: "http:", host: "100.64.0.1:7101" },
  navigator: {},
  localStorage: makeStorage(config.storage),
  fetch: makeFetch(config.fetch),
  WebSocket: makeWebSocket(config.ws),
  setTimeout: setTimeoutStub,
  clearTimeout: clearTimerStub,
  setInterval: setIntervalStub,
  clearInterval: clearTimerStub,
  Promise,
  Object,
  Math,
  Date,
  JSON,
  String,
  Number,
  Array,
  Error,
  TypeError,
  URL: { createObjectURL() { return "blob:stub"; }, revokeObjectURL() {} },
  console: {
    log: (...a) => record.consoleOutput.push(a.join(" ")),
    warn: (...a) => record.consoleOutput.push(a.join(" ")),
    error: (...a) => record.consoleOutput.push(a.join(" "))
  }
};
windowStub.localStorage = sandbox.localStorage;
windowStub.addEventListener = function () {};
windowStub.CofferdamLive = null;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
try {
  vm.runInContext(APP_JS, sandbox, { filename: "app.js" });
} catch (error) {
  record.uncaught = String(error && error.message);
}

/* Let promises settle, then run out every bounded timeout the app installed.
 *
 * Microtasks are drained generously between small time steps. Advancing the
 * clock in one jump would fire the bounded timeouts before the stubbed fetch
 * chain had resolved, and every scenario would report a timeout that the real
 * browser would never see — the harness measuring itself. */
function settle(times) {
  let chain = Promise.resolve();
  for (let i = 0; i < times; i += 1) { chain = chain.then(() => {}); }
  return chain;
}

function drain(rounds) {
  let chain = Promise.resolve();
  for (let i = 0; i < rounds; i += 1) {
    chain = chain.then(() => settle(40)).then(() => { advance(500); });
  }
  return chain.then(() => settle(40));
}

drain(60).then(() => {
  // Optional second phase: a user typing a token into the fresh form.
  if (scenario === "storage_blocked_then_token_entered") {
    elements.tokenInput.value = TOKEN;
    const handlers = elements.saveToken.listeners.click || [];
    handlers.forEach((fn) => { try { fn(); } catch (error) { record.uncaught = String(error.message); } });
    return drain(40);
  }
  return null;
}).then(() => {
  process.stdout.write(JSON.stringify({
    scenario,
    uncaught: record.uncaught,
    timerErrors: record.timerErrors,
    connText: elements.connText.textContent,
    connDot: elements.dot.className,
    connBannerHidden: elements.connBanner.hidden,
    connBannerText: elements.connBanner.textContent,
    retryHidden: elements.connRetry.hidden,
    setupHidden: elements.setup.hidden,
    appHidden: elements.app.hidden,
    setupErrorHidden: elements.setupError.hidden,
    setupErrorText: elements.setupError.textContent,
    storageWarningHidden: elements.storageWarning.hidden,
    storageWarningText: elements.storageWarning.textContent,
    fetchUrls: record.fetchUrls,
    socketUrls: record.socketUrls,
    socketProtocols: record.socketProtocols,
    consoleOutput: record.consoleOutput,
    token: TOKEN
  }));
});
