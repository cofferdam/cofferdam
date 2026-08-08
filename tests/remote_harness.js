/* A minimal browser stub for exercising web/remote.js outside a browser.
 *
 * Sixth of its kind, beside pwa_harness.js, audio_harness.js, spotify_harness.js,
 * youtube_harness.js and tasks_harness.js, and separate for the same reason
 * those are separate from each other. It injects the same `deps` contract
 * app.js passes at mount time — `api`, `el`, `escapeHtml` — so remote.js runs
 * exactly as it ships.
 *
 * The properties under test are behavioural and invisible to a scan of the
 * source: that the link endpoint is never polled, that a double tap sends one
 * mutation, that a failed status poll does not turn a running host into a
 * stopped one, that the blank tab is opened inside the click rather than after
 * the fetch, that a refused link closes that tab, and that the capability URL
 * reaches no storage, no DOM node and no log.
 *
 * Time is fake and advanced explicitly, so every bound is tested
 * deterministically rather than by waiting.
 *
 * Usage:  node tests/remote_harness.js <scenario>   -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const scenario = process.argv[2];
const ROOT = path.resolve(__dirname, "..");
const REMOTE_JS = fs.readFileSync(path.join(ROOT, "web", "remote.js"), "utf8");
const INDEX_HTML = fs.readFileSync(path.join(ROOT, "web", "index.html"), "utf8");

const IDS = Array.from(INDEX_HTML.matchAll(/id="([^"]+)"/g)).map((m) => m[1]);

/* A link with the confirmed structure and an entirely fake capability value.
   Nothing in this file is a real environment token. */
const FAKE_TOKEN = "FAKEfake0123456789-_TESTtok0";
const FAKE_URL = "https://claude.ai/code?environment=" + FAKE_TOKEN;

/* -------------------------------------------------------------------- clock */

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
function liveIntervals() {
  return timers.filter((t) => t.interval).length;
}
function drain(turns) {
  let chain = Promise.resolve();
  for (let i = 0; i < (turns || 40); i += 1) { chain = chain.then(() => {}); }
  return chain;
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

/* ---------------------------------------------------------------------- DOM */

const record = {
  requests: [],
  timerErrors: [],
  consoleOutput: [],
  storageWrites: [],
  tabs: [],
  uncaught: null
};

const elements = {};

function makeElement(id) {
  const listeners = {};
  let markup = "";
  const node = {
    id,
    hidden: false,
    textContent: "",
    disabled: false,
    value: "",
    open: false,
    listeners,
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener() {},
    getAttribute() { return null; },
    querySelectorAll() { return []; }
  };
  Object.defineProperty(node, "innerHTML", {
    get() { return markup; },
    set(value) {
      const previous = Array.from(markup.matchAll(/id="([^"]+)"/g)).map((m) => m[1]);
      markup = String(value);
      const current = Array.from(markup.matchAll(/id="([^"]+)"/g)).map((m) => m[1]);
      previous.concat(current).forEach(function (childId) {
        if (elements[childId]) { delete elements[childId]; }
      });
      current.forEach(function (childId) { elements[childId] = makeElement(childId); });
    }
  });
  /* remote.js renders into `[data-remote-body]` inside the panel, so the panel
     root has to answer that query the way a browser would. */
  node.querySelector = function (selector) {
    if (selector === "[data-remote-body]" && id === "remotePanel") { return bodyNode; }
    return null;
  };
  return node;
}

const bodyNode = (function () {
  let markup = "";
  const node = { id: "remoteBody", querySelector() { return null; } };
  Object.defineProperty(node, "innerHTML", {
    get() { return markup; },
    set(value) {
      const previous = Array.from(markup.matchAll(/id="([^"]+)"/g)).map((m) => m[1]);
      markup = String(value);
      const current = Array.from(markup.matchAll(/id="([^"]+)"/g)).map((m) => m[1]);
      previous.concat(current).forEach(function (childId) {
        if (elements[childId]) { delete elements[childId]; }
      });
      current.forEach(function (childId) { elements[childId] = makeElement(childId); });
    }
  });
  return node;
})();

IDS.forEach((id) => { elements[id] = makeElement(id); });

function el(id) {
  if (!elements[id]) { elements[id] = makeElement(id); }
  return elements[id];
}

function escapeHtml(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

const documentStub = {
  visibilityState: "visible",
  getElementById(id) { return elements[id] || null; }
};

function fire(type, target) {
  const panel = el("remotePanel");
  (panel.listeners[type] || []).forEach((fn) => fn({ target }));
}

function button(id) {
  return { id, value: "", closest() { return null; }, getAttribute() { return null; } };
}

function rendered() {
  return bodyNode.innerHTML;
}

function buttonDisabled(id) {
  const match = rendered().match(new RegExp('<button id="' + id + '"[^>]*>'));
  return match ? /disabled/.test(match[0]) : null;
}

/* --------------------------------------------------------------------- open */

/* A window.open stub that records what the panel did with the tab it was
   given: whether it severed the opener, where it navigated, and whether it was
   closed. `granted: false` models a blocked pop-up. */
let grantTab = true;

function openStub(url, target) {
  if (!grantTab) {
    record.tabs.push({ granted: false, requestedUrl: url, target });
    return null;
  }
  const tab = {
    opener: { name: "cofferdam" },
    closed: false,
    navigatedTo: null,
    location: {
      replace(value) { tab.navigatedTo = value; }
    },
    close() { tab.closed = true; }
  };
  record.tabs.push(
    Object.defineProperties({ granted: true, requestedUrl: url, target }, {
      openerSevered: { get: () => tab.opener === null, enumerable: true },
      navigatedTo: { get: () => tab.navigatedTo, enumerable: true },
      closed: { get: () => tab.closed, enumerable: true }
    })
  );
  return tab;
}

/* ---------------------------------------------------------------------- api */

let routes = {};

function api(pathname, options) {
  const settings = options || {};
  const method = settings.method || "GET";
  record.requests.push({ path: pathname, method });
  const key = method + " " + pathname;
  const handler = routes[key] || routes[pathname];
  if (!handler) {
    return Promise.resolve({ ok: false, status: 500, payload: {} });
  }
  return handler(settings);
}

function ok(payload) { return () => Promise.resolve({ ok: true, status: 200, payload }); }
function fail(status, payload) {
  return () => Promise.resolve({ ok: false, status, payload: payload || {} });
}
function boom() { return () => Promise.reject(new Error("network")); }

function projectsPayload(remoteControlEnabled) {
  return {
    projects: [{
      project_id: "claude-sandbox",
      display_name: "Claude adapter sandbox",
      enabled: true,
      remote_control_enabled: remoteControlEnabled !== false,
      adapters: ["claude-code"],
      notes: null
    }]
  };
}

function session(state, extra) {
  return {
    session: Object.assign({
      project_id: "claude-sandbox",
      kind: "claude_remote_control",
      unit: "cofferdam-rc@claude-sandbox.service",
      state,
      active_state: state === "running" ? "active" : "inactive",
      sub_state: state === "running" ? "running" : "dead",
      generation: state === "stopped" ? null : "gen-1",
      url_available: false,
      auth_required: false,
      awaiting_consent: false,
      started_at: null,
      last_seen_at: "2026-08-08T10:00:00+00:00",
      error: null
    }, extra || {})
  };
}

function setup(remoteControlEnabled, state, extra) {
  routes = {
    "/api/task-projects": ok(projectsPayload(remoteControlEnabled)),
    "/api/remote-control/claude-sandbox": ok(session(state, extra))
  };
}

/* ------------------------------------------------------------------ sandbox */

const storage = {
  store: {},
  setItem(key, value) { record.storageWrites.push({ key, value: String(value) }); this.store[key] = String(value); },
  getItem(key) { return Object.prototype.hasOwnProperty.call(this.store, key) ? this.store[key] : null; },
  removeItem(key) { delete this.store[key]; }
};

const sandbox = {
  window: null,
  document: documentStub,
  setTimeout: setTimeoutStub,
  setInterval: setIntervalStub,
  clearTimeout: clearTimerStub,
  clearInterval: clearTimerStub,
  open: openStub,
  localStorage: storage,
  sessionStorage: storage,
  Promise,
  Date,
  Math,
  JSON,
  RegExp,
  Error,
  String,
  Number,
  Boolean,
  Object,
  Array,
  encodeURIComponent,
  console: {
    log: (...a) => record.consoleOutput.push(a.join(" ")),
    warn: (...a) => record.consoleOutput.push(a.join(" ")),
    error: (...a) => record.consoleOutput.push(a.join(" ")),
    info: (...a) => record.consoleOutput.push(a.join(" ")),
    debug: (...a) => record.consoleOutput.push(a.join(" "))
  }
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(REMOTE_JS, sandbox);

const panelApi = sandbox.CofferdamRemote;

function mount() {
  panelApi.mount({ api, escapeHtml, el });
  return drain();
}

/* Everything the URL must never reach, checked in one place. */
function leakReport() {
  const html = rendered();
  return {
    urlInHtml: html.indexOf(FAKE_TOKEN) !== -1 || html.indexOf("environment=") !== -1,
    urlInStorage: record.storageWrites.some((w) => w.value.indexOf(FAKE_TOKEN) !== -1),
    urlInConsole: record.consoleOutput.some((line) => line.indexOf(FAKE_TOKEN) !== -1),
    storageWriteCount: record.storageWrites.length,
    consoleCount: record.consoleOutput.length
  };
}

function linkRequestCount() {
  return record.requests.filter((r) => r.path.indexOf("/link") !== -1).length;
}
function statusRequestCount() {
  return record.requests.filter((r) => /\/api\/remote-control\/[^/]+$/.test(r.path)).length;
}

function emit(extra) {
  const out = Object.assign({
    scenario,
    html: rendered(),
    requests: record.requests,
    linkRequests: linkRequestCount(),
    statusRequests: statusRequestCount(),
    tabs: record.tabs,
    leak: leakReport(),
    timerErrors: record.timerErrors,
    liveIntervals: liveIntervals()
  }, extra || {});
  process.stdout.write(JSON.stringify(out));
}

/* ---------------------------------------------------------------- scenarios */

const scenarios = {
  async capability_disabled() {
    setup(false, "stopped");
    await mount();
    emit({ startDisabled: buttonDisabled("rcStart"), stopDisabled: buttonDisabled("rcStop") });
  },

  async stopped() {
    setup(true, "stopped");
    await mount();
    emit({ startDisabled: buttonDisabled("rcStart"), openDisabled: buttonDisabled("rcOpen") });
  },

  async starting() {
    setup(true, "starting");
    await mount();
    emit({ startDisabled: buttonDisabled("rcStart"), stopDisabled: buttonDisabled("rcStop") });
  },

  async awaiting_consent() {
    setup(true, "running", { awaiting_consent: true });
    await mount();
    emit({ openDisabled: buttonDisabled("rcOpen") });
  },

  async running_without_link() {
    setup(true, "running", { url_available: false });
    await mount();
    emit({ openDisabled: buttonDisabled("rcOpen") });
  },

  async running_with_link() {
    setup(true, "running", { url_available: true });
    await mount();
    emit({ openDisabled: buttonDisabled("rcOpen"), stopDisabled: buttonDisabled("rcStop") });
  },

  async failed() {
    setup(true, "failed", { error: "the host exited" });
    await mount();
    emit({ startDisabled: buttonDisabled("rcStart"), stopDisabled: buttonDisabled("rcStop") });
  },

  async unknown_backend() {
    routes = {
      "/api/task-projects": ok(projectsPayload(true)),
      "/api/remote-control/claude-sandbox": fail(503, { error: { message: "unreachable" } })
    };
    await mount();
    emit({});
  },

  /* A failed *status* poll must not turn a confirmed running host into a
     stopped one. */
  async status_failure_keeps_last_state() {
    setup(true, "running", { url_available: true });
    await mount();
    const before = rendered();
    routes["/api/remote-control/claude-sandbox"] = boom();
    advance(11000);
    await drain();
    emit({ before, after: rendered(), openDisabled: buttonDisabled("rcOpen") });
  },

  /* Two taps in the same tick must produce one mutation. */
  async double_start() {
    setup(true, "stopped");
    routes["POST /api/remote-control/claude-sandbox/start"] = ok(session("running"));
    await mount();
    fire("click", button("rcStart"));
    fire("click", button("rcStart"));
    await drain();
    emit({ startPosts: record.requests.filter((r) => r.path.indexOf("/start") !== -1).length });
  },

  async double_stop() {
    setup(true, "running", { url_available: true });
    routes["POST /api/remote-control/claude-sandbox/stop"] = ok(session("stopped"));
    await mount();
    fire("click", button("rcStop"));
    fire("click", button("rcStop"));
    await drain();
    emit({ stopPosts: record.requests.filter((r) => r.path.indexOf("/stop") !== -1).length });
  },

  /* Capability revoked while a host still runs: no Start, but Stop must work. */
  async revoked_capability_can_still_stop() {
    setup(false, "running", { url_available: true });
    routes["POST /api/remote-control/claude-sandbox/stop"] = ok(session("stopped"));
    await mount();
    const startDisabled = buttonDisabled("rcStart");
    const stopDisabled = buttonDisabled("rcStop");
    fire("click", button("rcStop"));
    await drain();
    emit({
      startDisabled,
      stopDisabled,
      stopPosts: record.requests.filter((r) => r.path.indexOf("/stop") !== -1).length
    });
  },

  /* Polling touches the status route only, never the link route. */
  async polling_never_touches_link() {
    setup(true, "running", { url_available: true });
    await mount();
    advance(60000);
    await drain();
    emit({});
  },

  async polling_stops_when_hidden() {
    setup(true, "running", { url_available: true });
    await mount();
    const beforeHidden = statusRequestCount();
    documentStub.visibilityState = "hidden";
    advance(60000);
    await drain();
    const whileHidden = statusRequestCount();
    documentStub.visibilityState = "visible";
    advance(11000);
    await drain();
    emit({ beforeHidden, whileHidden, afterVisible: statusRequestCount() });
  },

  async polling_cleaned_up_on_stop() {
    setup(true, "running", { url_available: true });
    await mount();
    const before = liveIntervals();
    panelApi.stop();
    await drain();
    const after = liveIntervals();
    const requestsBefore = record.requests.length;
    advance(120000);
    await drain();
    emit({ intervalsBefore: before, intervalsAfter: after, requestsGrew: record.requests.length - requestsBefore });
  },

  async refresh_after_mutation() {
    setup(true, "stopped");
    routes["POST /api/remote-control/claude-sandbox/start"] = ok(session("running"));
    await mount();
    const before = statusRequestCount();
    fire("click", button("rcStart"));
    await drain();
    emit({ statusBefore: before, statusAfter: statusRequestCount() });
  },

  /* The whole point of the flow: a tab opened inside the gesture, opener
     severed, navigated once, and the URL nowhere else. */
  async open_link_success() {
    setup(true, "running", { url_available: true });
    routes["/api/remote-control/claude-sandbox/link"] =
      ok({ link: { project_id: "claude-sandbox", generation: "gen-1", url: FAKE_URL } });
    await mount();
    const linksBeforeClick = linkRequestCount();
    fire("click", button("rcOpen"));
    const tabOpenedSynchronously = record.tabs.length === 1;
    await drain();
    emit({
      linksBeforeClick,
      tabOpenedSynchronously,
      navigatedTo: record.tabs[0] ? record.tabs[0].navigatedTo : null,
      openerSevered: record.tabs[0] ? record.tabs[0].openerSevered : null,
      closed: record.tabs[0] ? record.tabs[0].closed : null
    });
  },

  async open_link_refused() {
    setup(true, "running", { url_available: true });
    routes["/api/remote-control/claude-sandbox/link"] =
      fail(409, { error: { code: "remote_control_link_unavailable", message: "no link" } });
    await mount();
    fire("click", button("rcOpen"));
    await drain();
    emit({
      closed: record.tabs[0] ? record.tabs[0].closed : null,
      navigatedTo: record.tabs[0] ? record.tabs[0].navigatedTo : null,
      stillRunning: rendered().indexOf("running") !== -1
    });
  },

  /* A malformed value must never be navigated to, even if the server sent it. */
  async open_link_rejects_malformed() {
    setup(true, "running", { url_available: true });
    routes["/api/remote-control/claude-sandbox/link"] =
      ok({ link: { url: "https://evil.test/code?environment=" + FAKE_TOKEN } });
    await mount();
    fire("click", button("rcOpen"));
    await drain();
    emit({
      navigatedTo: record.tabs[0] ? record.tabs[0].navigatedTo : null,
      closed: record.tabs[0] ? record.tabs[0].closed : null
    });
  },

  async open_link_network_error() {
    setup(true, "running", { url_available: true });
    routes["/api/remote-control/claude-sandbox/link"] = boom();
    await mount();
    fire("click", button("rcOpen"));
    await drain();
    emit({ closed: record.tabs[0] ? record.tabs[0].closed : null });
  },

  /* Pop-up blocked: say so, never navigate this page to the capability. */
  async open_link_popup_blocked() {
    grantTab = false;
    setup(true, "running", { url_available: true });
    routes["/api/remote-control/claude-sandbox/link"] =
      ok({ link: { url: FAKE_URL } });
    await mount();
    fire("click", button("rcOpen"));
    await drain();
    emit({ granted: record.tabs[0] ? record.tabs[0].granted : null });
  },

  /* Open is not offered, and pressing it does nothing, when no link exists. */
  async open_ignored_without_link() {
    setup(true, "running", { url_available: false });
    await mount();
    fire("click", button("rcOpen"));
    await drain();
    emit({ openDisabled: buttonDisabled("rcOpen") });
  },

  async double_open_sends_one_request() {
    setup(true, "running", { url_available: true });
    routes["/api/remote-control/claude-sandbox/link"] =
      ok({ link: { url: FAKE_URL } });
    await mount();
    fire("click", button("rcOpen"));
    fire("click", button("rcOpen"));
    await drain();
    emit({ tabsOpened: record.tabs.length });
  },

  /* After a successful open, nothing anywhere retains the value. */
  async url_not_retained_after_open() {
    setup(true, "running", { url_available: true });
    routes["/api/remote-control/claude-sandbox/link"] =
      ok({ link: { url: FAKE_URL } });
    await mount();
    fire("click", button("rcOpen"));
    await drain();
    advance(30000);
    await drain();
    emit({ htmlAfter: rendered().indexOf(FAKE_TOKEN) !== -1 });
  }
};

if (!scenarios[scenario]) {
  process.stdout.write(JSON.stringify({ error: "unknown scenario", scenario }));
  process.exit(2);
}

scenarios[scenario]().catch(function (error) {
  process.stdout.write(JSON.stringify({ error: String(error && error.message), scenario }));
  process.exit(1);
});
