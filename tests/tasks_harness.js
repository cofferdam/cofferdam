/* A minimal browser stub for exercising web/tasks.js outside a browser.
 *
 * Fifth of its kind, beside pwa_harness.js, audio_harness.js, spotify_harness.js
 * and youtube_harness.js, and separate for the same reason those are separate
 * from each other: entangling them would make each harder to read than any is
 * alone. This one injects the same `deps` contract app.js passes at mount time —
 * `api`, `el`, `escapeHtml` — so tasks.js runs exactly as it ships.
 *
 * The properties under test are behavioural, and none is visible to a scan of
 * the source: that a double tap creates one task rather than two, that a stale
 * list response cannot paint over a newer one, that polling stops when the tab
 * is hidden and when the token is forgotten, that a terminal task offers no
 * action that could only be refused.
 *
 * Time is fake and advanced explicitly, so every bound is tested
 * deterministically rather than by waiting.
 *
 * Usage:  node tests/tasks_harness.js <scenario>   -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const scenario = process.argv[2];
const ROOT = path.resolve(__dirname, "..");
const TASKS_JS = fs.readFileSync(path.join(ROOT, "web", "tasks.js"), "utf8");
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
    open: false,
    listeners,
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener() {},
    querySelector() { return null; },
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

/* `document.getElementById` is used by tasks.js for the two textareas, whose
   values are read at submit time rather than tracked per keystroke. */
const documentStub = {
  visibilityState: "visible",
  getElementById(id) { return elements[id] || null; }
};

function AbortControllerStub() {
  const self = this;
  this.signal = { aborted: false };
  this.abort = function () {
    self.signal.aborted = true;
    if (self.signal.onabort) { self.signal.onabort(); }
  };
}

function fire(type, target) {
  const panel = el("tasksPanel");
  (panel.listeners[type] || []).forEach((fn) => fn({ target }));
}

function button(id) {
  return { id, value: "", closest() { return null; }, getAttribute() { return null; } };
}

function openButton(taskId) {
  const node = {
    id: "",
    value: "",
    getAttribute(name) { return name === "data-task-open" ? taskId : null; }
  };
  node.closest = function (selector) {
    return selector === "[data-task-open]" ? node : null;
  };
  return node;
}

function field(id, value) {
  const node = elements[id] || (elements[id] = makeElement(id));
  node.value = value;
  return node;
}

/* ----------------------------------------------------------------- payloads */

const CAPABILITIES = {
  start: true, followup: true, cancel: true, recover_after_restart: false,
  structured_progress: true, final_result: true, approvals: false,
  authentication_waits: false
};

function taskPayload(options) {
  const settings = options || {};
  const state = settings.state || "running";
  const terminal = ["completed", "failed", "cancelled", "interrupted"].indexOf(state) !== -1;
  const bucket = state === "waiting_for_user" || state === "recovery_required"
    ? "waiting"
    : (terminal ? "finished" : "active");
  return {
    version: 1,
    task_id: settings.task_id || "task_01hzzzzzzzzzzzzzzzzzzzzzzz",
    correlation_id: "tcor-0000000000000000",
    parent_task_id: null,
    origin: "pwa",
    adapter_id: "validation",
    adapter_display_name: "Validation task adapter",
    project_id: "demo",
    project_display_name: "Demo project",
    state: state,
    bucket: bucket,
    terminal: terminal,
    waiting_reason: settings.waiting_reason || (state === "waiting_for_user" ? "clarification" : null),
    lifecycle_revision: settings.revision || 3,
    created_at: "2026-08-06T12:00:00.000Z",
    started_at: "2026-08-06T12:00:01.000Z",
    updated_at: "2026-08-06T12:00:02.000Z",
    completed_at: terminal ? "2026-08-06T12:00:03.000Z" : null,
    title: settings.title || "Bir görev",
    latest_activity: settings.activity === undefined ? "Step 2 of 2." : settings.activity,
    failure: settings.failure || null,
    cancellation: null,
    adapter_capabilities: settings.capabilities || CAPABILITIES,
    event_cursor: 8,
    resource_summary: { evidence_reported: 0 },
    limitations: ["Task Core runs no shell, no process and no model."],
    latest_meaningful_output: settings.output || null,
    final_result: settings.result || null,
    prompt: settings.prompt || null
  };
}

function listPayload(tasks) {
  const counts = {};
  (tasks || []).forEach(function (task) {
    counts[task.state] = (counts[task.state] || 0) + 1;
  });
  return { version: 1, tasks: tasks || [], counts: counts };
}

function adaptersPayload(options) {
  const settings = options || {};
  if (settings.empty) { return { adapters: [] }; }
  return {
    adapters: [
      {
        adapter_id: "validation",
        display_name: "Validation task adapter",
        description: "A deterministic test adapter. It runs no program, calls no model.",
        available: true,
        unavailable_reason: null,
        capabilities: CAPABILITIES,
        validation_only: true,
        scenarios: [
          { scenario: "complete", description: "Completes." },
          { scenario: "wait", description: "Waits." }
        ]
      }
    ]
  };
}

function projectsPayload(options) {
  const settings = options || {};
  if (settings.empty) {
    return { projects: [], configured: 0, problems: [], source_present: false };
  }
  return {
    projects: [
      { project_id: "demo", display_name: "Demo project", enabled: true,
        adapters: ["validation"], notes: null }
    ],
    configured: 1,
    problems: [],
    source_present: true
  };
}

function eventsPayload(taskId, events) {
  return {
    task_id: taskId,
    events: events || [],
    cursor: events && events.length ? events[events.length - 1].sequence : 0,
    event_cursor: events ? events.length : 0
  };
}

function eventItem(sequence, type, text, evidence) {
  return {
    task_id: "task_01hzzzzzzzzzzzzzzzzzzzzzzz",
    sequence: sequence,
    event_type: type,
    created_at: "2026-08-06T12:00:0" + (sequence % 10) + ".000Z",
    actor: "adapter",
    source: "adapter",
    lifecycle_revision: 3,
    correlation_id: "tcor-0000000000000000",
    state: null,
    text: text,
    detail: null,
    evidence: evidence || []
  };
}

/* The scripted server. GETs answer from the current catalogues and list; writes
   are described per scenario. */
function makeApi(behaviour) {
  let list = behaviour.initial || listPayload([]);
  let detail = behaviour.detail || null;
  let events = behaviour.events || [];

  return function api(pathname, options) {
    const settings = options || {};
    const method = settings.method || (settings.body !== undefined ? "POST" : "GET");
    record.requests.push({ method, path: pathname, body: settings.body || null });

    if (method === "GET") {
      if (behaviour.onGet) {
        const scripted = behaviour.onGet(pathname, list);
        if (scripted) { return scripted; }
      }
      if (pathname.indexOf("/api/task-adapters") === 0) {
        return Promise.resolve({ ok: true, status: 200, payload: adaptersPayload(behaviour) });
      }
      if (pathname.indexOf("/api/task-projects") === 0) {
        return Promise.resolve({ ok: true, status: 200, payload: projectsPayload(behaviour) });
      }
      if (pathname.indexOf("/events") !== -1) {
        return Promise.resolve({
          ok: true, status: 200, payload: eventsPayload("t", events)
        });
      }
      if (pathname.indexOf("/api/tasks/") === 0) {
        return Promise.resolve({
          ok: true, status: 200, payload: { task: detail || taskPayload() }
        });
      }
      return Promise.resolve({ ok: true, status: 200, payload: list });
    }

    if (behaviour.hang) { return new Promise(function () { /* never settles */ }); }
    if (behaviour.refuse) {
      return Promise.resolve({
        ok: false,
        status: behaviour.refuseStatus || 409,
        payload: { error: {
          code: behaviour.refuseCode || "task_already_finished",
          message: behaviour.refuse,
          detail: behaviour.refuseDetail || null
        } }
      });
    }
    const result = behaviour.result(settings.body, pathname, method);
    if (result.list) { list = result.list; }
    if (result.detail) { detail = result.detail; }
    if (result.events) { events = result.events; }
    return Promise.resolve({
      ok: true, status: result.status || 200, payload: result.payload
    });
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
    navigator: {},
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
  vm.runInContext(TASKS_JS, sandbox, { filename: "tasks.js" });

  const tasks = sandbox.CofferdamTasks;

  function mount(behaviour) {
    return tasks.mount({ api: makeApi(behaviour), el, escapeHtml });
  }
  function html() { return el("tasksSections").innerHTML; }
  function writes() { return record.requests.filter((r) => r.method !== "GET"); }

  /* -- rendering ---------------------------------------------------------- */

  if (scenario === "empty") {
    return mount({ result: () => ({ payload: {} }) }).then(function () {
      return { html: html() };
    });
  }

  if (scenario === "list-groups") {
    return mount({
      initial: listPayload([
        taskPayload({ task_id: "task_a", state: "running", title: "Çalışan görev" }),
        taskPayload({ task_id: "task_b", state: "waiting_for_user", title: "Bekleyen görev" }),
        taskPayload({ task_id: "task_c", state: "completed", title: "Biten görev" }),
        taskPayload({ task_id: "task_d", state: "interrupted", title: "Kesilen görev" }),
        taskPayload({ task_id: "task_e", state: "failed", title: "Başarısız görev" })
      ]),
      result: () => ({ payload: {} })
    }).then(function () { return { html: html() }; });
  }

  if (scenario === "no-projects-or-adapters") {
    return mount({
      empty: true,
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", button("taskCompose"));
      return drain().then(function () { return { html: html() }; });
    });
  }

  if (scenario === "composer-labels-the-validation-adapter") {
    return mount({ result: () => ({ payload: {} }) }).then(function () {
      fire("click", button("taskCompose"));
      return drain().then(function () { return { html: html() }; });
    });
  }

  /* -- detail ------------------------------------------------------------- */

  if (scenario === "detail-waiting-offers-followup") {
    return mount({
      initial: listPayload([taskPayload({ task_id: "task_w", state: "waiting_for_user" })]),
      detail: taskPayload({
        task_id: "task_w", state: "waiting_for_user", prompt: "Ne yapmalı?"
      }),
      events: [eventItem(1, "task_created", null), eventItem(2, "waiting_for_user", "Waiting.")],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_w"));
      return drain().then(function () { return { html: html() }; });
    });
  }

  if (scenario === "detail-terminal-hides-invalid-actions") {
    return mount({
      initial: listPayload([taskPayload({ task_id: "task_done", state: "completed" })]),
      detail: taskPayload({
        task_id: "task_done", state: "completed", result: "Bitti.", prompt: "Yap"
      }),
      events: [eventItem(1, "task_created", null)],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_done"));
      return drain().then(function () { return { html: html() }; });
    });
  }

  if (scenario === "detail-interrupted-is-distinct") {
    return mount({
      initial: listPayload([taskPayload({ task_id: "task_i", state: "interrupted" })]),
      detail: taskPayload({
        task_id: "task_i", state: "interrupted", output: "Halfway there."
      }),
      events: [eventItem(1, "task_interrupted", "Cofferdam restarted.")],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_i"));
      return drain().then(function () { return { html: html() }; });
    });
  }

  if (scenario === "detail-failed-is-distinct") {
    return mount({
      initial: listPayload([taskPayload({ task_id: "task_f", state: "failed" })]),
      detail: taskPayload({
        task_id: "task_f",
        state: "failed",
        failure: { code: "validation_scenario_failed", message: "It failed on purpose.", detail: null }
      }),
      events: [eventItem(1, "task_failed", "Failed.")],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_f"));
      return drain().then(function () { return { html: html() }; });
    });
  }

  if (scenario === "detail-is-not-a-terminal-log") {
    return mount({
      initial: listPayload([taskPayload({ task_id: "task_v", state: "completed" })]),
      detail: taskPayload({ task_id: "task_v", state: "completed", result: "Done." }),
      events: [
        eventItem(1, "task_created", null),
        eventItem(2, "progress", "tick one"),
        eventItem(3, "progress", "tick two"),
        eventItem(4, "meaningful_output", "the real output", [
          { evidence_type: "commit", source: "adapter_reported", verified: false,
            identifier: "deadbeef", operation: "made", result: "ok", observed_at: null },
          { evidence_type: "file", source: "cofferdam_action", verified: true,
            identifier: "notes.md", operation: "wrote", result: "ok", observed_at: null }
        ])
      ],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_v"));
      return drain().then(function () { return { html: html() }; });
    });
  }

  /* -- one action at a time ------------------------------------------------ */

  if (scenario === "double-tap-creates-one-task") {
    return mount({
      hang: true,
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", button("taskCompose"));
      return drain().then(function () {
        field("taskPrompt", "iki kere basıyorum");
        fire("input", field("taskPrompt", "iki kere basıyorum"));
        const before = writes().length;
        fire("click", button("taskStart"));
        fire("click", button("taskStart"));
        fire("click", button("taskStart"));
        return drain().then(function () {
          return { before, after: writes().length, writes: writes() };
        });
      });
    });
  }

  if (scenario === "retries-reuse-one-request-id") {
    /* The key is minted once per attempt, so a repeat carries the same value —
       which is what lets the server recognise it as one request. */
    let bodies = [];
    return mount({
      result: (body) => {
        bodies.push(body);
        return {
          status: 201,
          payload: { task: taskPayload({ state: "completed" }), created: true },
          list: listPayload([taskPayload({ state: "completed" })])
        };
      }
    }).then(function () {
      fire("click", button("taskCompose"));
      return drain().then(function () {
        fire("input", field("taskPrompt", "ilk deneme"));
        fire("click", button("taskStart"));
        return drain(80).then(function () {
          return { bodies: bodies, writes: writes() };
        });
      });
    });
  }

  if (scenario === "hung-request-gives-the-panel-back") {
    return mount({ hang: true, result: () => ({ payload: {} }) }).then(function () {
      fire("click", button("taskCompose"));
      return drain().then(function () {
        fire("input", field("taskPrompt", "asla cevap vermeyecek"));
        fire("click", button("taskStart"));
        return drain().then(function () {
          const during = html();
          advance(50000);
          return drain().then(function () {
            return { during, after: html() };
          });
        });
      });
    });
  }

  if (scenario === "refused-create-is-not-success") {
    return mount({
      refuse: "that project is not configured on this workstation",
      refuseCode: "task_project_unknown",
      refuseStatus: 404,
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", button("taskCompose"));
      return drain().then(function () {
        fire("input", field("taskPrompt", "olmayan proje"));
        fire("click", button("taskStart"));
        return drain(60).then(function () {
          return { html: html(), writes: writes() };
        });
      });
    });
  }

  /* -- response ordering --------------------------------------------------- */

  if (scenario === "stale-list-cannot-overwrite-a-newer-task") {
    const oldList = listPayload([taskPayload({ task_id: "task_x", state: "running", title: "The old title" })]);
    const newTask = taskPayload({ task_id: "task_x", state: "completed", title: "The new title" });
    const newList = listPayload([newTask]);

    let getCount = 0;
    let releasePoll = null;
    const held = new Promise(function (resolve) {
      releasePoll = function () { resolve({ ok: true, status: 200, payload: oldList }); };
    });

    const api = makeApi({
      initial: oldList,
      onGet: function (pathname) {
        if (pathname !== "/api/tasks") { return null; }
        getCount += 1;
        /* The first list read is mount's; the second is the poll we hold open. */
        return getCount === 2 ? held : null;
      },
      result: () => ({
        status: 201,
        payload: { task: newTask, created: true },
        list: newList,
        detail: newTask
      })
    });

    return tasks.mount({ api, el, escapeHtml }).then(function () {
      const baseline = html();
      advance(11000);      /* the periodic poll fires and does not answer */
      return drain().then(function () {
        fire("click", button("taskCompose"));
        return drain().then(function () {
          fire("input", field("taskPrompt", "yeni görev"));
          fire("click", button("taskStart"));
          return drain(80).then(function () {
            const afterWrite = html();
            releasePoll();
            return drain(80).then(function () {
              return { baseline, afterWrite, afterStalePoll: html(), getCount };
            });
          });
        });
      });
    });
  }

  if (scenario === "poll-stops-while-hidden") {
    return mount({ result: () => ({ payload: {} }) }).then(function () {
      const afterMount = record.requests.length;
      documentStub.visibilityState = "hidden";
      advance(120000);
      return drain().then(function () {
        const whileHidden = record.requests.length;
        documentStub.visibilityState = "visible";
        advance(11000);
        return drain().then(function () {
          return { afterMount, whileHidden, afterVisible: record.requests.length };
        });
      });
    });
  }

  if (scenario === "poll-stops-on-stop") {
    return mount({
      initial: listPayload([taskPayload({ state: "completed", title: "Gizli görev" })]),
      result: () => ({ payload: {} })
    }).then(function () {
      const mounted = record.requests.length;
      const intervalsWhileMounted = liveIntervals();
      tasks.stop();
      const intervalsAfterStop = liveIntervals();
      advance(300000);
      return drain().then(function () {
        return {
          mounted,
          intervalsWhileMounted,
          intervalsAfterStop,
          afterStop: record.requests.length,
          html: html()
        };
      });
    });
  }

  /* -- follow-up and cancel ------------------------------------------------ */

  if (scenario === "followup-sends-the-answer") {
    const waiting = taskPayload({ task_id: "task_w", state: "waiting_for_user" });
    return mount({
      initial: listPayload([waiting]),
      detail: waiting,
      events: [eventItem(1, "waiting_for_user", "Waiting.")],
      result: () => ({
        payload: { task: taskPayload({ task_id: "task_w", state: "completed", result: "Bitti." }) },
        detail: taskPayload({ task_id: "task_w", state: "completed", result: "Bitti." })
      })
    }).then(function () {
      fire("click", openButton("task_w"));
      return drain().then(function () {
        field("taskFollowupText", "evet, devam et");
        fire("click", button("taskFollowupSend"));
        return drain(80).then(function () {
          return { html: html(), writes: writes() };
        });
      });
    });
  }

  if (scenario === "cancel-repeats-the-observed-state") {
    const running = taskPayload({ task_id: "task_c", state: "running" });
    const cancelling = taskPayload({ task_id: "task_c", state: "cancelling" });
    return mount({
      initial: listPayload([running]),
      detail: running,
      events: [eventItem(1, "task_started", "Running.")],
      result: () => ({ payload: { task: cancelling }, detail: cancelling })
    }).then(function () {
      fire("click", openButton("task_c"));
      return drain().then(function () {
        fire("click", button("taskCancel"));
        return drain(80).then(function () {
          return { html: html(), writes: writes() };
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
