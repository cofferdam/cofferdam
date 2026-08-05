/* A minimal browser stub for exercising web/spotify.js outside a browser.
 *
 * Third of its kind, beside pwa_harness.js and audio_harness.js, and separate
 * for the same reason those two are separate from each other: entangling them
 * would make each harder to read than any is alone. This one injects the same
 * `deps` contract app.js passes at mount time — `api`, `el`, `escapeHtml` — so
 * spotify.js runs exactly as it ships.
 *
 * The properties under test are behavioural, and none of them is visible to a
 * structural scan of the source: that a second tap sends no second request, that
 * a refused or unobserved action never renders as done, that a request which
 * never answers still gives the panel back, that a hidden tab stops polling, and
 * that a pending authorization expires instead of hanging.
 *
 * Time is fake and advanced explicitly, so every bound is tested deterministically
 * rather than by waiting.
 *
 * Usage:  node tests/spotify_harness.js <scenario>   -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const scenario = process.argv[2];
const ROOT = path.resolve(__dirname, "..");
const SPOTIFY_JS = fs.readFileSync(path.join(ROOT, "web", "spotify.js"), "utf8");
const INDEX_HTML = fs.readFileSync(path.join(ROOT, "web", "index.html"), "utf8");

/* Element ids come from the real index.html, so the stub cannot drift into
   providing something the shipped page does not have. */
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
function liveIntervals() {
  return timers.filter((t) => t.interval).length;
}

/* Promise callbacks live on the microtask queue, which the fake clock knows
   nothing about. A scenario that read the DOM straight after firing an event
   would catch the panel mid-flight — disabled, request still in the air — and
   quietly assert the wrong thing. */
function drain(turns) {
  let chain = Promise.resolve();
  for (let i = 0; i < (turns || 30); i += 1) { chain = chain.then(() => {}); }
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

const record = { requests: [], timerErrors: [], consoleOutput: [], uncaught: null };

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
    querySelector(selector) {
      if (selector === ".sp-volume-value") {
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

const documentStub = { hidden: false };

function fire(type, target) {
  const panel = el("spotifyPanel");
  (panel.listeners[type] || []).forEach((fn) => fn({ target }));
}

function button(id) { return { id, value: "", getAttribute() { return null; } }; }
function slider(value) { return { id: "spotifyVolume", value: String(value) }; }
function devicePicker(value) { return { id: "spotifyDevice", value: String(value) }; }

/* ----------------------------------------------------------------- payloads */

const DEVICE_LAPTOP = {
  resource_id: "spdev-aaa",
  name: "Workstation",
  device_type: "computer",
  is_active: true,
  is_restricted: false,
  is_private_session: false,
  volume_percent: 60,
  supports_volume: true,
  controllable: true,
  identity_stability: "provider_session"
};
const DEVICE_SPEAKER = Object.assign({}, DEVICE_LAPTOP, {
  resource_id: "spdev-bbb", name: "Kitchen", device_type: "speaker",
  is_active: false, volume_percent: 20
});
/* A car head unit is the realistic restricted device: Spotify documents
   `is_restricted` as accepting no Web API commands at all. */
const DEVICE_RESTRICTED = Object.assign({}, DEVICE_LAPTOP, {
  resource_id: "spdev-ccc", name: "Car", device_type: "automobile",
  is_active: false, is_restricted: true, controllable: false,
  supports_volume: false, volume_percent: null
});

const NOW_PLAYING = {
  item_type: "track",
  track_id: "3n3Ppam7vgaVa1iaRUc9Lp",
  title: "Gönül Dağı",
  artists: ["Neşet Ertaş"],
  album: "Gönül Dağı",
  duration_ms: 240000,
  explicit: false
};

function connectionPayload(status, extra) {
  return Object.assign({
    status: status,
    scopes: ["user-read-playback-state", "user-read-currently-playing",
             "user-modify-playback-state"],
    display_name: "Efe",
    connected_at: "2026-08-05T11:00:00.000Z",
    required_scopes: ["user-read-playback-state", "user-read-currently-playing",
                      "user-modify-playback-state"],
    missing_scopes: [],
    detail: null
  }, extra || {});
}

function playbackPayload(options) {
  const settings = options || {};
  const devices = settings.devices === undefined
    ? [DEVICE_LAPTOP, DEVICE_SPEAKER] : settings.devices;
  const active = devices.filter((d) => d.is_active)[0] || null;
  return {
    version: 1,
    observed_at: settings.observed_at || "2026-08-05T12:00:00.000Z",
    connection: settings.connection || connectionPayload("connected"),
    playback_available: settings.playback_available !== false,
    is_playing: settings.is_playing !== false,
    progress_ms: settings.progress_ms === undefined ? 61000 : settings.progress_ms,
    repeat_state: "off",
    shuffle_state: false,
    active_device_resource_id: active ? active.resource_id : null,
    devices_available: settings.devices_available !== false,
    devices: devices,
    now_playing: settings.now_playing === undefined ? NOW_PLAYING : settings.now_playing,
    muted_by_cofferdam: settings.muted_by_cofferdam === true,
    restore_volume_known: settings.restore_volume_known === true,
    capabilities: settings.capabilities || {
      transport: !!active && !active.is_restricted,
      volume: !!active && !active.is_restricted && active.supports_volume === true,
      mute: !!active && !active.is_restricted && active.supports_volume === true,
      transfer: true,
      play_result: true,
      queue_result: true
    },
    limitations: ["Spotify publishes no mute operation, so muting sets the device volume to " +
                  "zero and remembers the level to restore"],
    warnings: settings.warnings || [],
    authorization: settings.authorization || { pending: false, expires_in_seconds: null,
                                               last_outcome: null }
  };
}

/* The scripted server. GETs always return the current snapshot so a re-read
   after an action is realistic; writes are described per scenario. */
function makeApi(behaviour) {
  let snapshot = behaviour.initial || playbackPayload();
  return function api(pathname, options) {
    const settings = options || {};
    const method = settings.method || (settings.body !== undefined ? "POST" : "GET");
    record.requests.push({ method, path: pathname, body: settings.body || null });

    if (method === "GET") {
      return Promise.resolve({ ok: true, status: 200, payload: snapshot });
    }
    if (behaviour.hang) { return new Promise(function () { /* never settles */ }); }
    if (behaviour.refuse) {
      return Promise.resolve({
        ok: false,
        status: behaviour.refuseStatus || 409,
        payload: { error: {
          code: behaviour.refuseCode || "spotify_no_active_device",
          message: behaviour.refuse,
          detail: behaviour.refuseDetail || null
        } }
      });
    }
    const result = behaviour.result(settings.body, snapshot, pathname, method);
    if (result.snapshot) { snapshot = result.snapshot; }
    return Promise.resolve({ ok: true, status: 200, payload: result.payload });
  };
}

function actionPayload(operation, outcome, message, snapshot, extra) {
  return Object.assign({
    operation: operation,
    outcome: outcome,
    requested: {},
    observed: {},
    message: message,
    observed_at: "2026-08-05T12:00:01.000Z",
    playback: snapshot
  }, extra || {});
}

/* ---------------------------------------------------------------- scenarios */

function run() {
  const sandbox = {
    console: {
      log: (...a) => record.consoleOutput.push(a.join(" ")),
      warn: (...a) => record.consoleOutput.push(a.join(" ")),
      error: (...a) => record.consoleOutput.push(a.join(" "))
    },
    document: documentStub,
    setTimeout: setTimeoutStub,
    clearTimeout: clearTimerStub,
    setInterval: setIntervalStub,
    clearInterval: clearTimerStub,
    Promise, Date, JSON, Math, isNaN, parseInt, parseFloat, encodeURIComponent,
    String, Object, Array, Error
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SPOTIFY_JS, sandbox, { filename: "spotify.js" });

  const spotify = sandbox.CofferdamSpotify;

  function mount(behaviour) {
    return spotify.mount({ api: makeApi(behaviour), el, escapeHtml });
  }
  function html() { return el("spotifySections").innerHTML; }
  function writes() {
    return record.requests.filter((r) => r.method !== "GET");
  }

  /* -- connected, playing, everything available --------------------------- */

  if (scenario === "connected") {
    return mount({ result: () => ({ payload: {} }) }).then(function () {
      return { html: html(), requests: record.requests, observed: el("spotifyObserved").textContent };
    });
  }

  if (scenario === "disconnected") {
    return mount({
      initial: playbackPayload({
        connection: connectionPayload("disconnected", { display_name: null, connected_at: null,
                                                        scopes: [] }),
        playback_available: false, devices: [], now_playing: null
      }),
      result: () => ({ payload: {} })
    }).then(function () { return { html: html() }; });
  }

  if (scenario === "premium-required") {
    return mount({
      initial: playbackPayload({
        connection: connectionPayload("premium_required", {
          detail: "every Spotify player endpoint is documented as Premium-only" }),
        playback_available: false, devices: [], now_playing: null
      }),
      result: () => ({ payload: {} })
    }).then(function () { return { html: html() }; });
  }

  if (scenario === "missing-scopes") {
    return mount({
      initial: playbackPayload({
        connection: connectionPayload("missing_required_scopes", {
          scopes: ["user-read-playback-state"],
          missing_scopes: ["user-modify-playback-state"],
          detail: "reconnect the account to grant the missing permissions" }),
        playback_available: false, devices: [], now_playing: null
      }),
      result: () => ({ payload: {} })
    }).then(function () { return { html: html() }; });
  }

  if (scenario === "no-active-device") {
    const devices = [Object.assign({}, DEVICE_SPEAKER, { is_active: false })];
    return mount({
      initial: playbackPayload({ devices, playback_available: false, now_playing: null,
                                 is_playing: false }),
      result: () => ({ payload: {} })
    }).then(function () { return { html: html() }; });
  }

  if (scenario === "restricted-device") {
    return mount({
      initial: playbackPayload({
        devices: [Object.assign({}, DEVICE_RESTRICTED, { is_active: true })],
        now_playing: NOW_PLAYING
      }),
      result: () => ({ payload: {} })
    }).then(function () { return { html: html() }; });
  }

  /* -- authorization ------------------------------------------------------ */

  if (scenario === "authorize-start") {
    let started = false;
    const disconnected = playbackPayload({
      connection: connectionPayload("disconnected", { display_name: null, scopes: [] }),
      playback_available: false, devices: [], now_playing: null
    });
    const pendingSnapshot = playbackPayload({
      connection: connectionPayload("authorization_pending", { display_name: null, scopes: [] }),
      playback_available: false, devices: [], now_playing: null,
      authorization: { pending: true, expires_in_seconds: 280, last_outcome: null }
    });
    let current = disconnected;
    const api = function (pathname, options) {
      const settings = options || {};
      const method = settings.method || (settings.body !== undefined ? "POST" : "GET");
      record.requests.push({ method, path: pathname, body: settings.body || null });
      if (method === "GET") { return Promise.resolve({ ok: true, status: 200, payload: current }); }
      started = true;
      current = pendingSnapshot;
      return Promise.resolve({ ok: true, status: 200, payload: {
        pending: true, expires_in_seconds: 300,
        message: "Continue authorization in Opera on the workstation."
      } });
    };
    return spotify.mount({ api, el, escapeHtml }).then(function () {
      const before = html();
      fire("click", button("spotifyAuthorize"));
      return drain().then(function () {
        return { beforeHtml: before, html: html(), started, requests: record.requests,
                 intervals: liveIntervals() };
      });
    });
  }

  if (scenario === "authorize-expires") {
    /* The attempt the user walked away from. The server expires it and the panel
       must return to a state with a button, never sit pending forever. */
    let ticks = 0;
    const pendingSnapshot = playbackPayload({
      connection: connectionPayload("authorization_pending", { display_name: null, scopes: [] }),
      playback_available: false, devices: [], now_playing: null,
      authorization: { pending: true, expires_in_seconds: 12, last_outcome: null }
    });
    const timedOut = playbackPayload({
      connection: connectionPayload("disconnected", { display_name: null, scopes: [] }),
      playback_available: false, devices: [], now_playing: null,
      authorization: { pending: false, expires_in_seconds: null, last_outcome: {
        state: "timed_out",
        message: "Authorization was not completed in time. Nothing was changed.",
        at: "2026-08-05T12:05:00.000Z"
      } }
    });
    const api = function (pathname, options) {
      const settings = options || {};
      const method = settings.method || (settings.body !== undefined ? "POST" : "GET");
      record.requests.push({ method, path: pathname, body: settings.body || null });
      if (method === "GET") {
        ticks += 1;
        return Promise.resolve({ ok: true, status: 200,
                                 payload: ticks > 2 ? timedOut : pendingSnapshot });
      }
      return Promise.resolve({ ok: true, status: 200, payload: {} });
    };
    return spotify.mount({ api, el, escapeHtml }).then(function () {
      const pendingHtml = html();
      let chain = Promise.resolve();
      for (let i = 0; i < 6; i += 1) {
        chain = chain.then(() => { advance(4000); return drain(); });
      }
      return chain.then(function () {
        return { pendingHtml, html: html(), getCount: record.requests.length };
      });
    });
  }

  if (scenario === "authorize-cancel") {
    const pendingSnapshot = playbackPayload({
      connection: connectionPayload("authorization_pending", { display_name: null, scopes: [] }),
      playback_available: false, devices: [], now_playing: null,
      authorization: { pending: true, expires_in_seconds: 200, last_outcome: null }
    });
    const disconnected = playbackPayload({
      connection: connectionPayload("disconnected", { display_name: null, scopes: [] }),
      playback_available: false, devices: [], now_playing: null
    });
    let current = pendingSnapshot;
    const api = function (pathname, options) {
      const settings = options || {};
      const method = settings.method || (settings.body !== undefined ? "POST" : "GET");
      record.requests.push({ method, path: pathname, body: settings.body || null });
      if (method === "GET") { return Promise.resolve({ ok: true, status: 200, payload: current }); }
      current = disconnected;
      return Promise.resolve({ ok: true, status: 200, payload: { cancelled: true } });
    };
    return spotify.mount({ api, el, escapeHtml }).then(function () {
      fire("click", button("spotifyCancelAuth"));
      return drain().then(function () {
        return { html: html(), requests: record.requests };
      });
    });
  }

  /* -- transport ---------------------------------------------------------- */

  if (scenario === "pause-observed") {
    return mount({
      result(body, snapshot) {
        const next = playbackPayload({ is_playing: false });
        return { snapshot: next,
                 payload: actionPayload("spotify_pause", "applied", "Spotify is paused", next) };
      }
    }).then(function () {
      fire("click", button("spotifyPlayPause"));
      return drain().then(function () {
        return { html: html(), requests: record.requests };
      });
    });
  }

  if (scenario === "pause-not-observed") {
    /* Spotify answered 204 and kept playing. The panel must not say "paused". */
    return mount({
      result() {
        const next = playbackPayload({ is_playing: true });
        return { snapshot: next, payload: actionPayload(
          "spotify_pause", "not_applied", "Spotify is still playing", next) };
      }
    }).then(function () {
      fire("click", button("spotifyPlayPause"));
      return drain().then(function () { return { html: html() }; });
    });
  }

  if (scenario === "double-submit") {
    return mount({
      result() {
        const next = playbackPayload({ is_playing: false });
        return { snapshot: next,
                 payload: actionPayload("spotify_pause", "applied", "Spotify is paused", next) };
      }
    }).then(function () {
      fire("click", button("spotifyPlayPause"));
      fire("click", button("spotifyPlayPause"));
      fire("click", button("spotifyPlayPause"));
      const duringHtml = html();
      return drain().then(function () {
        return { writeCount: writes().length, duringHtml, html: html() };
      });
    });
  }

  if (scenario === "pending-bound") {
    return mount({ hang: true }).then(function () {
      fire("click", button("spotifyPlayPause"));
      const stuckHtml = html();
      advance(60000);
      return drain().then(function () { return { stuckHtml, html: html() }; });
    });
  }

  /* -- volume and mute ---------------------------------------------------- */

  if (scenario === "volume-observed") {
    return mount({
      result(body) {
        const devices = [Object.assign({}, DEVICE_LAPTOP, { volume_percent: body.volume_percent }),
                         DEVICE_SPEAKER];
        const next = playbackPayload({ devices });
        return { snapshot: next, payload: actionPayload(
          "spotify_set_volume", "applied",
          "Spotify volume is now " + body.volume_percent + "%", next) };
      }
    }).then(function () {
      fire("change", slider(25));
      return drain().then(function () {
        return { html: html(), requests: record.requests };
      });
    });
  }

  if (scenario === "volume-refused") {
    return mount({
      refuse: "that Spotify device does not support volume control",
      refuseCode: "spotify_volume_unsupported",
      refuseDetail: "the device reports supports_volume as false; use the device's own controls"
    }).then(function () {
      fire("change", slider(70));
      return drain().then(function () { return { html: html(), requests: record.requests }; });
    });
  }

  if (scenario === "mute") {
    return mount({
      result() {
        const devices = [Object.assign({}, DEVICE_LAPTOP, { volume_percent: 0 }), DEVICE_SPEAKER];
        const next = playbackPayload({ devices, muted_by_cofferdam: true,
                                       restore_volume_known: true });
        return { snapshot: next, payload: actionPayload(
          "spotify_set_mute", "applied",
          "Spotify is muted — Cofferdam set its volume to zero", next) };
      }
    }).then(function () {
      const before = html();
      fire("click", button("spotifyMute"));
      return drain().then(function () {
        return { beforeHtml: before, html: html(), requests: record.requests };
      });
    });
  }

  if (scenario === "unmute-unknown") {
    /* A device at zero that Cofferdam did not mute. Unmuting is refused rather
       than guessing a level, and the refusal is the whole point. */
    const devices = [Object.assign({}, DEVICE_LAPTOP, { volume_percent: 0 }), DEVICE_SPEAKER];
    return mount({
      initial: playbackPayload({ devices, muted_by_cofferdam: false,
                                 restore_volume_known: false }),
      refuse: "Cofferdam does not know what volume to restore",
      refuseCode: "spotify_unmute_restore_unknown",
      refuseDetail: "it did not perform the mute it is being asked to undo, so it will not " +
        "pick a level for you — set a volume directly instead"
    }).then(function () {
      fire("click", button("spotifyMute"));
      return drain().then(function () { return { html: html() }; });
    });
  }

  /* -- devices ------------------------------------------------------------ */

  if (scenario === "transfer") {
    return mount({
      result(body) {
        const devices = [Object.assign({}, DEVICE_LAPTOP, { is_active: false }),
                         Object.assign({}, DEVICE_SPEAKER, { is_active: true })];
        const next = playbackPayload({ devices });
        return { snapshot: next, payload: actionPayload(
          "spotify_transfer_playback", "applied",
          "Spotify is now playing through Kitchen", next, { system_audio_unchanged: true }) };
      }
    }).then(function () {
      fire("change", devicePicker("spdev-bbb"));
      fire("click", button("spotifyTransfer"));
      return drain().then(function () {
        return { html: html(), requests: record.requests };
      });
    });
  }

  if (scenario === "stale-device") {
    return mount({
      refuse: "that Spotify device is not available right now",
      refuseCode: "spotify_device_unknown",
      refuseStatus: 404,
      refuseDetail: "the device list has changed since this page loaded — refresh and retry"
    }).then(function () {
      fire("change", devicePicker("spdev-bbb"));
      fire("click", button("spotifyTransfer"));
      return drain().then(function () { return { html: html(), requests: record.requests }; });
    });
  }

  /* -- search-result playback -------------------------------------------- */

  if (scenario === "play-result") {
    return mount({
      result() {
        const next = playbackPayload({});
        return { snapshot: next, payload: actionPayload(
          "spotify_play_search_result", "applied", "Spotify is playing the track you chose",
          next) };
      }
    }).then(function () {
      return spotify.playResult("msrch-1", "mres-9").then(function (outcome) {
        return { outcome, requests: record.requests, html: html() };
      });
    });
  }

  if (scenario === "queue-result") {
    return mount({
      result() {
        const next = playbackPayload({});
        return { snapshot: next, payload: actionPayload(
          "spotify_queue_search_result", "accepted_by_provider",
          "Spotify accepted the track into the queue — what is playing now has not changed",
          next) };
      }
    }).then(function () {
      return spotify.queueResult("msrch-1", "mres-9").then(function (outcome) {
        return { outcome, requests: record.requests };
      });
    });
  }

  if (scenario === "play-result-expired") {
    return mount({
      refuse: "that search has expired",
      refuseCode: "media_search_expired",
      refuseStatus: 409,
      refuseDetail: "run the search again to pick a result"
    }).then(function () {
      return spotify.playResult("msrch-1", "mres-9").then(function (outcome) {
        return { outcome, requests: record.requests };
      });
    });
  }

  /* -- polling ------------------------------------------------------------ */

  if (scenario === "poll-hidden") {
    return mount({ result: () => ({ payload: {} }) }).then(function () {
      const afterMount = record.requests.length;
      documentStub.hidden = true;
      advance(120000);
      return drain().then(function () {
        const whileHidden = record.requests.length;
        documentStub.hidden = false;
        advance(60000);
        return drain().then(function () {
          documentStub.hidden = false;
          return { afterMount, whileHidden, afterVisible: record.requests.length };
        });
      });
    });
  }

  if (scenario === "poll-stops-on-stop") {
    return mount({ result: () => ({ payload: {} }) }).then(function () {
      const mounted = record.requests.length;
      const intervalsWhileMounted = liveIntervals();
      spotify.stop();
      const intervalsAfterStop = liveIntervals();
      advance(180000);
      return drain().then(function () {
        return {
          mounted,
          intervalsWhileMounted,
          intervalsAfterStop,
          afterStop: record.requests.length,
          html: html(),
          connected: spotify.connected()
        };
      });
    });
  }

  return Promise.resolve({ error: "unknown scenario: " + scenario });
}

run().then(function (result) {
  result.timerErrors = record.timerErrors;
  result.consoleOutput = record.consoleOutput;
  process.stdout.write(JSON.stringify(result));
}).catch(function (error) {
  process.stdout.write(JSON.stringify({
    uncaught: String((error && error.stack) || error),
    timerErrors: record.timerErrors,
    consoleOutput: record.consoleOutput
  }));
});
