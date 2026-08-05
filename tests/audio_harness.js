/* A minimal browser stub for exercising web/audio.js outside a browser.
 *
 * Separate from pwa_harness.js on purpose: that one drives app.js through
 * connection and token scenarios, and entangling the two would make each
 * harder to read than either is alone. This one injects the same `deps`
 * contract app.js passes at mount time — `api`, `el`, `escapeHtml` — so audio.js
 * runs exactly as it ships.
 *
 * The properties under test here are behavioural, not textual: that a second
 * tap does not send a second request, that a refused or ignored change never
 * renders as done, and that a request which never answers still gives the panel
 * back. A structural scan cannot see any of those.
 *
 * Time is fake and advanced explicitly, so the bounded pending state is tested
 * deterministically rather than by waiting.
 *
 * Usage:  node tests/audio_harness.js <scenario>   -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const scenario = process.argv[2];
const ROOT = path.resolve(__dirname, "..");
const AUDIO_JS = fs.readFileSync(path.join(ROOT, "web", "audio.js"), "utf8");
const INDEX_HTML = fs.readFileSync(path.join(ROOT, "web", "index.html"), "utf8");

const IDS = Array.from(INDEX_HTML.matchAll(/id="([^"]+)"/g)).map((m) => m[1]);

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
/* Promise callbacks live on the microtask queue, which the fake clock knows
   nothing about. A scenario that captured the DOM straight after firing an
   event would read the panel mid-flight — disabled, with the request still in
   the air — and quietly assert the wrong thing. Draining a generous number of
   turns lets the whole send -> respond -> re-read -> render chain settle. */
function drain(turns) {
  let chain = Promise.resolve();
  for (let i = 0; i < (turns || 25); i += 1) { chain = chain.then(() => {}); }
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

const record = { requests: [], timerErrors: [], uncaught: null };

function makeElement(id) {
  const listeners = {};
  return {
    id,
    hidden: false,
    textContent: "",
    innerHTML: "",
    disabled: false,
    value: "",
    listeners,
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener() {},
    /* Good enough for the one query audio.js makes: the live volume readout. */
    querySelector(selector) {
      if (selector === ".audio-volume-value") {
        return this._readout || (this._readout = makeElement("readout"));
      }
      return null;
    },
    querySelectorAll() { return []; },
    getAttribute() { return null; }
  };
}

const elements = {};
IDS.forEach((id) => { elements[id] = makeElement(id); });

function el(id) {
  if (!elements[id]) { elements[id] = makeElement(id); }
  return elements[id];
}

function escapeHtml(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fire(type, target) {
  const panel = el("audioPanel");
  (panel.listeners[type] || []).forEach((fn) => fn({ target }));
}

function button(id) {
  return { id, getAttribute() { return null; } };
}

function outputButton(resourceId) {
  return { id: "", getAttribute(name) { return name === "data-output" ? resourceId : null; } };
}

function slider(value) {
  return { id: "audioVolume", value: String(value), getAttribute() { return null; } };
}

/* ------------------------------------------------------------------ payloads */

const OUTPUT_A = {
  resource_id: "aout-aaa", stable_id: "asink-aaa", identity_stability: "hardware",
  node_id: 58, node_id_is_transient: true, object_serial: 58,
  node_name: "alsa_output.builtin", display_name: "Built-in Speaker",
  description: "Internal card", device_type: "builtin_speaker",
  device_type_evidence: "an internal PCI sound card", route: "Speaker",
  profile: "HiFi", available: true, is_default: true,
  volume_percent: 50, muted: false, channels: 2, channel_map: ["FL", "FR"]
};
const OUTPUT_B = Object.assign({}, OUTPUT_A, {
  resource_id: "aout-bbb", stable_id: "asink-bbb", node_id: 70, object_serial: 70,
  node_name: "alsa_output.hdmi", display_name: "Monitor Audio",
  device_type: "hdmi", route: "HDMI / DisplayPort", is_default: false, volume_percent: 30
});

function snapshotPayload(options) {
  const settings = options || {};
  const outputs = settings.outputs || [OUTPUT_A, OUTPUT_B];
  const streams = settings.streamsUnavailable
    ? {
        kind: "streams", status: "unavailable", count: 0, items: [],
        evidence: null, reason: "this host cannot be asked what is playing", warnings: []
      }
    : {
        kind: "streams", status: "ok", count: (settings.streams || []).length,
        items: settings.streams || [], evidence: null, reason: null, warnings: []
      };
  return {
    version: 1,
    observed_at: "2026-08-05T12:00:00.000Z",
    host: {}, boot: {},
    graph: { available: true, graph_id: "agraph-x", backend: "wireplumber" },
    backend: "wireplumber",
    default_output_resource_id: settings.defaultId || "aout-aaa",
    collections: {
      outputs: {
        kind: "outputs", status: "ok", count: outputs.length, items: outputs,
        evidence: null, reason: null, warnings: []
      },
      streams: streams
    },
    capabilities: [], warnings: settings.warnings || []
  };
}

/* The scripted server. Each scenario supplies how PUTs behave; GETs always
   return the current snapshot so a re-read after an action is realistic. */
function makeApi(behaviour) {
  let snapshot = behaviour.initial || snapshotPayload();
  return function api(pathname, options) {
    const settings = options || {};
    const method = settings.method || "GET";
    record.requests.push({ method, path: pathname, body: settings.body || null });

    if (method === "GET") {
      return Promise.resolve({ ok: true, status: 200, payload: snapshot });
    }
    if (behaviour.hang) { return new Promise(function () { /* never settles */ }); }
    if (behaviour.refuse) {
      return Promise.resolve({
        ok: false, status: 422,
        payload: { error: { code: "audio_volume_invalid", message: behaviour.refuse, detail: null } }
      });
    }
    const result = behaviour.result(settings.body, snapshot);
    if (result.snapshot) { snapshot = result.snapshot; }
    return Promise.resolve({ ok: true, status: 200, payload: result.payload });
  };
}

/* ----------------------------------------------------------------- scenarios */

function run() {
  const sandbox = {
    console: { log() {}, warn() {}, error() {} },
    setTimeout: setTimeoutStub,
    clearTimeout: clearTimerStub,
    setInterval: setIntervalStub,
    clearInterval: clearTimerStub,
    Promise, Date, JSON, Math, isNaN, parseInt, parseFloat, encodeURIComponent, String, Object
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(AUDIO_JS, sandbox, { filename: "audio.js" });

  const audio = sandbox.CofferdamAudio;
  const behaviours = {
    /* A change that really lands. */
    applied: {
      result(body, snapshot) {
        const value = body.volume_percent;
        const outputs = snapshot.collections.outputs.items.map((item) =>
          item.resource_id === "aout-aaa" ? Object.assign({}, item, { volume_percent: value }) : item);
        return {
          snapshot: snapshotPayload({ outputs }),
          payload: {
            operation: "set_output_volume", resource_id: "aout-aaa", outcome: "applied",
            requested: { volume_percent: value }, observed: { volume_percent: value },
            message: "the volume is now " + value + "%", output: outputs[0],
            observed_at: "2026-08-05T12:00:01.000Z"
          }
        };
      }
    },
    /* A host that accepted the command and did nothing. */
    ignored: {
      result(body) {
        return {
          payload: {
            operation: "set_output_volume", resource_id: "aout-aaa", outcome: "not_applied",
            requested: { volume_percent: body.volume_percent },
            observed: { volume_percent: 50 },
            message: "the volume was set to " + body.volume_percent +
              "% but this output reports 50%",
            output: OUTPUT_A, observed_at: "2026-08-05T12:00:01.000Z"
          }
        };
      }
    },
    /* A default-output switch where the playing stream stayed put. */
    partial: {
      result() {
        const outputs = [
          Object.assign({}, OUTPUT_A, { is_default: false }),
          Object.assign({}, OUTPUT_B, { is_default: true })
        ];
        return {
          snapshot: snapshotPayload({ outputs, defaultId: "aout-bbb" }),
          payload: {
            operation: "set_default_audio_output", resource_id: "aout-bbb",
            outcome: "partially_applied", requested: { default: true },
            observed: { is_default: true },
            message: "new sound will now play through this output, but audio that was " +
              "already playing stayed where it was",
            output: outputs[1], observed_at: "2026-08-05T12:00:01.000Z",
            streams: { already_playing: true, moved: [], stayed: [{}], verified: true }
          }
        };
      }
    },
    mute: {
      result() {
        const outputs = [Object.assign({}, OUTPUT_A, { muted: true }), OUTPUT_B];
        return {
          snapshot: snapshotPayload({ outputs }),
          payload: {
            operation: "set_output_mute", resource_id: "aout-aaa", outcome: "applied",
            requested: { muted: true }, observed: { muted: true },
            message: "this output is muted", output: outputs[0],
            observed_at: "2026-08-05T12:00:01.000Z"
          }
        };
      }
    }
  };

  function mount(behaviour) {
    return audio.mount({ api: makeApi(behaviour), el, escapeHtml });
  }

  function html() { return el("audioSections").innerHTML; }

  if (scenario === "renders") {
    return mount(behaviours.applied).then(function () {
      return {
        html: html(),
        requests: record.requests
      };
    });
  }

  if (scenario === "double-submit") {
    /* Two taps on Mute with no time between them. The second must not produce
       a second request — the panel is busy and says so. */
    return mount(behaviours.mute).then(function () {
      fire("click", button("audioMute"));
      fire("click", button("audioMute"));
      fire("click", button("audioMute"));
      const duringHtml = html();
      return drain().then(function () {
        return {
          putCount: record.requests.filter((r) => r.method === "PUT").length,
          duringHtml,
          html: html()
        };
      });
    });
  }

  if (scenario === "no-optimistic") {
    /* The server says 25 was requested and 50 is real. The panel must show 50. */
    return mount(behaviours.ignored).then(function () {
      fire("change", slider(25));
      return drain().then(function () {
        return { html: html(), requests: record.requests };
      });
    });
  }

  if (scenario === "observed-output") {
    return mount(behaviours.partial).then(function () {
      fire("click", outputButton("aout-bbb"));
      return drain().then(function () {
        return { html: html(), requests: record.requests };
      });
    });
  }

  if (scenario === "refused") {
    return mount({ refuse: "the volume must be between 0 and 100 percent" }).then(function () {
      fire("change", slider(90));
      return drain().then(function () { return { html: html() }; });
    });
  }

  if (scenario === "streams-unavailable") {
    return mount({
      initial: snapshotPayload({ streamsUnavailable: true }),
      result: behaviours.mute.result
    }).then(function () {
      const before = html();
      fire("click", button("audioMute"));
      return drain().then(function () {
        return { html: before, afterMuteHtml: html(),
                 putCount: record.requests.filter((r) => r.method === "PUT").length };
      });
    });
  }

  if (scenario === "pending-bound") {
    /* A request that never answers. The panel must come back on its own. */
    return mount({ hang: true }).then(function () {
      fire("click", button("audioMute"));
      const stuckHtml = html();
      advance(60000);
      return drain().then(function () { return { stuckHtml, html: html() }; });
    });
  }

  if (scenario === "no-default") {
    return mount({
      initial: snapshotPayload({ defaultId: null, outputs: [OUTPUT_B] }),
      result: behaviours.mute.result
    }).then(function () {
      return { html: html() };
    });
  }

  return Promise.resolve({ error: "unknown scenario: " + scenario });
}

run().then(function (result) {
  result.timerErrors = record.timerErrors;
  process.stdout.write(JSON.stringify(result));
}).catch(function (error) {
  process.stdout.write(JSON.stringify({
    uncaught: String((error && error.stack) || error),
    timerErrors: record.timerErrors
  }));
});
