/* A minimal browser stub for exercising web/youtube.js outside a browser.
 *
 * Fourth of its kind, beside pwa_harness.js, audio_harness.js and
 * spotify_harness.js, and separate for the same reason those are separate from
 * each other: entangling them would make each harder to read than any is alone.
 * This one injects the same `deps` contract app.js passes at mount time —
 * `api`, `el`, `escapeHtml` — so youtube.js runs exactly as it ships.
 *
 * The properties under test are behavioural, and none of them is visible to a
 * structural scan of the source: that a second tap sends no second request, that
 * a refused or unobserved action never renders as done, that a request which
 * never answers still gives the panel back, that a hidden tab stops polling, and
 * — the one this milestone cares most about — that an older poll response can
 * never overwrite a newer verified one.
 *
 * Time is fake and advanced explicitly, so every bound is tested
 * deterministically rather than by waiting.
 *
 * Usage:  node tests/youtube_harness.js <scenario>   -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const scenario = process.argv[2];
const ROOT = path.resolve(__dirname, "..");
const YOUTUBE_JS = fs.readFileSync(path.join(ROOT, "web", "youtube.js"), "utf8");
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
      if (selector === ".yt-volume-value") {
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

const documentStub = { visibilityState: "visible" };

/* Enough of AbortController for youtube.js: a signal object, an `aborted` flag,
   and a listener the scripted server can honour. The generation guard is what
   makes ordering correct; aborting is the optimisation on top of it. */
function AbortControllerStub() {
  const self = this;
  this.signal = { aborted: false };
  this.abort = function () {
    self.signal.aborted = true;
    if (self.signal.onabort) { self.signal.onabort(); }
  };
}

function fire(type, target) {
  const panel = el("youtubePanel");
  (panel.listeners[type] || []).forEach((fn) => fn({ target }));
}

/* `closest` is used by the panel's delegated click handler for queue removal.
   A button that is not a queue-remove control returns null, exactly as a real
   element would. */
function button(id) {
  return {
    id,
    value: "",
    closest() { return null; },
    getAttribute() { return null; }
  };
}

function removeButton(handle) {
  const node = {
    id: "",
    value: "",
    getAttribute(name) {
      return name === "data-remove-queue-item" ? handle : null;
    }
  };
  node.closest = function (selector) {
    return selector === "[data-remove-queue-item]" ? node : null;
  };
  return node;
}

function slider(value) {
  return { id: "youtubeVolume", value: String(value), closest() { return null; } };
}

/* ----------------------------------------------------------------- payloads */

const VIDEO = {
  video_handle: "ytv-aaaaaaaa",
  title: "Gönül Dağı",
  channel: "Neşet Ertaş",
  published: "2011-03-04"
};

function queueItem(handle, title) {
  return {
    queue_item_id: handle,
    video_handle: "ytv-" + handle,
    title: title,
    channel: "A channel",
    published: null
  };
}

function playerPayload(options) {
  const settings = options || {};
  const state = settings.connection_state || "ready";
  return {
    version: 1,
    observed_at: settings.observed_at || "2026-08-06T12:00:00.000Z",
    connection: {
      state: state,
      connected: state === "ready",
      identity_basis: "player_heartbeat",
      player_resource_id: state === "ready" ? "ytp-aaaa" : null
    },
    current: {
      result_handle: settings.result_handle === undefined ? "r0" : settings.result_handle,
      video: settings.video === undefined ? VIDEO : settings.video,
      playback_state: settings.playback_state || "playing",
      current_time_seconds: settings.current_time === undefined ? 61 : settings.current_time,
      duration_seconds: settings.duration === undefined ? 240 : settings.duration
    },
    volume: {
      volume_percent: settings.volume === undefined ? 50 : settings.volume,
      muted: settings.muted === true,
      scope: "youtube_player_only"
    },
    queue: {
      length: (settings.queue || []).length,
      max_length: 25,
      index: settings.queue_index === undefined ? 0 : settings.queue_index,
      items: settings.queue || []
    },
    capabilities: {
      play_search_result: true, queue_search_result: true,
      pause: true, resume: true, next: true, previous: true,
      set_volume: true, mute: true, seek: false,
      automatic_queue_continuation: false
    },
    limitations: [
      "Volume and mute here are the YouTube player's own, not this computer's speaker."
    ],
    last_error: settings.last_error || null
  };
}

function actionPayload(outcome, note, player, extra) {
  return Object.assign({
    outcome: outcome,
    note: note,
    correlation_id: "ytop-000000000000",
    progress: { correlation_id: "ytop-000000000000", started_at: "", elapsed_ms: 5, steps: [] },
    player: player
  }, extra || {});
}

/* The scripted server. GETs return the current snapshot so a re-read after an
   action is realistic; writes are described per scenario. */
function makeApi(behaviour) {
  let snapshot = behaviour.initial || playerPayload();
  return function api(pathname, options) {
    const settings = options || {};
    const method = settings.method || (settings.body !== undefined ? "POST" : "GET");
    record.requests.push({ method, path: pathname, body: settings.body || null });

    if (method === "GET") {
      if (behaviour.onGet) {
        const scripted = behaviour.onGet(pathname, snapshot);
        if (scripted) { return scripted; }
      }
      return Promise.resolve({ ok: true, status: 200, payload: snapshot });
    }
    if (behaviour.hang) { return new Promise(function () { /* never settles */ }); }
    if (behaviour.refuse) {
      return Promise.resolve({
        ok: false,
        status: behaviour.refuseStatus || 409,
        payload: { error: {
          code: behaviour.refuseCode || "youtube_player_not_connected",
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

/* ---------------------------------------------------------------- scenarios */

function run() {
  const sandbox = {
    console: {
      log: (...a) => record.consoleOutput.push(a.join(" ")),
      warn: (...a) => record.consoleOutput.push(a.join(" ")),
      error: (...a) => record.consoleOutput.push(a.join(" "))
    },
    document: documentStub,
    AbortController: AbortControllerStub,
    setTimeout: setTimeoutStub,
    clearTimeout: clearTimerStub,
    setInterval: setIntervalStub,
    clearInterval: clearTimerStub,
    Promise, Date, JSON, Math, isNaN, parseInt, parseFloat, encodeURIComponent,
    String, Object, Array, Error
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(YOUTUBE_JS, sandbox, { filename: "youtube.js" });

  const youtube = sandbox.CofferdamYouTube;

  function mount(behaviour) {
    return youtube.mount({ api: makeApi(behaviour), el, escapeHtml });
  }
  function html() { return el("youtubeSections").innerHTML; }
  function writes() { return record.requests.filter((r) => r.method !== "GET"); }

  /* -- rendering ---------------------------------------------------------- */

  if (scenario === "connected") {
    return mount({ result: () => ({ payload: {} }) }).then(function () {
      return { html: html(), requests: record.requests,
               observed: el("youtubeObserved").textContent };
    });
  }

  if (scenario === "player-closed") {
    return mount({
      initial: playerPayload({
        connection_state: "disconnected", video: null, result_handle: null,
        playback_state: "idle", volume: null, current_time: null, duration: null
      }),
      result: () => ({ payload: {} })
    }).then(function () { return { html: html() }; });
  }

  if (scenario === "unavailable") {
    return mount({
      initial: playerPayload({
        connection_state: "unavailable", video: null, playback_state: "idle"
      }),
      result: () => ({ payload: {} })
    }).then(function () { return { html: html() }; });
  }

  if (scenario === "autoplay-blocked") {
    return mount({
      initial: playerPayload({ playback_state: "autoplay_blocked" }),
      result: () => ({ payload: {} })
    }).then(function () { return { html: html() }; });
  }

  if (scenario === "queue-expanded") {
    const items = [queueItem("ytq-a", "First"), queueItem("ytq-b", "Second")];
    return mount({
      initial: playerPayload({ queue: items, queue_index: 0 }),
      result: () => ({ payload: {} })
    }).then(function () {
      const collapsed = html();
      fire("click", button("youtubeQueueToggle"));
      return drain().then(function () {
        return { collapsed, expanded: html() };
      });
    });
  }

  /* -- no false success --------------------------------------------------- */

  if (scenario === "refused-action-is-not-success") {
    return mount({
      refuse: "the YouTube player tab closed",
      refuseCode: "youtube_player_gone",
      refuseDetail: "press Play now again to open it once more",
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", button("youtubePlayPause"));
      return drain().then(function () {
        advance(1);
        return drain().then(function () {
          return { html: html(), writes: writes() };
        });
      });
    });
  }

  if (scenario === "partial-outcome-is-not-success") {
    /* The server observed the player and it did not do what was asked. The
       panel must repeat that, not upgrade it. */
    return mount({
      result: () => ({
        payload: actionPayload(
          "autoplay_blocked",
          "The video is loaded, and the browser will not start sound until the player " +
          "window on the workstation is clicked once.",
          playerPayload({ playback_state: "autoplay_blocked" })
        ),
        snapshot: playerPayload({ playback_state: "autoplay_blocked" })
      })
    }).then(function () {
      fire("click", button("youtubePlayPause"));
      return drain().then(function () { return { html: html() }; });
    });
  }

  /* -- one action at a time ----------------------------------------------- */

  if (scenario === "double-submission") {
    return mount({ hang: true, result: () => ({ payload: {} }) }).then(function () {
      const before = writes().length;
      fire("click", button("youtubePlayPause"));
      fire("click", button("youtubePlayPause"));
      fire("click", button("youtubePlayPause"));
      return drain().then(function () {
        return { before, after: writes().length, writes: writes() };
      });
    });
  }

  if (scenario === "hung-request-gives-the-panel-back") {
    return mount({ hang: true, result: () => ({ payload: {} }) }).then(function () {
      fire("click", button("youtubePlayPause"));
      return drain().then(function () {
        const during = html();
        advance(20000);       /* past ACTION_TIMEOUT_MS */
        return drain().then(function () {
          return { during, after: html() };
        });
      });
    });
  }

  /* -- response ordering: the property this milestone inherits ------------ */

  /* The exact sequence real validation found on the Spotify panel:
   *
   *   1. a periodic poll is issued and does not answer yet;
   *   2. the user acts; the write completes and returns the server's freshly
   *      *verified* state, which the panel adopts;
   *   3. only then does the poll from step 1 answer, describing the world as it
   *      was before the write.
   *
   * Ordering by arrival paints the old value back over the new one. The
   * generation guard is what makes step 3 a no-op. Both scenarios below build
   * that sequence deterministically: the first GET (mount's own) answers
   * immediately so the panel has a baseline, and the *second* one — the poll —
   * is the one held open.
   */
  function staleOverwriteScenario(oldSnapshot, newSnapshot, act, note) {
    let getCount = 0;
    let releasePoll = null;
    const held = new Promise(function (resolve) {
      releasePoll = function () {
        resolve({ ok: true, status: 200, payload: oldSnapshot });
      };
    });

    const api = makeApi({
      initial: oldSnapshot,
      onGet: function (pathname) {
        if (pathname.indexOf("/api/youtube/player") !== 0) { return null; }
        getCount += 1;
        /* The first read is mount's; the second is the poll we hold open. */
        return getCount === 2 ? held : null;
      },
      result: () => ({
        payload: actionPayload("applied", note, newSnapshot),
        snapshot: newSnapshot
      })
    });

    return youtube.mount({ api, el, escapeHtml }).then(function () {
      const baseline = html();
      /* Step 1: let the periodic poll fire. It will not answer. */
      advance(11000);
      return drain().then(function () {
        /* Step 2: the user acts, and the write returns verified state. */
        act();
        return drain(60).then(function () {
          const afterWrite = html();
          /* Step 3: the stale poll finally answers. */
          releasePoll();
          return drain(60).then(function () {
            return {
              baseline,
              afterWrite,
              afterStalePoll: html(),
              getCount
            };
          });
        });
      });
    });
  }

  if (scenario === "stale-poll-cannot-overwrite-a-newer-video") {
    return staleOverwriteScenario(
      playerPayload({ video: Object.assign({}, VIDEO, { title: "The old video" }) }),
      playerPayload({ video: Object.assign({}, VIDEO, { title: "The new video" }) }),
      function () { fire("click", button("youtubePlayPause")); },
      "Playing."
    );
  }

  if (scenario === "stale-poll-cannot-overwrite-a-newer-volume") {
    return staleOverwriteScenario(
      playerPayload({ volume: 50 }),
      playerPayload({ volume: 80 }),
      function () { fire("change", slider(80)); },
      "YouTube player volume is now 80%."
    );
  }

  if (scenario === "polling-pauses-during-a-write") {
    return mount({ hang: true, result: () => ({ payload: {} }) }).then(function () {
      const beforeIntervals = liveIntervals();
      fire("click", button("youtubePlayPause"));
      return drain().then(function () {
        const readsBefore = record.requests.filter(
          (r) => r.method === "GET" && r.path.indexOf("/api/youtube/player") === 0
        ).length;
        advance(11000);   /* past POLL_MS, still inside the action timeout */
        return drain().then(function () {
          const readsAfter = record.requests.filter(
            (r) => r.method === "GET" && r.path.indexOf("/api/youtube/player") === 0
          ).length;
          return { beforeIntervals, readsBefore, readsAfter };
        });
      });
    });
  }

  if (scenario === "poll-stops-while-hidden") {
    return mount({ result: () => ({ payload: {} }) }).then(function () {
      const afterMount = record.requests.length;
      documentStub.visibilityState = "hidden";
      advance(60000);
      return drain().then(function () {
        const whileHidden = record.requests.length;
        documentStub.visibilityState = "visible";
        advance(11000);
        return drain().then(function () {
          documentStub.visibilityState = "visible";
          return { afterMount, whileHidden, afterVisible: record.requests.length };
        });
      });
    });
  }

  if (scenario === "poll-stops-on-stop") {
    return mount({ result: () => ({ payload: {} }) }).then(function () {
      const mounted = record.requests.length;
      const intervalsWhileMounted = liveIntervals();
      youtube.stop();
      const intervalsAfterStop = liveIntervals();
      advance(180000);
      return drain().then(function () {
        return {
          mounted,
          intervalsWhileMounted,
          intervalsAfterStop,
          afterStop: record.requests.length,
          html: html(),
          connected: youtube.connected()
        };
      });
    });
  }

  /* -- the client's vocabulary -------------------------------------------- */

  if (scenario === "requests-carry-only-handles") {
    return mount({
      result: () => ({ payload: actionPayload("applied", "Playing.", playerPayload()) })
    }).then(function () {
      return youtube.playResult("search-abc", "r2").then(function () {
        return youtube.queueResult("search-abc", "r3").then(function () {
          fire("change", slider(35));
          return drain().then(function () {
            fire("click", button("youtubeMute"));
            return drain().then(function () {
              return { writes: writes() };
            });
          });
        });
      });
    });
  }

  if (scenario === "queue-removal-sends-the-handle") {
    const items = [queueItem("ytq-a", "First")];
    return mount({
      initial: playerPayload({ queue: items, queue_index: 0 }),
      result: () => ({ payload: actionPayload("applied", "Removed.", playerPayload()) })
    }).then(function () {
      fire("click", removeButton("ytq-a"));
      return drain().then(function () { return { writes: writes() }; });
    });
  }

  if (scenario === "volume-drag-does-not-send-per-pixel") {
    return mount({
      result: () => ({ payload: actionPayload("applied", "Volume set.", playerPayload({ volume: 70 })) })
    }).then(function () {
      fire("input", slider(20));
      fire("input", slider(40));
      fire("input", slider(70));
      return drain().then(function () {
        const duringDrag = writes().length;
        fire("change", slider(70));
        return drain().then(function () {
          return { duringDrag, afterCommit: writes().length, writes: writes() };
        });
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
