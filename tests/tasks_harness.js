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

/* Which element currently has focus, and how many times a node the user was
   typing in was destroyed under them. A real browser answers both; the stub had
   to learn to, because the defect being tested is invisible without them. */
let activeElementId = null;
let destroyedFocusedNodes = 0;

function makeElement(id) {
  const listeners = {};
  const node = {
    id,
    hidden: false,
    textContent: "",
    disabled: false,
    value: "",
    open: false,
    selectionStart: 0,
    selectionEnd: 0,
    listeners,
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    getAttribute() { return null; },
    focus() { activeElementId = id; },
    blur() { if (activeElementId === id) { activeElementId = null; } }
  };

  /* The property this whole file exists to model honestly.
   *
   * Assigning `innerHTML` in a browser DESTROYS every descendant node and
   * builds new ones. A textarea inside gets a fresh node with an empty value,
   * whatever the user had typed into the old one, and focus goes with it.
   *
   * The stub used to memoise elements by id forever, so `innerHTML` assignment
   * changed nothing and a test could type into a textarea, poll ten times, and
   * still find the text there. That is exactly the bug it was supposed to
   * catch: on a phone the draft vanished. A double that cannot lose data
   * cannot test for data loss. */
  let markup = "";
  Object.defineProperty(node, "innerHTML", {
    get() { return markup; },
    set(value) {
      /* Everything the previous markup contained is gone — including nodes the
         new markup does not mention. Deleting only the ids that reappear would
         leave a stale stub behind for anything that disappeared, so a follow-up
         box that the panel stopped rendering would still answer with the text
         somebody typed into it a minute ago. */
      const previous = Array.from(markup.matchAll(/id="([^"]+)"/g)).map((m) => m[1]);
      markup = String(value);
      const current = Array.from(markup.matchAll(/id="([^"]+)"/g)).map((m) => m[1]);
      previous.concat(current).forEach(function (childId) {
        if (elements[childId]) {
          if (activeElementId === childId) {
            destroyedFocusedNodes += 1;
            activeElementId = null;
          }
          delete elements[childId];
        }
      });
      /* A browser builds the new nodes **now**, not when somebody first asks for
         one. Deferring creation made a freshly rendered textarea look absent,
         so a test could not tell "the panel rendered the draft" from "the draft
         is gone" — and those are the two outcomes it exists to distinguish.
         A textarea rendered with content between its tags comes back with that
         content in `.value`. */
      const values = {};
      Array.from(
        markup.matchAll(/<textarea\b[^>]*\bid="([^"]+)"[^>]*>([\s\S]*?)<\/textarea>/g)
      ).forEach(function (match) {
        values[match[1]] = unescapeHtml(match[2]);
      });
      /* And a text input rendered with a `value` attribute comes back holding
         it, exactly as the textarea above does. Without this a form whose state
         lives in the markup — the M2K PR24 requirement rows — would look empty
         after every render, and a test could not tell "the composer survived a
         refusal" from "the composer was thrown away", which is the distinction
         those scenarios exist to make. */
      Array.from(
        markup.matchAll(/<input\b[^>]*\bid="([^"]+)"[^>]*>/g)
      ).forEach(function (match) {
        const attribute = /\bvalue="([^"]*)"/.exec(match[0]);
        if (attribute) { values[match[1]] = unescapeHtml(attribute[1]); }
      });
      current.forEach(function (childId) {
        const child = makeElement(childId);
        if (Object.prototype.hasOwnProperty.call(values, childId)) {
          child.value = values[childId];
        }
        elements[childId] = child;
      });
    }
  });
  return node;
}

const elements = {};
IDS.forEach((id) => { elements[id] = makeElement(id); });

function unescapeHtml(value) {
  return String(value)
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&");
}

function el(id) {
  if (!elements[id]) { elements[id] = makeElement(id); }
  return elements[id];
}

/* Does an element with this id currently exist in the stub, and what is in it?
   `el()` would create one, which would hide the very destruction being tested. */
function existing(id) {
  return Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null;
}

function valueOf(id) {
  const node = existing(id);
  return node ? node.value : null;
}

function escapeHtml(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* ------------------------------------------------------------------ storage
 *
 * A localStorage double, deliberately **outside** the sandbox that runs
 * tasks.js. That is the whole point of it: a "reload" below builds a brand new
 * sandbox with brand new module state, and this object is the only thing that
 * survives — which is exactly what a browser does to a PWA whose tab iOS
 * discarded. A per-sandbox store could not tell "the draft was saved" from "the
 * draft was still in a variable".
 */
const storage = {
  data: {},
  get length() { return Object.keys(this.data).length; },
  key(index) { return Object.keys(this.data)[index]; },
  getItem(name) {
    return Object.prototype.hasOwnProperty.call(this.data, name)
      ? this.data[name]
      : null;
  },
  setItem(name, value) { this.data[name] = String(value); },
  removeItem(name) { delete this.data[name]; }
};

/* A localStorage that throws on every access, as iOS Safari does under Private
   Browsing. Selected per scenario, so the memory fallback is exercised rather
   than assumed. */
const hostileStorage = {
  get length() { throw new Error("SecurityError"); },
  key() { throw new Error("SecurityError"); },
  getItem() { throw new Error("SecurityError"); },
  setItem() { throw new Error("SecurityError"); },
  removeItem() { throw new Error("SecurityError"); }
};

/* `document.getElementById` is used by tasks.js for the two textareas, whose
   values are read at submit time rather than tracked per keystroke. */
const documentListeners = {};

const documentStub = {
  visibilityState: "visible",
  getElementById(id) { return elements[id] || null; },
  addEventListener(type, fn) {
    (documentListeners[type] = documentListeners[type] || []).push(fn);
  },
  removeEventListener(type, fn) {
    const list = documentListeners[type] || [];
    const index = list.indexOf(fn);
    if (index !== -1) { list.splice(index, 1); }
  },
  /* A browser reports which node has focus, and the panel reads it to decide
     whether to put the caret back after a re-render. Without it here, focus
     restoration could never be tested. */
  get activeElement() {
    return activeElementId ? elements[activeElementId] || null : null;
  }
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

/* An adapter that asks structured questions — the Agent SDK transport. The
   `clarifications` flag is what routes an answer to the answer route instead of
   to `/followups`, so it is the one capability these scenarios turn on. */
const SDK_CAPABILITIES = Object.assign({}, CAPABILITIES, { clarifications: true });

/* One pending question, shaped exactly as `PendingClarification.to_dict`
   produces it. Note what is absent and cannot be added: no provider session id,
   no tool input, no raw provider payload — the backend does not send them, so a
   scenario cannot smuggle one in to see whether the panel would render it. */
function clarificationPayload(options) {
  const settings = options || {};
  return {
    version: 1,
    category: "clarification",
    question_id: settings.question_id || "q_01hqqqqqqqqqqqqqqqqqqqqqqq",
    task_id: settings.task_id || "task_sdk",
    provider: "claude-agent-sdk",
    question: settings.question || "Hangi dosyayı düzenleyeyim?",
    answer_mode: settings.answer_mode || "single_choice",
    allows_free_text: settings.allows_free_text === true,
    schema_verified: settings.schema_verified !== false,
    options: settings.options === undefined
      ? [
          { option_id: "opt1", label: "README.md", description: "The readme" },
          { option_id: "opt2", label: "STATUS.md", description: null }
        ]
      : settings.options,
    requested_at: "2026-08-09T12:00:00.000Z",
    status: settings.status || "pending",
    answered_at: null
  };
}

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
  if (settings.twoAdapters) {
    /* Both Claude transports registered at once — the M2I.5 Gate B host. The
       SDK is listed first, which is what `/api/task-adapters` really returns
       (it sorts by id), so a composer that took `available[0]` would choose it. */
    return {
      adapters: [
        {
          adapter_id: "claude-agent-sdk",
          display_name: "Claude Agent SDK",
          description: "Runs a real program inside one approved project.",
          available: true,
          unavailable_reason: null,
          capabilities: CAPABILITIES,
          limitations: ["It has no shell."],
          max_concurrent_tasks: 1
        },
        {
          adapter_id: "claude-code",
          display_name: "Claude Code",
          description: "Runs a real program inside one approved project.",
          available: true,
          unavailable_reason: null,
          capabilities: CAPABILITIES,
          limitations: ["It has no shell."],
          max_concurrent_tasks: 1
        }
      ]
    };
  }
  if (settings.realAdapter) {
    /* An adapter that runs an actual program. Shaped exactly like the payload
       `/api/task-adapters` returns for one, and deliberately NOT named after any
       product: the panel must render it from these fields alone. */
    return {
      adapters: [
        {
          adapter_id: "runner",
          display_name: "Runner",
          description: "Runs a real program inside one approved project.",
          available: true,
          unavailable_reason: null,
          capabilities: CAPABILITIES,
          limitations: [
            "It has no shell. It can read and edit files in the chosen project.",
            "It cannot leave the project folder.",
            "Never type a password, one-time code or token into a prompt."
          ],
          max_concurrent_tasks: 1
        }
      ]
    };
  }
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
  if (settings.twoAdapters) {
    /* A project permitting both Claude transports and delegating to one of
       them — the M2I.5 Gate B shape. `claude-agent-sdk` is deliberately the
       adapter this project does NOT delegate to *and* the one that sorts first,
       so a composer that fell back to list order would be visible here. */
    return {
      projects: [
        { project_id: "demo", display_name: "Demo project", enabled: true,
          adapters: ["claude-agent-sdk", "claude-code"],
          delegated_adapter: "claude-code", delegation: "ok", notes: null }
      ],
      configured: 1,
      problems: [],
      source_present: true
    };
  }
  return {
    projects: [
      { project_id: "demo", display_name: "Demo project", enabled: true,
        adapters: ["validation"], delegated_adapter: "validation",
        delegation: "ok", notes: null }
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
      if (pathname.indexOf("/clarifications") !== -1) {
        return Promise.resolve({
          ok: true,
          status: 200,
          payload: {
            version: 1,
            task_id: (detail || taskPayload()).task_id,
            state: (detail || taskPayload()).state,
            waiting_reason: (detail || taskPayload()).waiting_reason,
            clarifications: behaviour.clarifications || []
          }
        });
      }
      if (pathname.indexOf("/assessment") !== -1) {
        if (behaviour.assessmentRefuse) {
          return Promise.resolve({
            ok: false,
            status: behaviour.assessmentRefuse,
            payload: { error: {
              code: "not_found", message: "that task has no such turn", detail: null
            } }
          });
        }
        /* The stub answers for the turn that was asked for, the way the real
           route does. A fixture that always returned turn 2 would make the
           panel's next-turn arithmetic wrap and would hide the bug it exists to
           catch. */
        const askedTurn = Number((pathname.match(/\/turns\/(\d+)\//) || [])[1] || 1);
        const view = Object.assign({}, behaviour.assessmentPayload || {});
        view.turn_number = askedTurn;
        return Promise.resolve({
          ok: true,
          status: 200,
          payload: {
            assessment: view,
            generated_at: "2026-08-16T12:00:00Z"
          }
        });
      }
      if (pathname.indexOf("/evidence") !== -1) {
        if (behaviour.evidenceRefuse) {
          return Promise.resolve({
            ok: false,
            status: behaviour.evidenceRefuse,
            payload: { error: {
              code: "not_found", message: "that task has no such turn", detail: null
            } }
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          payload: {
            evidence: behaviour.evidencePayload || {},
            /* Presentation metadata, on the envelope. The panel must not put it
               inside the bundle or treat it as part of its identity. */
            generated_at: "2026-08-14T12:00:00Z"
          }
        });
      }
      if (pathname.indexOf("/result") !== -1) {
        return Promise.resolve({
          ok: true, status: 200, payload: { result: behaviour.resultPayload || {} }
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
    if (behaviour.onWrite) {
      const deferred = behaviour.onWrite(pathname, settings.body);
      if (deferred) { return deferred; }
    }
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

/* One evaluation of the shipped tasks.js, in its own module state.
 *
 * A separate function rather than inline, because "load the page again" is now a
 * property under test: `reload()` below calls it a second time, giving fresh
 * module variables and a fresh DOM while `storage` persists. Anything that
 * survives that is something the panel genuinely wrote down. */
function evaluatePanel(store) {
  const sandbox = {
    console: {
      log: (...a) => record.consoleOutput.push(a.join(" ")),
      warn: (...a) => record.consoleOutput.push(a.join(" ")),
      error: (...a) => record.consoleOutput.push(a.join(" "))
    },
    document: documentStub,
    localStorage: store || storage,
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
  return sandbox.CofferdamTasks;
}

function run() {
  let tasks = evaluatePanel();

  function mount(behaviour) {
    return tasks.mount({ api: makeApi(behaviour), el, escapeHtml });
  }

  /* Throw the page away and load it again — a PWA reload, or iOS discarding a
     backgrounded tab. Every DOM node and every module variable is new; only
     `storage` crosses the boundary. */
  function reload(behaviour, store) {
    Object.keys(elements).forEach((id) => { delete elements[id]; });
    IDS.forEach((id) => { elements[id] = makeElement(id); });
    Object.keys(documentListeners).forEach((type) => {
      documentListeners[type] = [];
    });
    activeElementId = null;
    tasks = evaluatePanel(store);
    return tasks.mount({ api: makeApi(behaviour), el, escapeHtml });
  }

  /* Fire a document-level event, as a browser does when a tab is foregrounded. */
  function fireDocument(type) {
    (documentListeners[type] || []).forEach((fn) => fn({ type }));
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

  if (scenario === "composer-follows-the-projects-delegation") {
    /* Two adapters registered, one project delegating to the one that does NOT
       sort first. The composer must open on the delegated adapter and send it.
       If it ever falls back to list order this sends `claude-agent-sdk`. */
    return mount({
      twoAdapters: true,
      hang: true,
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", button("taskCompose"));
      return drain().then(function () {
        field("taskPrompt", "merhaba");
        fire("input", field("taskPrompt", "merhaba"));
        fire("click", button("taskStart"));
        return drain().then(function () {
          return { html: html(), writes: writes() };
        });
      });
    });
  }

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

  /* -- a real adapter's boundaries, and the wait that must not offer a box -- */

  if (scenario === "composer-shows-a-real-adapter-limitations") {
    return mount({
      realAdapter: true,
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", button("taskCompose"));
      return drain().then(function () { return { html: html() }; });
    });
  }

  if (scenario === "authentication-wait-offers-no-text-field") {
    /* The property: a task waiting for sign-in gets a sentence, never an input.
       A textarea under "waiting for sign-in on the workstation" is an invitation
       to type a password into a task history. */
    const waiting = taskPayload({
      task_id: "task_auth",
      state: "waiting_for_user",
      waiting_reason: "authentication",
      activity: "Sign in to Claude Code on the workstation."
    });
    return mount({
      realAdapter: true,
      initial: listPayload([waiting]),
      detail: waiting,
      events: [eventItem(1, "waiting_for_user", "Not signed in.")],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_auth"));
      return drain().then(function () {
        /* Read the rendered markup, not `el()`: the stub resolves every id in
           index.html whether or not the panel actually painted it, so asking
           it "is there a follow-up box" would answer yes forever. */
        const markup = html();
        return {
          html: markup,
          hasFollowupBox: markup.indexOf("taskFollowupText") !== -1,
          hasSendButton: markup.indexOf("taskFollowupSend") !== -1,
          hasCancelButton: markup.indexOf("taskCancel") !== -1
        };
      });
    });
  }

  if (scenario === "clarification-wait-still-offers-the-box") {
    /* The control for the scenario above: an ordinary question must still get a
       field, or the fix would have removed follow-up rather than protected it. */
    const waiting = taskPayload({
      task_id: "task_q",
      state: "waiting_for_user",
      waiting_reason: "clarification"
    });
    return mount({
      realAdapter: true,
      initial: listPayload([waiting]),
      detail: waiting,
      events: [eventItem(1, "waiting_for_user", "Which file?")],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_q"));
      return drain().then(function () {
        const markup = html();
        return { html: markup, hasFollowupBox: markup.indexOf("taskFollowupText") !== -1 };
      });
    });
  }

  /* -- the detail view must follow the backend on its own ------------------ */

  if (scenario === "detail-updates-without-a-page-reload") {
    /* The reported defect: the phone sat on `running` while the backend had
       already moved on, and only a manual browser refresh showed the truth.

       The detail endpoint is deliberately made SLOWER than the list endpoint
       here, because that is what production does — reading a task detail asks
       the adapter what it saw, which for the Claude adapter runs Git probes.
       A detail response that always arrives second is what turns a race into a
       certainty. */
    let state = "running";
    const taskOf = () => taskPayload({
      task_id: "task_live",
      state: state,
      activity: state === "running" ? "Working." : null,
      result: state === "running" ? null : "Bitti."
    });

    let slowDetail = 0;
    return mount({
      initial: listPayload([taskOf()]),
      onGet(pathname) {
        if (pathname.indexOf("/api/tasks/") === 0 && pathname.indexOf("/events") === -1) {
          /* Resolve after a few extra microtasks, so the list response for the
             same tick is applied first. */
          slowDetail += 1;
          let chain = Promise.resolve();
          for (let i = 0; i < 12; i += 1) { chain = chain.then(() => {}); }
          return chain.then(() => ({ ok: true, status: 200, payload: { task: taskOf() } }));
        }
        if (pathname === "/api/tasks") {
          return Promise.resolve({ ok: true, status: 200, payload: listPayload([taskOf()]) });
        }
        return null;
      },
      events: [eventItem(1, "task_started", "Running.")],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_live"));
      return drain(60).then(function () {
        const whileRunning = html();
        /* The backend moves on. No page reload happens after this point. */
        state = "ready_for_followup";
        advance(5000);
        return drain(120).then(function () {
          advance(5000);
          return drain(120).then(function () {
            return {
              whileRunning: whileRunning,
              html: html(),
              detailRequests: slowDetail,
              listRequests: record.requests.filter((r) => r.path === "/api/tasks").length
            };
          });
        });
      });
    });
  }

  if (scenario === "turn-complete-does-not-say-needs-you") {
    const done = taskPayload({
      task_id: "task_done",
      state: "ready_for_followup",
      activity: null,
      result: "NOTES.md olusturuldu."
    });
    return mount({
      realAdapter: true,
      initial: listPayload([done]),
      detail: done,
      events: [eventItem(1, "meaningful_output", "NOTES.md olusturuldu.")],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_done"));
      return drain().then(function () {
        const markup = html();
        return {
          html: markup,
          hasFollowupBox: markup.indexOf("taskFollowupText") !== -1,
          hasFinishButton: markup.indexOf("taskFinish") !== -1,
          hasCancelButton: markup.indexOf("taskCancel") !== -1
        };
      });
    });
  }

  if (scenario === "finish-closes-the-session") {
    const done = taskPayload({ task_id: "task_done", state: "ready_for_followup",
                               activity: null, result: "Bitti." });
    const completed = taskPayload({ task_id: "task_done", state: "completed",
                                    activity: null, result: "Bitti." });
    return mount({
      realAdapter: true,
      initial: listPayload([done]),
      detail: done,
      events: [eventItem(1, "meaningful_output", "Bitti.")],
      result: () => ({ payload: { task: completed }, detail: completed })
    }).then(function () {
      fire("click", openButton("task_done"));
      return drain().then(function () {
        fire("click", button("taskFinish"));
        return drain(80).then(function () {
          return { html: html(), writes: writes() };
        });
      });
    });
  }

  if (scenario === "unauthorized-is-surfaced-as-sign-in") {
    let reject = false;
    return mount({
      initial: listPayload([taskPayload({ task_id: "task_a", state: "running" })]),
      detail: taskPayload({ task_id: "task_a", state: "running" }),
      events: [eventItem(1, "task_started", "Running.")],
      onGet() {
        if (reject) { return Promise.reject(new Error("unauthorized")); }
        return null;
      },
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_a"));
      return drain().then(function () {
        reject = true;
        advance(5000);
        return drain(80).then(function () {
          const before = record.requests.length;
          advance(30000);
          return drain(80).then(function () {
            return {
              html: html(),
              requestsAfterUnauthorized: record.requests.length - before
            };
          });
        });
      });
    });
  }

  /* -- the follow-up draft must survive polling ---------------------------- */

  if (scenario === "draft-survives-polling") {
    /* The reported defect: text typed into the follow-up field vanished after a
       few seconds, and pasting appeared to re-render the page. Detail polling
       rebuilt the whole detail with `innerHTML`, which destroys the textarea
       and everything in it. */
    let activity = "Working.";
    const taskOf = () => taskPayload({
      task_id: "task_d", state: "ready_for_followup",
      activity: activity, result: "Bitti."
    });
    return mount({
      realAdapter: true,
      initial: listPayload([taskOf()]),
      onGet(pathname) {
        if (pathname.indexOf("/api/tasks/") === 0 && pathname.indexOf("/events") === -1) {
          return Promise.resolve({ ok: true, status: 200, payload: { task: taskOf() } });
        }
        if (pathname === "/api/tasks") {
          return Promise.resolve({ ok: true, status: 200, payload: listPayload([taskOf()]) });
        }
        return null;
      },
      events: [eventItem(1, "meaningful_output", "Bitti.")],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_d"));
      return drain().then(function () {
        /* The user types, and focuses the field. */
        const box = el("taskFollowupText");
        box.value = "ikinci bir Türkçe cümle ekle";
        box.focus();
        fire("input", box);
        const focusedBefore = activeElementId;

        /* Several polls happen, and the backend changes something real. */
        activity = "Still working.";
        advance(5000);
        return drain(80).then(function () {
          advance(5000);
          return drain(80).then(function () {
            advance(5000);
            return drain(80).then(function () {
              return {
                draftAfterPolling: valueOf("taskFollowupText"),
                focusedBefore: focusedBefore,
                focusedAfter: activeElementId,
                destroyedFocusedNodes: destroyedFocusedNodes,
                html: html()
              };
            });
          });
        });
      });
    });
  }

  if (scenario === "draft-survives-paste-and-identical-snapshots") {
    const task = taskPayload({ task_id: "task_p", state: "ready_for_followup",
                               activity: null, result: "Bitti." });
    return mount({
      realAdapter: true,
      initial: listPayload([task]), detail: task,
      events: [eventItem(1, "meaningful_output", "Bitti.")],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_p"));
      return drain().then(function () {
        const box = el("taskFollowupText");
        box.value = "Birinci cümle. İkinci cümle. Üçüncü cümle.";
        box.focus();
        fire("paste", box);
        /* Nothing about the task changes across these polls. */
        advance(5000); advance(5000); advance(5000); advance(5000);
        return drain(140).then(function () {
          return {
            draft: valueOf("taskFollowupText"),
            renders: destroyedFocusedNodes,
            focused: activeElementId
          };
        });
      });
    });
  }

  if (scenario === "failed-submit-preserves-the-draft") {
    const task = taskPayload({ task_id: "task_f", state: "ready_for_followup",
                               activity: null, result: "Bitti." });
    return mount({
      realAdapter: true,
      initial: listPayload([task]), detail: task,
      events: [eventItem(1, "meaningful_output", "Bitti.")],
      refuse: "That task cannot take a follow-up right now.",
      refuseCode: "task_conflict",
      refuseStatus: 409,
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_f"));
      return drain().then(function () {
        field("taskFollowupText", "bu metin kaybolmamalı");
        fire("click", button("taskFollowupSend"));
        return drain(120).then(function () {
          return { draft: valueOf("taskFollowupText"), html: html() };
        });
      });
    });
  }

  if (scenario === "accepted-submit-clears-once") {
    const ready = taskPayload({ task_id: "task_s", state: "ready_for_followup",
                                activity: null, result: "Bitti." });
    const running = taskPayload({ task_id: "task_s", state: "running" });
    return mount({
      realAdapter: true,
      initial: listPayload([ready]), detail: ready,
      events: [eventItem(1, "meaningful_output", "Bitti.")],
      result: () => ({ payload: { task: running }, detail: running })
    }).then(function () {
      fire("click", openButton("task_s"));
      return drain().then(function () {
        field("taskFollowupText", "gönderilecek metin");
        fire("click", button("taskFollowupSend"));
        fire("click", button("taskFollowupSend"));   /* double tap */
        return drain(140).then(function () {
          const posts = writes().filter((w) => w.path.indexOf("/followups") !== -1);
          return {
            draft: valueOf("taskFollowupText"),
            followupPosts: posts.length,
            requestIds: posts.map((w) => w.body && w.body.client_request_id)
          };
        });
      });
    });
  }

  if (scenario === "drafts-are-per-task") {
    const a = taskPayload({ task_id: "task_a", state: "ready_for_followup",
                            activity: null, result: "A." });
    const b = taskPayload({ task_id: "task_b", state: "ready_for_followup",
                            activity: null, result: "B." });
    let current = a;
    return mount({
      realAdapter: true,
      initial: listPayload([a, b]),
      onGet(pathname) {
        if (pathname.indexOf("/api/tasks/task_a") === 0 && pathname.indexOf("/events") === -1) {
          return Promise.resolve({ ok: true, status: 200, payload: { task: a } });
        }
        if (pathname.indexOf("/api/tasks/task_b") === 0 && pathname.indexOf("/events") === -1) {
          return Promise.resolve({ ok: true, status: 200, payload: { task: b } });
        }
        return null;
      },
      events: [eventItem(1, "meaningful_output", "x")],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_a"));
      return drain().then(function () {
        field("taskFollowupText", "A için taslak");
        fire("click", button("taskBack"));
        return drain(60).then(function () {
          fire("click", openButton("task_b"));
          return drain(60).then(function () {
            const inB = valueOf("taskFollowupText");
            fire("click", button("taskBack"));
            return drain(60).then(function () {
              fire("click", openButton("task_a"));
              return drain(60).then(function () {
                return { draftInB: inB, draftBackInA: valueOf("taskFollowupText") };
              });
            });
          });
        });
      });
    });
  }

  if (scenario === "finishing-does-not-submit-the-draft") {
    const ready = taskPayload({ task_id: "task_x", state: "ready_for_followup",
                               activity: null, result: "Bitti." });
    const done = taskPayload({ task_id: "task_x", state: "completed", result: "Bitti." });
    return mount({
      realAdapter: true,
      initial: listPayload([ready]), detail: ready,
      events: [eventItem(1, "meaningful_output", "Bitti.")],
      result: () => ({ payload: { task: done }, detail: done })
    }).then(function () {
      fire("click", openButton("task_x"));
      return drain().then(function () {
        field("taskFollowupText", "gönderilmemeli");
        fire("click", button("taskFinish"));
        return drain(140).then(function () {
          return {
            writes: writes().map((w) => w.path),
            html: html()
          };
        });
      });
    });
  }

  if (scenario === "text-typed-while-sending-is-not-swallowed") {
    /* The requirement that the clear must correspond to *this* submission.
       Somebody sends one message and starts typing the next before the server
       answers. Clearing unconditionally on acceptance would delete the second
       message, which nobody sent and nobody asked to lose. */
    const ready = taskPayload({ task_id: "task_t", state: "ready_for_followup",
                                activity: null, result: "Bitti." });
    const running = taskPayload({ task_id: "task_t", state: "running" });
    let release = null;
    return mount({
      realAdapter: true,
      initial: listPayload([ready]), detail: ready,
      events: [eventItem(1, "meaningful_output", "Bitti.")],
      onWrite() {
        return new Promise(function (resolve) { release = resolve; });
      },
      result: () => ({ payload: { task: running }, detail: running })
    }).then(function () {
      fire("click", openButton("task_t"));
      return drain().then(function () {
        field("taskFollowupText", "birinci mesaj");
        fire("click", button("taskFollowupSend"));
        return drain(20).then(function () {
          /* In flight. The person keeps typing. */
          field("taskFollowupText", "ikinci mesaj");
          if (release) {
            release({ ok: true, status: 200, payload: { task: running } });
          }
          return drain(140).then(function () {
            return { draft: valueOf("taskFollowupText") };
          });
        });
      });
    });
  }

  /* -- M2I PR4: the structured question round trip -------------------------- */

  function sdkWaiting(extra) {
    return taskPayload(Object.assign({
      task_id: "task_sdk",
      state: "waiting_for_user",
      waiting_reason: "clarification",
      capabilities: SDK_CAPABILITIES
    }, extra || {}));
  }

  function openSdkQuestion(behaviour) {
    const waiting = sdkWaiting();
    return mount(Object.assign({
      realAdapter: true,
      initial: listPayload([waiting]),
      detail: waiting,
      events: [eventItem(1, "waiting_for_user", "Claude asked a question.")],
      clarifications: [clarificationPayload({})],
      result: () => ({ payload: { task: waiting } })
    }, behaviour || {})).then(function () {
      fire("click", openButton("task_sdk"));
      return drain();
    });
  }

  if (scenario === "sdk-question-is-rendered-with-its-options") {
    /* The gap M2I PR4 exists to close. Before it, a task waiting on a structured
       question got a generic "Your answer" box wired to `/followups` — a route
       the server refuses outright while a question is open. */
    return openSdkQuestion().then(function () {
      const markup = html();
      return {
        html: markup,
        hasQuestion: markup.indexOf("task-question") !== -1,
        hasAnswerBox: markup.indexOf("taskAnswerText") !== -1,
        hasFollowupBox: markup.indexOf("taskFollowupText") !== -1,
        hasSendButton: markup.indexOf("taskAnswerSend") !== -1
      };
    });
  }

  if (scenario === "sdk-question-free-text-is-offered-only-when-allowed") {
    const waiting = sdkWaiting();
    return mount({
      realAdapter: true,
      initial: listPayload([waiting]),
      detail: waiting,
      clarifications: [clarificationPayload({ allows_free_text: true })],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_sdk"));
      return drain().then(function () {
        return { html: html(), hasAnswerBox: html().indexOf("taskAnswerText") !== -1 };
      });
    });
  }

  if (scenario === "sdk-answer-goes-to-the-answer-route") {
    /* Which route, and which body. Both matter: `option_ids` are Cofferdam's own
       identifiers taken from the question being answered, and there is no third
       field for anything an approval could travel in. */
    const answered = taskPayload({
      task_id: "task_sdk", state: "running", capabilities: SDK_CAPABILITIES
    });
    return openSdkQuestion({
      result: () => ({ payload: { task: answered }, detail: answered })
    }).then(function () {
      fire("change", { className: "task-option-input", value: "opt2", checked: true });
      fire("click", button("taskAnswerSend"));
      return drain().then(function () {
        return { html: html(), writes: writes() };
      });
    });
  }

  if (scenario === "sdk-followup-is-refused-while-a-question-is-open") {
    /* The panel refuses before the server has to. A follow-up sent here would be
       answered `task_clarification_pending`, which is a refusal the person can do
       nothing about from the screen they are looking at. */
    return openSdkQuestion().then(function () {
      fire("click", button("taskFollowupSend"));
      return drain().then(function () {
        return { html: html(), writes: writes() };
      });
    });
  }

  if (scenario === "sdk-unverified-question-shape-is-labelled") {
    const waiting = sdkWaiting();
    return mount({
      realAdapter: true,
      initial: listPayload([waiting]),
      detail: waiting,
      clarifications: [clarificationPayload({
        schema_verified: false, answer_mode: "unknown", options: [],
        allows_free_text: true
      })],
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_sdk"));
      return drain().then(function () { return { html: html() }; });
    });
  }

  if (scenario === "sdk-question-renders-no-provider-field") {
    /* Everything the payload carries is rendered or dropped; nothing a provider
       named appears. The assertion belongs in the test, which knows which words
       are forbidden — this only hands it the markup. */
    return openSdkQuestion().then(function () {
      return { html: html(), storage: storage.data };
    });
  }

  /* -- M2I PR4: drafts that survive a reload -------------------------------- */

  function readyTask(id) {
    return taskPayload({
      task_id: id || "task_ready",
      state: "ready_for_followup",
      result: "Birinci tur bitti.",
      capabilities: SDK_CAPABILITIES
    });
  }

  if (scenario === "followup-draft-survives-a-reload") {
    /* The defect this closes: iOS discards a backgrounded tab, the person comes
       back, and the sentence they were part-way through is gone. Module state
       cannot survive that; only storage can, which is why the harness's store
       lives outside the sandbox. */
    const ready = readyTask();
    const behaviour = {
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      result: () => ({ payload: { task: ready } })
    };
    return mount(behaviour).then(function () {
      fire("click", openButton("task_ready"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "yarım kalmış bir cümle");
      /* A render is what files the draft, exactly as a poll would. */
      advance(4000);
      return drain();
    }).then(function () {
      const before = storage.data;
      return reload(behaviour).then(function () {
        fire("click", openButton("task_ready"));
        return drain().then(function () {
          return {
            storedBefore: Object.keys(before).length,
            draftAfterReload: valueOf("taskFollowupText"),
            writes: writes()
          };
        });
      });
    });
  }

  if (scenario === "clarification-draft-survives-a-reload") {
    const waiting = sdkWaiting();
    const behaviour = {
      realAdapter: true,
      initial: listPayload([waiting]),
      detail: waiting,
      clarifications: [clarificationPayload({ allows_free_text: true })],
      result: () => ({ payload: { task: waiting } })
    };
    return mount(behaviour).then(function () {
      fire("click", openButton("task_sdk"));
      return drain();
    }).then(function () {
      field("taskAnswerText", "üçüncü seçenek olsun");
      /* A waiting task polls on the slower interval — it is not active. */
      advance(12000);
      return drain();
    }).then(function () {
      return reload(behaviour).then(function () {
        fire("click", openButton("task_sdk"));
        return drain().then(function () {
          return {
            draftAfterReload: valueOf("taskAnswerText"),
            keys: Object.keys(storage.data),
            writes: writes()
          };
        });
      });
    });
  }

  if (scenario === "drafts-are-separate-by-operation") {
    /* A half-typed answer to a question must not reappear as a follow-up once
       the question is answered and the task moves on. Two keys, two boxes. */
    let waiting = sdkWaiting();
    const ready = readyTask("task_sdk");
    const behaviour = {
      realAdapter: true,
      initial: listPayload([waiting]),
      detail: waiting,
      clarifications: [clarificationPayload({ allows_free_text: true })],
      result: () => ({ payload: { task: waiting } })
    };
    return mount(behaviour).then(function () {
      fire("click", openButton("task_sdk"));
      return drain();
    }).then(function () {
      field("taskAnswerText", "soruya cevap");
      advance(12000);
      return drain();
    }).then(function () {
      /* The same task, now past the question. */
      const moved = Object.assign({}, behaviour, {
        detail: ready,
        initial: listPayload([ready]),
        clarifications: []
      });
      return reload(moved).then(function () {
        fire("click", openButton("task_sdk"));
        return drain().then(function () {
          return {
            followupBox: valueOf("taskFollowupText"),
            keys: Object.keys(storage.data)
          };
        });
      });
    });
  }

  if (scenario === "drafts-do-not-cross-tasks-in-storage") {
    const a = readyTask("task_a");
    const b = readyTask("task_b");
    let current = a;
    const behaviour = {
      realAdapter: true,
      initial: listPayload([a, b]),
      onGet(pathname) {
        if (pathname === "/api/tasks/task_a") {
          return Promise.resolve({ ok: true, status: 200, payload: { task: a } });
        }
        if (pathname === "/api/tasks/task_b") {
          return Promise.resolve({ ok: true, status: 200, payload: { task: b } });
        }
        return null;
      },
      detail: current,
      result: () => ({ payload: { task: current } })
    };
    return mount(behaviour).then(function () {
      fire("click", openButton("task_a"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "A için taslak");
      advance(4000);
      return drain();
    }).then(function () {
      return reload(behaviour).then(function () {
        fire("click", openButton("task_b"));
        return drain().then(function () {
          return { draftInB: valueOf("taskFollowupText"), keys: Object.keys(storage.data) };
        });
      });
    });
  }

  if (scenario === "a-terminal-task-drops-its-draft") {
    /* A cancelled task keeps no unsent words. Leaving them would show somebody
       text on a screen that has nowhere left to send it. */
    const ready = readyTask("task_end");
    const cancelled = taskPayload({
      task_id: "task_end", state: "cancelled", capabilities: SDK_CAPABILITIES
    });
    let current = ready;
    const behaviour = {
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      onGet(pathname) {
        if (pathname.indexOf("/api/tasks/task_end") === 0 &&
            pathname.indexOf("/events") === -1) {
          return Promise.resolve({ ok: true, status: 200, payload: { task: current } });
        }
        return null;
      },
      result() {
        current = cancelled;
        return { payload: { task: cancelled }, detail: cancelled };
      }
    };
    return mount(behaviour).then(function () {
      fire("click", openButton("task_end"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "gönderilmeyecek metin");
      advance(4000);
      return drain();
    }).then(function () {
      const held = Object.keys(storage.data).length;
      fire("click", button("taskCancel"));
      return drain().then(function () {
        return { keysWhileOpen: held, keysAfterCancel: Object.keys(storage.data).length };
      });
    });
  }

  if (scenario === "signing-out-removes-stored-drafts") {
    const ready = readyTask();
    const behaviour = {
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      result: () => ({ payload: { task: ready } })
    };
    return mount(behaviour).then(function () {
      fire("click", openButton("task_ready"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "çıkışta silinmeli");
      advance(4000);
      return drain();
    }).then(function () {
      const before = Object.keys(storage.data).length;
      tasks.stop();
      return drain().then(function () {
        return { before: before, after: Object.keys(storage.data).length };
      });
    });
  }

  if (scenario === "a-storage-refusal-does-not-break-the-panel") {
    /* iOS Safari throws on the property access itself under Private Browsing.
       app.js learned this the hard way; this panel inherits the guard. */
    const ready = readyTask();
    const behaviour = {
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      result: () => ({ payload: { task: ready } })
    };
    return reload(behaviour, hostileStorage).then(function () {
      fire("click", openButton("task_ready"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "hafızada kalsın");
      advance(4000);
      return drain().then(function () {
        return { html: html(), draft: valueOf("taskFollowupText") };
      });
    });
  }

  /* -- M2I PR4: request identity ------------------------------------------- */

  if (scenario === "a-refused-followup-keeps-its-request-id") {
    /* The defect: the key was cleared on any response at all, including a
       refusal — so the retry the person immediately makes carried a *new* key
       and the server could not recognise it as the same message. */
    const ready = readyTask();
    let attempts = 0;
    return mount({
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      onWrite(pathname, body) {
        if (pathname.indexOf("/followups") === -1) { return null; }
        attempts += 1;
        if (attempts === 1) {
          return Promise.resolve({
            ok: false,
            status: 503,
            payload: { error: { code: "task_adapter_error", message: "Not now." } }
          });
        }
        return Promise.resolve({ ok: true, status: 200, payload: { task: ready } });
      },
      result: () => ({ payload: { task: ready } })
    }).then(function () {
      fire("click", openButton("task_ready"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "aynı mesaj");
      fire("click", button("taskFollowupSend"));
      return drain();
    }).then(function () {
      /* After the refusal: the words and the key both have to still be here,
         because this is the moment somebody presses the button again. */
      const draftAfterRefusal = valueOf("taskFollowupText");
      field("taskFollowupText", "aynı mesaj");
      fire("click", button("taskFollowupSend"));
      return drain().then(function () {
        /* And the retry is accepted, so now — and only now — it goes. */
        const posts = writes().filter((w) => w.path.indexOf("/followups") !== -1);
        return {
          posts: posts.length,
          requestIds: posts.map((w) => w.body.client_request_id),
          draftAfterRefusal: draftAfterRefusal,
          draftAfterAccept: valueOf("taskFollowupText"),
          keysAfterAccept: Object.keys(storage.data)
        };
      });
    });
  }

  if (scenario === "an-edited-followup-gets-a-new-request-id") {
    /* The other half. The server binds a key to a payload hash and answers the
       same key with different words as a conflict — so different words have to
       arrive under a different key. */
    const ready = readyTask();
    return mount({
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      onWrite(pathname) {
        if (pathname.indexOf("/followups") === -1) { return null; }
        return Promise.resolve({
          ok: false,
          status: 409,
          payload: { error: { code: "task_idempotency_conflict", message: "No." } }
        });
      },
      result: () => ({ payload: { task: ready } })
    }).then(function () {
      fire("click", openButton("task_ready"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "ilk hâli");
      fire("click", button("taskFollowupSend"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "düzeltilmiş hâli");
      fire("click", button("taskFollowupSend"));
      return drain().then(function () {
        const posts = writes().filter((w) => w.path.indexOf("/followups") !== -1);
        return { requestIds: posts.map((w) => w.body.client_request_id) };
      });
    });
  }

  if (scenario === "no-draft-is-submitted-on-its-own") {
    /* Nothing about coming back to the app may send anything. A draft restored
       after a reload is text on a screen, not a message in flight. */
    const ready = readyTask();
    const behaviour = {
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      result: () => ({ payload: { task: ready } })
    };
    return mount(behaviour).then(function () {
      fire("click", openButton("task_ready"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "kendiliğinden gitmesin");
      advance(4000);
      return drain();
    }).then(function () {
      return reload(behaviour).then(function () {
        fire("click", openButton("task_ready"));
        return drain();
      }).then(function () {
        documentStub.visibilityState = "hidden";
        fireDocument("visibilitychange");
        advance(20000);
        documentStub.visibilityState = "visible";
        fireDocument("visibilitychange");
        advance(20000);
        return drain().then(function () {
          return {
            draft: valueOf("taskFollowupText"),
            writes: writes().map((w) => w.path)
          };
        });
      });
    });
  }

  /* -- M2I PR4: foregrounding ---------------------------------------------- */

  if (scenario === "foregrounding-refreshes-without-waiting") {
    /* Polling has always stopped while hidden. What was missing is the other
       half: coming back waited out the rest of an interval before asking. */
    const running = taskPayload({ task_id: "task_fg", state: "running" });
    return mount({
      initial: listPayload([running]),
      detail: running,
      result: () => ({ payload: {} })
    }).then(function () {
      const before = record.requests.filter((r) => r.method === "GET").length;
      documentStub.visibilityState = "hidden";
      fireDocument("visibilitychange");
      advance(30000);
      const whileHidden = record.requests.filter((r) => r.method === "GET").length;
      documentStub.visibilityState = "visible";
      fireDocument("visibilitychange");
      return drain().then(function () {
        return {
          afterMount: before,
          whileHidden: whileHidden,
          afterForeground: record.requests.filter((r) => r.method === "GET").length
        };
      });
    });
  }

  /* -- M2I PR4: the result route ------------------------------------------- */

  if (scenario === "the-result-route-reports-the-latest-turn") {
    const ready = readyTask("task_res");
    return mount({
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      resultPayload: {
        version: 1,
        task_id: "task_res",
        task_state: "ready_for_followup",
        task_terminal: false,
        outcome: "completed",
        succeeded: true,
        completed_at: "2026-08-09T12:00:00.000Z",
        provider: "claude-agent-sdk",
        provider_session_id: "3f5a6b7c-1111-2222-3333-444455556666",
        turn_number: 2,
        provider_turn_sequence: 12,
        turn_count: 2,
        result: "İkinci turun sonucu.",
        failure_code: null,
        failure_summary: null,
        follow_up_available: true,
        evidence_source: "adapter_reported",
        result_meaning: "The latest completed turn's result."
      },
      result: () => ({ payload: { task: ready } })
    }).then(function () {
      fire("click", openButton("task_res"));
      return drain();
    }).then(function () {
      fire("click", button("taskShowResult"));
      return drain().then(function () {
        return { html: html(), storage: storage.data };
      });
    });
  }

  /* -- M2I PR4 fix: an accepted follow-up leaves nothing behind ------------- */

  if (scenario === "an-accepted-followup-clears-everything") {
    /* The defect the phone found, in one scenario.

       Clearing the store was not enough: the draft is not part of the markup,
       so the textarea still held the accepted sentence, and the next render's
       `captureDraft` read that node and wrote it back. On the phone the text
       reappeared, and because the request id had been released with it, the
       next tap sent the same words under a new key — a second provider turn. */
    const ready = readyTask("task_acc");
    return mount({
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      result: () => ({ payload: { task: ready } })
    }).then(function () {
      fire("click", openButton("task_acc"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "hangi etiketi seçtim?");
      advance(4000);                       /* a poll files the draft */
      return drain();
    }).then(function () {
      const storedBefore = Object.keys(storage.data).length;
      fire("click", button("taskFollowupSend"));
      return drain().then(function () {
        /* And a further render, which is where the draft used to come back. */
        advance(4000);
        return drain().then(function () {
          return {
            storedBefore: storedBefore,
            box: valueOf("taskFollowupText"),
            keys: Object.keys(storage.data),
            posts: writes().filter((w) => w.path.indexOf("/followups") !== -1).length
          };
        });
      });
    });
  }

  if (scenario === "an-accepted-followup-does-not-return-after-a-reload") {
    const ready = readyTask("task_acc");
    const behaviour = {
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      result: () => ({ payload: { task: ready } })
    };
    return mount(behaviour).then(function () {
      fire("click", openButton("task_acc"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "gönderildi ve bitti");
      advance(4000);
      return drain();
    }).then(function () {
      fire("click", button("taskFollowupSend"));
      return drain();
    }).then(function () {
      return reload(behaviour).then(function () {
        fire("click", openButton("task_acc"));
        return drain().then(function () {
          return { box: valueOf("taskFollowupText"), keys: Object.keys(storage.data) };
        });
      });
    });
  }

  if (scenario === "a-second-tap-after-acceptance-sends-nothing") {
    /* The consequence, asserted directly: with the box empty there is nothing
       to resend, so a second tap produces no second turn. */
    const ready = readyTask("task_acc");
    return mount({
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      result: () => ({ payload: { task: ready } })
    }).then(function () {
      fire("click", openButton("task_acc"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "tek sefer");
      fire("click", button("taskFollowupSend"));
      return drain();
    }).then(function () {
      fire("click", button("taskFollowupSend"));
      return drain();
    }).then(function () {
      fire("click", button("taskFollowupSend"));
      return drain().then(function () {
        const posts = writes().filter((w) => w.path.indexOf("/followups") !== -1);
        return {
          posts: posts.length,
          requestIds: posts.map((w) => w.body.client_request_id),
          box: valueOf("taskFollowupText"),
          html: html()
        };
      });
    });
  }

  if (scenario === "text-typed-while-in-flight-survives-acceptance") {
    /* The property the clear must not break: what somebody typed *after*
       pressing Send is their next message, not a leftover of the accepted one. */
    const ready = readyTask("task_acc");
    let release = null;
    return mount({
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      onWrite(pathname) {
        if (pathname.indexOf("/followups") === -1) { return null; }
        return new Promise(function (resolve) {
          release = () => resolve({ ok: true, status: 200, payload: { task: ready } });
        });
      },
      result: () => ({ payload: { task: ready } })
    }).then(function () {
      fire("click", openButton("task_acc"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "ilk mesaj");
      fire("click", button("taskFollowupSend"));
      return drain();
    }).then(function () {
      /* Typed while the request is still open. */
      field("taskFollowupText", "sonraki mesaj");
      release();
      return drain().then(function () {
        return { box: valueOf("taskFollowupText"), keys: Object.keys(storage.data) };
      });
    });
  }

  if (scenario === "a-refused-followup-still-keeps-the-box") {
    const ready = readyTask("task_acc");
    return mount({
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      onWrite(pathname) {
        if (pathname.indexOf("/followups") === -1) { return null; }
        return Promise.resolve({
          ok: false,
          status: 409,
          payload: { error: { code: "task_idempotency_conflict", message: "No." } }
        });
      },
      result: () => ({ payload: { task: ready } })
    }).then(function () {
      fire("click", openButton("task_acc"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "reddedildi ama duruyor");
      fire("click", button("taskFollowupSend"));
      return drain().then(function () {
        advance(4000);
        return drain().then(function () {
          const posts = writes().filter((w) => w.path.indexOf("/followups") !== -1);
          return {
            box: valueOf("taskFollowupText"),
            keys: Object.keys(storage.data),
            requestIds: posts.map((w) => w.body.client_request_id)
          };
        });
      });
    });
  }

  if (scenario === "accepting-one-task-leaves-another-tasks-draft") {
    const a = readyTask("task_a");
    const b = readyTask("task_b");
    let current = a;
    const behaviour = {
      realAdapter: true,
      initial: listPayload([a, b]),
      detail: () => current,
      result: () => ({ payload: { task: current } })
    };
    return mount(behaviour).then(function () {
      fire("click", openButton("task_b"));
      return drain();
    }).then(function () {
      current = b;
      field("taskFollowupText", "b için taslak");
      advance(4000);
      return drain();
    }).then(function () {
      fire("click", button("taskBack"));
      return drain();
    }).then(function () {
      current = a;
      fire("click", openButton("task_a"));
      return drain();
    }).then(function () {
      field("taskFollowupText", "a gönderiliyor");
      fire("click", button("taskFollowupSend"));
      return drain().then(function () {
        return {
          keys: Object.keys(storage.data).sort(),
          boxAfterAcceptOnA: valueOf("taskFollowupText")
        };
      });
    });
  }

  if (scenario === "an-accepted-answer-leaves-the-followup-draft-alone") {
    /* The two operations stay separate through an acceptance as well. */
    const waiting = sdkWaiting();
    return mount({
      realAdapter: true,
      initial: listPayload([waiting]),
      detail: waiting,
      clarifications: [clarificationPayload({ allows_free_text: true })],
      result: () => ({ payload: { task: waiting } })
    }).then(function () {
      fire("click", openButton("task_sdk"));
      return drain();
    }).then(function () {
      field("taskAnswerText", "cevap metni");
      advance(12000);
      return drain();
    }).then(function () {
      /* A follow-up draft filed under the same task, by hand, so the
         acceptance below has something of the other kind to leave alone. */
      storage.setItem("cofferdam.taskdraft.followup.task_sdk", "sonraki mesaj");
      fire("click", button("taskAnswerSend"));
      return drain().then(function () {
        return { keys: Object.keys(storage.data).sort() };
      });
    });
  }

  /* -- evidence (M2K PR2) -------------------------------------------------- */

  /* One bundle carrying all three relationships at once, plus a limitation and
     an incomplete claim set. One scenario rather than four, because the thing
     under test is that the three read *differently on the same screen* — which
     is exactly what a separate fixture per relationship would stop proving. */
  function evidenceBundle(overrides) {
    return Object.assign({
      version: 1,
      assembler_version: 2,
      input_fingerprint: "a".repeat(64),
      task_id: "task_e",
      turn_number: 1,
      turn_attribution: "exact",
      opened_after_event_sequence: 4,
      closed_through_event_sequence: 9,
      turn_open: false,
      repository_reported_clean: false,
      machine_observations_complete: true,
      ingestion: {
        state: "incomplete",
        submitted: 3,
        accepted: 2,
        rejected: 1,
        truncated: false,
        reason_counts: { claim_invalid: 1 },
        sequences: [0]
      },
      claims: [
        {
          claim_id: "chg_one", task_id: "task_e", turn_number: 1,
          operation: "modified", path: "src/foo.py", to_path: null,
          adapter_label: null, reported_at: "2026-08-14T00:00:00Z",
          artifact_id: "art_one", reason: "ok",
          source: "adapter_reported", verified: false
        },
        {
          claim_id: "chg_two", task_id: "task_e", turn_number: 1,
          operation: "created", path: "src/bar.py", to_path: null,
          adapter_label: null, reported_at: "2026-08-14T00:00:00Z",
          artifact_id: "art_two", reason: "ok",
          source: "adapter_reported", verified: false
        }
      ],
      observations: [
        {
          reference: "evt7.0", event_sequence: 7, evidence_index: 0,
          path: "src/foo.py", source: "git_observed", evidence_type: "file",
          operation: "git status", result: "changed", verified: true,
          change_kind: "modified", previous_path: null, change_status: "MM"
        },
        {
          reference: "evt7.1", event_sequence: 7, evidence_index: 1,
          path: "src/unclaimed.py", source: "git_observed", evidence_type: "file",
          operation: "git status", result: "changed", verified: true,
          change_kind: "created", previous_path: null
        }
      ],
      relationships: [
        {
          path: "src/bar.py", relationship: "claim_only",
          claim_ids: ["chg_two"], claim_operations: ["created"],
          observation_refs: [], path_agreement: false,
          operation_agreement: "unknown", claim_count: 1,
          observation_count: 0, sources_truncated: false
        },
        {
          path: "src/foo.py", relationship: "path_agreed",
          claim_ids: ["chg_one"], claim_operations: ["modified"],
          observation_refs: ["evt7.0"], path_agreement: true,
          operation_agreement: "true", observed_kinds: ["modified"], claim_count: 1,
          observation_count: 1, sources_truncated: false
        },
        {
          path: "src/unclaimed.py", relationship: "observed_only",
          claim_ids: [], claim_operations: [],
          observation_refs: ["evt7.1"], path_agreement: false,
          operation_agreement: "unknown", claim_count: 0,
          observation_count: 1, sources_truncated: false
        }
      ],
      limitations: ["claim_set_incomplete"]
    }, overrides || {});
  }

  function evidenceScenario(bundle, extra) {
    return mount(Object.assign({
      initial: listPayload([taskPayload({ task_id: "task_e", state: "completed" })]),
      detail: taskPayload({
        task_id: "task_e", state: "completed", result: "Done."
      }),
      evidencePayload: bundle,
      result: () => ({ payload: {} })
    }, extra || {})).then(function () {
      fire("click", openButton("task_e"));
      return drain().then(function () {
        fire("click", button("taskShowEvidence"));
        return drain().then(function () {
          return { html: html(), requests: record.requests.slice() };
        });
      });
    });
  }

  /* -- assessment (M2K PR8) ------------------------------------------------ */

  /* One assessment carrying all three results at once, plus a manual criterion.
     One scenario rather than three, for the reason the evidence fixture gives:
     what is under test is that `met`, `not_met` and `unverified` read
     *differently on the same screen*, which a fixture per result would stop
     proving. */
  /* M2K PR22. `counts` and `requires_human` are tri-state and the harness must
     be able to produce `null` for both, so scenarios pass them explicitly. */
  function acceptanceView(overrides) {
    return Object.assign({
      aggregator_version: 1,
      availability: "assessable",
      availability_reason: null,
      unavailable_cause: null,
      unavailable_at_turn_number: null,
      outcome: "incomplete",
      counts: { total: 4, met: 2, not_met: 1, unverified: 1 },
      requires_human: true,
      assessment_fingerprint: "e".repeat(64),
      acceptance_fingerprint: "a".repeat(64)
    }, overrides || {});
  }

  function assessmentView(overrides) {
    return Object.assign({
      version: 1,
      task_id: "task_a",
      turn_number: 1,
      acceptance: acceptanceView(),
      criteria: {
        state: "present",
        recorded: true,
        snapshot_id: "acs_" + "a".repeat(26),
        criteria_fingerprint: "c".repeat(64),
        criterion_count: 4,
        items: [
          { criterion_id: "acr_1", ordinal: 1, kind: "evidence",
            predicate: "path_changed", path: "src/app.py", to_path: null,
            operation: null, description: null },
          { criterion_id: "acr_2", ordinal: 2, kind: "evidence",
            predicate: "path_operation", path: "src/gone.py", to_path: null,
            operation: "created", description: null },
          { criterion_id: "acr_3", ordinal: 3, kind: "evidence",
            predicate: "rename", path: "src/old.py", to_path: "src/new.py",
            operation: null, description: null },
          { criterion_id: "acr_4", ordinal: 4, kind: "manual",
            predicate: null, path: null, to_path: null, operation: null,
            description: "a person confirms the page renders" }
        ]
      },
      evaluation: {
        state: "recorded",
        recorded: true,
        evaluation_id: "evl_" + "b".repeat(26),
        evaluator_version: 1,
        criteria_state: "present",
        criteria_snapshot_id: "acs_" + "a".repeat(26),
        criteria_fingerprint: "c".repeat(64),
        assembler_version: 3,
        evidence_input_fingerprint: "f".repeat(64),
        result_count: 4,
        evaluation_fingerprint: "d".repeat(64),
        results: [
          { criterion_id: "acr_1", ordinal: 1, result: "met",
            reason: "machine_change_observed" },
          { criterion_id: "acr_2", ordinal: 2, result: "not_met",
            reason: "complete_resulting_change_absent" },
          { criterion_id: "acr_3", ordinal: 3, result: "unverified",
            reason: "pre_work_boundary_not_clean" },
          { criterion_id: "acr_4", ordinal: 4, result: "unverified",
            reason: "manual_criterion" }
        ]
      }
    }, overrides || {});
  }

  function assessmentScenario(view, extra) {
    return mount(Object.assign({
      initial: listPayload([taskPayload({ task_id: "task_a", state: "completed" })]),
      detail: taskPayload({
        task_id: "task_a", state: "completed", result: "Done."
      }),
      assessmentPayload: view,
      result: () => ({ payload: {} })
    }, extra || {})).then(function () {
      fire("click", openButton("task_a"));
      return drain().then(function () {
        fire("click", button("taskShowAssessment"));
        return drain().then(function () {
          return { html: html(), requests: record.requests.slice() };
        });
      });
    });
  }

  if (scenario === "assessment-shows-all-three-results") {
    return assessmentScenario(assessmentView());
  }

  if (scenario === "acceptance-met") {
    return assessmentScenario(assessmentView({
      acceptance: acceptanceView({
        outcome: "met",
        counts: { total: 2, met: 2, not_met: 0, unverified: 0 },
        requires_human: false
      })
    }));
  }

  if (scenario === "acceptance-not-met") {
    return assessmentScenario(assessmentView({
      acceptance: acceptanceView({
        outcome: "not_met",
        counts: { total: 3, met: 1, not_met: 1, unverified: 1 },
        requires_human: false
      })
    }));
  }

  if (scenario === "acceptance-incomplete-needs-human") {
    return assessmentScenario(assessmentView({ acceptance: acceptanceView() }));
  }

  if (scenario === "acceptance-no-structured-criteria") {
    return assessmentScenario(assessmentView({
      acceptance: acceptanceView({
        availability: "not_assessable",
        availability_reason: "no_structured_criteria",
        outcome: null,
        counts: { total: 0, met: 0, not_met: 0, unverified: 0 },
        requires_human: false
      })
    }));
  }

  if (scenario === "acceptance-unknown-population") {
    return assessmentScenario(assessmentView({
      acceptance: acceptanceView({
        availability: "not_assessable",
        availability_reason: "continuity_not_declared",
        outcome: null,
        counts: null,
        requires_human: null
      })
    }));
  }

  if (scenario === "acceptance-nested-cause") {
    return assessmentScenario(assessmentView({
      acceptance: acceptanceView({
        availability: "not_assessable",
        availability_reason: "predecessor_unavailable",
        unavailable_cause: "continuity_legacy_unknown",
        unavailable_at_turn_number: 2,
        outcome: null,
        counts: null,
        requires_human: null
      })
    }));
  }

  if (scenario === "acceptance-structural") {
    return assessmentScenario(assessmentView({
      acceptance: acceptanceView({
        availability: "not_assessable",
        availability_reason: "final_state_inconsistent",
        outcome: null,
        counts: null,
        requires_human: null
      })
    }));
  }

  if (scenario === "assessment-not-provided") {
    return assessmentScenario(assessmentView({
      criteria: {
        state: "not_provided", recorded: true,
        snapshot_id: "acs_" + "a".repeat(26),
        criteria_fingerprint: "c".repeat(64),
        criterion_count: 0, items: []
      },
      evaluation: {
        state: "recorded", recorded: true,
        evaluation_id: "evl_" + "b".repeat(26), evaluator_version: 1,
        criteria_state: "not_provided",
        criteria_snapshot_id: "acs_" + "a".repeat(26),
        criteria_fingerprint: "c".repeat(64),
        assembler_version: 3, evidence_input_fingerprint: "f".repeat(64),
        result_count: 0, evaluation_fingerprint: "d".repeat(64), results: []
      }
    }));
  }

  if (scenario === "assessment-legacy-unknown") {
    return assessmentScenario(assessmentView({
      criteria: {
        state: "legacy_unknown", recorded: false, snapshot_id: null,
        criteria_fingerprint: null, criterion_count: 0, items: []
      },
      evaluation: {
        state: "criteria_legacy_unknown", recorded: false,
        evaluation_id: null, evaluator_version: null, criteria_state: null,
        criteria_snapshot_id: null, criteria_fingerprint: null,
        assembler_version: null, evidence_input_fingerprint: null,
        result_count: 0, evaluation_fingerprint: null, results: []
      }
    }));
  }

  if (scenario === "assessment-evaluation-not-recorded") {
    return assessmentScenario(assessmentView({
      evaluation: {
        state: "not_recorded", recorded: false,
        evaluation_id: null, evaluator_version: null, criteria_state: null,
        criteria_snapshot_id: null, criteria_fingerprint: null,
        assembler_version: null, evidence_input_fingerprint: null,
        result_count: 0, evaluation_fingerprint: null, results: []
      }
    }));
  }

  if (scenario === "assessment-turn-not-closed") {
    return assessmentScenario(assessmentView({
      evaluation: {
        state: "turn_not_closed", recorded: false,
        evaluation_id: null, evaluator_version: null, criteria_state: null,
        criteria_snapshot_id: null, criteria_fingerprint: null,
        assembler_version: null, evidence_input_fingerprint: null,
        result_count: 0, evaluation_fingerprint: null, results: []
      }
    }));
  }

  if (scenario === "assessment-second-turn") {
    return mount({
      initial: listPayload([taskPayload({ task_id: "task_a", state: "completed" })]),
      detail: taskPayload({ task_id: "task_a", state: "completed", result: "Done." }),
      assessmentPayload: assessmentView(),
      /* Two turns, so the button advances rather than wrapping back to one.
         `turnsSoFar` reads this from the result payload, exactly as the evidence
         button does — the client never guesses a turn count. */
      resultPayload: { task_id: "task_a", turn_count: 2 }
    }).then(function () {
      fire("click", openButton("task_a"));
      return drain().then(function () {
        fire("click", button("taskShowResult"));
        return drain().then(function () {
          fire("click", button("taskShowAssessment"));
          return drain().then(function () {
            fire("click", button("taskShowAssessment"));
            return drain().then(function () {
              return { html: html(), requests: record.requests.slice() };
            });
          });
        });
      });
    });
  }

  if (scenario === "evidence-shows-all-three-relationships") {
    return evidenceScenario(evidenceBundle());
  }

  if (scenario === "evidence-operation-differs") {
    return evidenceScenario(evidenceBundle({
      relationships: [
        {
          path: "src/foo.py", relationship: "claim_conflict",
          claim_ids: ["chg_one"], claim_operations: ["modified"],
          observation_refs: ["evt7.0"], path_agreement: true,
          operation_agreement: "false", observed_kinds: ["deleted"],
          claim_count: 1, observation_count: 1, sources_truncated: false
        }
      ],
      observations: [
        {
          reference: "evt7.0", event_sequence: 7, evidence_index: 0,
          path: "src/foo.py", source: "git_observed", evidence_type: "file",
          operation: "git status", result: "changed", verified: true,
          change_kind: "deleted", previous_path: null
        }
      ],
      limitations: []
    }));
  }

  if (scenario === "evidence-rename-observed") {
    return evidenceScenario(evidenceBundle({
      observations: [
        {
          reference: "evt7.0", event_sequence: 7, evidence_index: 0,
          path: "src/new.py", source: "git_observed", evidence_type: "file",
          operation: "git status", result: "changed", verified: true,
          change_kind: "renamed", previous_path: "src/old.py", change_status: "RM"
        }
      ],
      relationships: [
        {
          path: "src/new.py", relationship: "path_agreed",
          claim_ids: ["chg_one"], claim_operations: ["renamed"],
          observation_refs: ["evt7.0"], path_agreement: true,
          operation_agreement: "true", observed_kinds: ["renamed"],
          claim_count: 1, observation_count: 1, sources_truncated: false
        }
      ],
      limitations: []
    }));
  }

  if (scenario === "evidence-machine-incomplete") {
    return evidenceScenario(evidenceBundle({
      machine_observations_complete: false,
      limitations: ["machine_observations_incomplete"]
    }));
  }

  if (scenario === "evidence-legacy-turn") {
    return evidenceScenario(evidenceBundle({
      turn_attribution: "legacy_unknown",
      opened_after_event_sequence: null,
      closed_through_event_sequence: null,
      observations: [],
      ingestion: {
        state: "legacy_unknown", submitted: 0, accepted: 0, rejected: 0,
        truncated: false, reason_counts: {}, sequences: []
      },
      relationships: [
        {
          path: "src/foo.py", relationship: "claim_only",
          claim_ids: ["chg_one"], claim_operations: ["modified"],
          observation_refs: [], path_agreement: false,
          operation_agreement: "unknown", claim_count: 1,
          observation_count: 0, sources_truncated: false
        }
      ],
      limitations: ["legacy_turn_attribution_unavailable"]
    }));
  }

  if (scenario === "evidence-missing-ingestion") {
    return evidenceScenario(evidenceBundle({
      ingestion: {
        state: "ingestion_missing", submitted: 0, accepted: 0, rejected: 0,
        truncated: false, reason_counts: {}, sequences: []
      },
      limitations: ["claim_ingestion_record_missing"]
    }));
  }

  if (scenario === "evidence-clean-tree") {
    return evidenceScenario(evidenceBundle({
      repository_reported_clean: true,
      observations: [],
      relationships: [],
      claims: [],
      limitations: []
    }));
  }

  if (scenario === "evidence-refusal-is-not-success") {
    return evidenceScenario(evidenceBundle(), { evidenceRefuse: 404 });
  }

  if (scenario === "evidence-double-tap-sends-one-request") {
    return mount({
      initial: listPayload([taskPayload({ task_id: "task_e", state: "completed" })]),
      detail: taskPayload({ task_id: "task_e", state: "completed", result: "Done." }),
      evidencePayload: evidenceBundle(),
      hang: true,
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_e"));
      return drain().then(function () {
        const before = record.requests.filter(
          (r) => r.path.indexOf("/evidence") !== -1
        ).length;
        fire("click", button("taskShowEvidence"));
        fire("click", button("taskShowEvidence"));
        fire("click", button("taskShowEvidence"));
        return drain().then(function () {
          return {
            before: before,
            after: record.requests.filter(
              (r) => r.path.indexOf("/evidence") !== -1
            ).length,
            html: html()
          };
        });
      });
    });
  }

  if (scenario === "evidence-is-not-fetched-until-asked") {
    return mount({
      initial: listPayload([taskPayload({ task_id: "task_e", state: "completed" })]),
      detail: taskPayload({ task_id: "task_e", state: "completed", result: "Done." }),
      evidencePayload: evidenceBundle(),
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", openButton("task_e"));
      return drain().then(function () {
        advance(60000);
        return drain().then(function () {
          return {
            requests: record.requests
              .filter((r) => r.path.indexOf("/evidence") !== -1).length,
            html: html()
          };
        });
      });
    });
  }

  /* -- requirement authoring (M2K PR24) ------------------------------------
   *
   * The properties here are about the *request*, not the pixels: whether a
   * declaration was sent at all, whether an empty one is distinguishable from an
   * absent one, and whether anything in the panel manufactures a mode nobody
   * chose. Every one of those is invisible to a scan of the source and visible
   * in `writes()`.
   */

  const CREATE = "taskAuthCreate";
  const FOLLOWUP = "taskAuthFollowup";

  /* Operating a control that is not on screen must fail loudly. `field()` mints
     an element on demand, which is exactly right for a stub and exactly wrong
     for a test that means to assert the control exists — it would let a
     scenario "type into" a box the panel never rendered and pass. */
  function control(id) {
    if (!existing(id)) { throw new Error("no such control: " + id); }
    return elements[id];
  }

  function chooseMode(prefix, modeId) {
    control(prefix + modeId);
    fire("change", button(prefix + modeId));
  }

  function addRow(prefix) {
    control(prefix + "Add");
    fire("click", button(prefix + "Add"));
  }

  function setRowType(prefix, index, value) {
    const id = prefix + "Kind" + String(index);
    control(id);
    fire("change", field(id, value));
  }

  function setRowText(prefix, index, name, value) {
    const id = prefix + name + String(index);
    control(id);
    fire("input", field(id, value));
  }

  function setRowOperation(prefix, index, value) {
    const id = prefix + "Op" + String(index);
    control(id);
    fire("change", field(id, value));
  }

  function createPosts() {
    return writes().filter((w) => w.path === "/api/tasks");
  }

  function followupPosts() {
    return writes().filter((w) => w.path.indexOf("/followups") !== -1);
  }

  /* A create scenario: open the composer, write a prompt, run `build`, send. */
  function createScenario(build, extra) {
    return mount(Object.assign({
      hang: true,
      result: () => ({ payload: {} })
    }, extra || {})).then(function () {
      fire("click", button("taskCompose"));
      return drain();
    }).then(function () {
      field("taskPrompt", "bir görev");
      fire("input", field("taskPrompt", "bir görev"));
      return Promise.resolve(build ? build() : null);
    }).then(function () {
      return drain();
    }).then(function () {
      fire("click", button("taskStart"));
      return drain().then(function () {
        return { posts: createPosts(), html: html() };
      });
    });
  }

  /* A follow-up scenario against a task whose latest turn recorded criteria, so
     an anchor can actually be read. Both routes the anchor needs already exist
     and are already device-token-only; the stub answers them as the real ones
     do. */
  function followupScenario(build, extra) {
    const ready = readyTask();
    const options = extra || {};
    return mount(Object.assign({
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      onGet(pathname) {
        if (pathname.indexOf("/result") !== -1) {
          return Promise.resolve({
            ok: true, status: 200,
            payload: { result: {
              task_id: "task_ready", turn_number: options.turnCount || 1,
              turn_count: options.turnCount || 1, turn_count_known: true
            } }
          });
        }
        return null;
      },
      assessmentPayload: options.assessment || assessmentView({ task_id: "task_ready" }),
      result: () => ({ payload: { task: ready } })
    }, options.behaviour || {})).then(function () {
      fire("click", openButton("task_ready"));
      return drain();
    }).then(function () {
      return Promise.resolve(build ? build() : null);
    }).then(function () {
      return drain();
    }).then(function () {
      field("taskFollowupText", "devam et");
      fire("click", button("taskFollowupSend"));
      return drain().then(function () {
        return { posts: followupPosts(), html: html(), requests: record.requests.slice() };
      });
    });
  }

  /* -- create ------------------------------------------------------------- */

  if (scenario === "authoring-create-not-declared") {
    /* The form is left alone. The request must be byte-for-byte the one this
       panel sent before PR24 — no `continuity` key at all, and no `criteria`
       key either. An omitted declaration is `not_declared` in the store, and
       nothing here may turn "this is turn one" into `root`. */
    return createScenario(null);
  }

  if (scenario === "authoring-create-root-with-state-criterion") {
    return createScenario(function () {
      chooseMode(CREATE, "Root");
      addRow(CREATE);
      setRowType(CREATE, 0, "evidence:path_exists");
      setRowText(CREATE, 0, "Path", "a.txt");
    });
  }

  if (scenario === "authoring-create-root-with-no-criteria") {
    /* Explicit `root`, empty composer. The declaration is real and the criteria
       set is genuinely empty — which the server records as `not_provided`, and
       which reads afterwards as `no_structured_criteria` rather than as nobody
       having declared anything. So `criteria` must be present and `[]`, not
       absent. */
    return createScenario(function () { chooseMode(CREATE, "Root"); });
  }

  if (scenario === "authoring-create-criteria-without-a-declaration") {
    /* The two fields are independent on the wire. Requirements with no lineage
       declaration is a legitimate thing to say, and the panel must not couple
       them by inventing a `root` to carry them. */
    return createScenario(function () {
      addRow(CREATE);
      setRowType(CREATE, 0, "evidence:path_changed");
      setRowText(CREATE, 0, "Path", "src/app.py");
    });
  }

  if (scenario === "authoring-create-offers-only-first-turn-modes") {
    return mount({ result: () => ({ payload: {} }) }).then(function () {
      fire("click", button("taskCompose"));
      return drain().then(function () {
        return {
          html: html(),
          hasRoot: !!existing("taskAuthCreateRoot"),
          hasNotDeclared: !!existing("taskAuthCreateNotDeclared"),
          hasExtend: !!existing("taskAuthCreateExtend"),
          hasReplace: !!existing("taskAuthCreateReplace"),
          hasRevise: !!existing("taskAuthCreateRevise")
        };
      });
    });
  }

  if (scenario === "authoring-every-predicate") {
    /* All six shapes the model owns, in one snapshot, in the order they were
       added. Ordinal is positional and part of the stored fingerprint, so the
       order sent has to be the order on screen. */
    return createScenario(function () {
      chooseMode(CREATE, "Root");
      const rows = [
        ["evidence:path_changed", { Path: "one.txt" }],
        ["evidence:path_operation", { Path: "two.txt", op: "created" }],
        ["evidence:rename", { Path: "three.txt", To: "four.txt" }],
        ["manual", { Desc: "somebody reads the page" }],
        ["evidence:path_exists", { Path: "five.txt" }],
        ["evidence:path_absent", { Path: "six.txt" }],
        /* A second `path_operation`, so the operation select is proved to carry
           a chosen value rather than the one a new row happens to start on —
           and so `deleted` is visibly not the same thing as `path_absent`. */
        ["evidence:path_operation", { Path: "seven.txt", op: "deleted" }]
      ];
      rows.forEach(function (entry, index) {
        addRow(CREATE);
        setRowType(CREATE, index, entry[0]);
        Object.keys(entry[1]).forEach(function (name) {
          if (name === "op") { setRowOperation(CREATE, index, entry[1][name]); return; }
          setRowText(CREATE, index, name, entry[1][name]);
        });
      });
    });
  }

  if (scenario === "authoring-predicate-fields-are-bounded-by-predicate") {
    /* A row draws only the fields its predicate owns. A `path_exists` row with
       a destination box or an operation select would be a control whose only
       outcome is a refusal — and would be the place an invalid combination
       came from. */
    return mount({ result: () => ({ payload: {} }) }).then(function () {
      fire("click", button("taskCompose"));
      return drain().then(function () {
        addRow(CREATE);
        return drain();
      }).then(function () {
        const seen = {};
        [
          "evidence:path_changed", "evidence:path_operation", "evidence:rename",
          "manual", "evidence:path_exists", "evidence:path_absent"
        ].forEach(function (value) {
          setRowType(CREATE, 0, value);
          seen[value] = {
            path: !!existing(CREATE + "Path0"),
            to: !!existing(CREATE + "To0"),
            operation: !!existing(CREATE + "Op0"),
            description: !!existing(CREATE + "Desc0")
          };
        });
        return { seen: seen, html: html() };
      });
    });
  }

  if (scenario === "authoring-rows-keep-their-order") {
    return createScenario(function () {
      chooseMode(CREATE, "Root");
      ["zebra.txt", "apple.txt", "mango.txt"].forEach(function (path, index) {
        addRow(CREATE);
        setRowType(CREATE, index, "evidence:path_exists");
        setRowText(CREATE, index, "Path", path);
      });
      /* Remove the middle one. What is left must still be in insertion order,
         renumbered by position rather than resorted by anything. */
      fire("click", button(CREATE + "Remove1"));
    });
  }

  /* -- follow-up ---------------------------------------------------------- */

  if (scenario === "authoring-followup-not-declared") {
    return followupScenario(null);
  }

  if (scenario === "authoring-followup-extend") {
    return followupScenario(function () {
      chooseMode(FOLLOWUP, "Extend");
      return drain().then(function () {
        addRow(FOLLOWUP);
        setRowType(FOLLOWUP, 0, "evidence:path_absent");
        setRowText(FOLLOWUP, 0, "Path", "b.txt");
      });
    });
  }

  if (scenario === "authoring-followup-replace") {
    return followupScenario(function () {
      chooseMode(FOLLOWUP, "Replace");
      return drain().then(function () {
        addRow(FOLLOWUP);
        setRowType(FOLLOWUP, 0, "evidence:path_exists");
        setRowText(FOLLOWUP, 0, "Path", "replacement.txt");
      });
    });
  }

  if (scenario === "authoring-followup-offers-no-root") {
    const ready = readyTask();
    return mount({
      realAdapter: true,
      initial: listPayload([ready]),
      detail: ready,
      result: () => ({ payload: { task: ready } })
    }).then(function () {
      fire("click", openButton("task_ready"));
      return drain().then(function () {
        return {
          html: html(),
          hasNotDeclared: !!existing("taskAuthFollowupNotDeclared"),
          hasExtend: !!existing("taskAuthFollowupExtend"),
          hasReplace: !!existing("taskAuthFollowupReplace"),
          hasRoot: !!existing("taskAuthFollowupRoot"),
          hasRevise: !!existing("taskAuthFollowupRevise")
        };
      });
    });
  }

  if (scenario === "authoring-anchor-is-read-not-typed") {
    /* The one property that decides whether this is a user interface: the
       predecessor snapshot id reaches the request without anybody typing it.
       There is no control for it, and the value that is sent is the one the
       assessment route answered with. */
    let beforeSubmit = null;
    let identityInputs = null;
    return followupScenario(function () {
      chooseMode(FOLLOWUP, "Extend");
      return drain().then(function () {
        /* Captured while the form is still on screen: what the anchor is has to
           be visible *before* the request goes, not reconstructable after it. */
        beforeSubmit = html();
        /* Every control the panel is currently rendering. None of them may be a
           box for an internal identifier — that is the difference between a user
           interface and a JSON editor with a nicer font. */
        identityInputs = Object.keys(elements).filter(function (id) {
          return /snapshot|predecessor|criterion.?id/i.test(id);
        });
      });
    }).then(function (payload) {
      payload.htmlBeforeSubmit = beforeSubmit;
      payload.identityInputs = identityInputs;
      return payload;
    });
  }

  if (scenario === "authoring-legacy-turn-cannot-be-continued") {
    /* A turn that predates criteria persistence publishes no snapshot id. There
       is nothing to anchor to, so the panel says so rather than sending a
       declaration with a missing field. */
    return followupScenario(function () {
      chooseMode(FOLLOWUP, "Extend");
      return drain();
    }, {
      assessment: assessmentView({
        task_id: "task_ready",
        criteria: {
          state: "legacy_unknown", recorded: false, snapshot_id: null,
          criteria_fingerprint: null, criterion_count: 0, items: []
        }
      })
    });
  }

  if (scenario === "authoring-declaration-does-not-cross-tasks") {
    const first = readyTask("task_one");
    const second = readyTask("task_two");
    return mount({
      realAdapter: true,
      initial: listPayload([first, second]),
      detail: first,
      result: () => ({ payload: { task: first } })
    }).then(function () {
      fire("click", openButton("task_one"));
      return drain();
    }).then(function () {
      addRow(FOLLOWUP);
      setRowType(FOLLOWUP, 0, "evidence:path_exists");
      setRowText(FOLLOWUP, 0, "Path", "only-for-task-one.txt");
      return drain();
    }).then(function () {
      fire("click", button("taskBack"));
      return drain();
    }).then(function () {
      fire("click", openButton("task_two"));
      return drain().then(function () {
        return {
          html: html(),
          rowsOnSecondTask: !!existing("taskAuthFollowupPath0")
        };
      });
    });
  }

  /* -- refusals ----------------------------------------------------------- */

  function refusedScenario(code, detail) {
    return createScenario(function () {
      chooseMode(CREATE, "Root");
      addRow(CREATE);
      setRowType(CREATE, 0, "evidence:path_exists");
      setRowText(CREATE, 0, "Path", "a.txt");
    }, {
      hang: false,
      onWrite(pathname) {
        if (pathname !== "/api/tasks") { return null; }
        return Promise.resolve({
          ok: false, status: 422,
          payload: { error: {
            code: code,
            message: "that was refused",
            detail: detail
          } }
        });
      }
    });
  }

  if (scenario === "authoring-criteria-refusal") {
    return refusedScenario("task_criteria_invalid", "criterion_path_invalid");
  }

  if (scenario === "authoring-continuity-refusal") {
    return refusedScenario("task_continuity_invalid", "continuity_mode_invalid");
  }

  if (scenario === "authoring-stale-anchor-refusal") {
    /* The concurrency case. A form is displayed, another caller creates a turn,
       and the anchor this declaration names is no longer where it was. The
       server refuses honestly; the panel must say so, keep the composer, and —
       the property that matters — must not send a second, altered request. */
    return refusedScenario(
      "task_continuity_invalid", "continuity_predecessor_unknown"
    );
  }

  if (scenario === "authoring-refusal-keeps-the-composer") {
    return refusedScenario(
      "task_criteria_invalid", "criterion_path_required"
    ).then(function (payload) {
      return {
        posts: payload.posts,
        html: payload.html,
        pathStillThere: valueOf("taskAuthCreatePath0"),
        rowStillThere: !!existing("taskAuthCreateKind0"),
        modeStillRoot: payload.html.indexOf(
          'id="taskAuthCreateRoot" class="task-continuity-input" checked'
        ) !== -1
      };
    });
  }

  /* -- idempotency -------------------------------------------------------- */

  if (scenario === "authoring-double-tap-sends-one-request") {
    return mount({
      hang: true,
      result: () => ({ payload: {} })
    }).then(function () {
      fire("click", button("taskCompose"));
      return drain();
    }).then(function () {
      field("taskPrompt", "iki kere");
      fire("input", field("taskPrompt", "iki kere"));
      chooseMode(CREATE, "Root");
      addRow(CREATE);
      setRowType(CREATE, 0, "evidence:path_exists");
      setRowText(CREATE, 0, "Path", "a.txt");
      return drain();
    }).then(function () {
      fire("click", button("taskStart"));
      fire("click", button("taskStart"));
      fire("click", button("taskStart"));
      return drain().then(function () {
        return { posts: createPosts() };
      });
    });
  }

  if (scenario === "authoring-retry-reuses-its-request-id") {
    /* Same declaration, pressed twice after a refusal: one key, because the key
       is a function of what was authored rather than a fresh value per attempt.
       A new key on every attempt is how a timeout becomes two turns. */
    let attempts = 0;
    return createScenario(function () {
      chooseMode(CREATE, "Root");
      addRow(CREATE);
      setRowType(CREATE, 0, "evidence:path_exists");
      setRowText(CREATE, 0, "Path", "a.txt");
    }, {
      hang: false,
      onWrite(pathname) {
        if (pathname !== "/api/tasks") { return null; }
        attempts += 1;
        return Promise.resolve({
          ok: false, status: 503,
          payload: { error: { code: "task_adapter_error", message: "not now" } }
        });
      }
    }).then(function () {
      fire("click", button("taskStart"));
      return drain().then(function () {
        return { requestIds: createPosts().map((w) => w.body.client_request_id) };
      });
    });
  }

  if (scenario === "authoring-an-edited-declaration-gets-a-new-request-id") {
    /* The other half, and the reason the declaration is in the key at all: the
       server binds a key to a payload hash, so a changed requirement arriving
       under the old key would be answered as a conflict rather than as the
       different request it is. */
    return createScenario(function () {
      chooseMode(CREATE, "Root");
      addRow(CREATE);
      setRowType(CREATE, 0, "evidence:path_exists");
      setRowText(CREATE, 0, "Path", "a.txt");
    }, {
      hang: false,
      onWrite(pathname) {
        if (pathname !== "/api/tasks") { return null; }
        return Promise.resolve({
          ok: false, status: 503,
          payload: { error: { code: "task_adapter_error", message: "not now" } }
        });
      }
    }).then(function () {
      /* One more requirement, same prompt. */
      addRow(CREATE);
      setRowType(CREATE, 1, "evidence:path_absent");
      setRowText(CREATE, 1, "Path", "b.txt");
      return drain();
    }).then(function () {
      fire("click", button("taskStart"));
      return drain().then(function () {
        return { requestIds: createPosts().map((w) => w.body.client_request_id) };
      });
    });
  }

  /* -- the loop ----------------------------------------------------------- */

  if (scenario === "authoring-reaches-acceptance") {
    /* The whole point of the milestone, end to end through the shipped request
       layer: a person declares a root and a requirement, the turn runs, and the
       acceptance section that PR22 already built answers for that turn. No
       second acceptance viewer, and no developer tooling anywhere in the path. */
    const created = taskPayload({
      task_id: "task_a", state: "completed", result: "Bitti.",
      capabilities: SDK_CAPABILITIES
    });
    return mount({
      initial: listPayload([]),
      detail: created,
      assessmentPayload: assessmentView({
        task_id: "task_a",
        turn_number: 1,
        acceptance: acceptanceView({
          availability: "assessable", availability_reason: null,
          outcome: "met", requires_human: false,
          counts: { total: 1, met: 1, not_met: 0, unverified: 0 }
        }),
        criteria: {
          state: "present", recorded: true,
          snapshot_id: "acs_" + "a".repeat(26),
          criteria_fingerprint: "c".repeat(64), criterion_count: 1,
          items: [{
            criterion_id: "acr_1", ordinal: 1, kind: "evidence",
            predicate: "path_exists", path: "a.txt", to_path: null,
            operation: null, description: null
          }]
        },
        evaluation: {
          state: "recorded", recorded: true,
          evaluation_id: "evl_" + "b".repeat(26), evaluator_version: 1,
          criteria_state: "present", criteria_snapshot_id: "acs_" + "a".repeat(26),
          criteria_fingerprint: "c".repeat(64), assembler_version: 3,
          evidence_input_fingerprint: "f".repeat(64), result_count: 1,
          evaluation_fingerprint: "d".repeat(64),
          results: [{
            criterion_id: "acr_1", ordinal: 1, result: "met",
            reason: "machine_state_observed"
          }]
        }
      }),
      result: (body, pathname) => ({
        payload: pathname === "/api/tasks"
          ? { created: true, task: created }
          : { task: created }
      })
    }).then(function () {
      fire("click", button("taskCompose"));
      return drain();
    }).then(function () {
      field("taskPrompt", "a.txt oluştur");
      fire("input", field("taskPrompt", "a.txt oluştur"));
      chooseMode(CREATE, "Root");
      addRow(CREATE);
      setRowType(CREATE, 0, "evidence:path_exists");
      setRowText(CREATE, 0, "Path", "a.txt");
      return drain();
    }).then(function () {
      fire("click", button("taskStart"));
      return drain();
    }).then(function () {
      fire("click", button("taskShowAssessment"));
      return drain().then(function () {
        return { posts: createPosts(), html: html() };
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
