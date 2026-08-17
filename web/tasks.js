/* Cofferdam — the Tasks panel (M2F Agent Task Core).
 *
 * Its own file beside audio.js, spotify.js and youtube.js, and the separation is
 * the same one the backend makes: those three control a *device*, this one
 * describes *work*. A task is not a stream, a player or a volume — it has a
 * state, a history, and something a person may need to do about it.
 *
 * The rules this panel is written to, all inherited from milestones that earned
 * them and one that is new here:
 *
 *   1. **Nothing is claimed until the server has observed it.** Every write
 *      returns the task the server actually stored, and this file renders that.
 *      A task does not become "running" because a button was pressed.
 *   2. **One action at a time, bounded.** Controls disable while a request is in
 *      flight and a timer gives the panel back if the request never answers.
 *   3. **An older answer never wins.** Every request that can produce state
 *      carries a monotonic generation; a response older than the newest applied
 *      one is dropped. The Spotify milestone earned this in real validation.
 *   4. **Nothing is logged.** There is no `console` call in this file. A task's
 *      prompt and result are somebody's private thinking, and a browser console
 *      is a surface neither of us controls. This is stricter here than anywhere
 *      else in the PWA, because this panel handles the most personal content in
 *      the product.
 *   5. **The default view is not a terminal.** A task shows its state, what it
 *      last did, and its result. The raw event stream is available behind an
 *      Advanced disclosure, because a log is where you go when the summary is
 *      not enough — not the first thing you have to read.
 */
(function (global) {
  "use strict";

  var deps = null;

  /* Task state changes at human speed, not animation speed. Ten seconds is
     often enough to see a task move without asking the workstation to describe
     itself constantly to a phone lying on a desk. */
  var POLL_MS = 10000;

  /* A faster poll while something is actually moving. Still conservative: this
     is a list of rows, not a progress bar, and the honest reading is "at last
     check" either way. */
  var ACTIVE_POLL_MS = 4000;

  var ACTION_TIMEOUT_MS = 20000;

  /* Creating a task is the slow one: the server resolves the project, verifies
     the root on disk, writes durably, and calls the adapter's start before it
     answers. */
  var CREATE_TIMEOUT_MS = 45000;

  var MAX_PROMPT_CHARS = 8000;
  var MAX_FOLLOWUP_CHARS = 4000;

  /* The two things a person can be asked to write about an existing task, named
     as a closed set because everything below is keyed by one of them.

     They are separate all the way down — separate drafts, separate request ids,
     separate routes — for the same reason the backend keeps them separate: a
     follow-up is a new instruction and an answer resolves something the agent is
     blocked on. A single "message" concept here would have to decide which at
     the moment of sending, which is the worst possible moment to decide it. */
  var OP_FOLLOWUP = "followup";
  var OP_CLARIFICATION = "clarification";

  /* The longest draft this panel will keep. Equal to the server's own follow-up
     bound, so a draft that is kept is a draft that could be sent — keeping more
     would mean storing text whose only future is a 422. */
  var MAX_DRAFT_CHARS = MAX_FOLLOWUP_CHARS;

  /* The storage key prefix. Namespaced so this panel's keys are recognisable,
     removable, and impossible to confuse with the token app.js stores.

     **What may be under one of these keys is a closed list: text a person typed
     into a task box, and nothing else.** No token, no provider session id, no
     provider event, no tool approval, no result. That is not a convention this
     file merely follows — `writeDraft` is the only writer, it takes a task id, an
     operation from the two above, and a string, and there is no second writer to
     add anything else through. */
  var DRAFT_PREFIX = "cofferdam.taskdraft.";

  /* States, mirrored from the backend's closed vocabulary. Rendering branches on
     these strings, so an unrecognised one must degrade to something honest
     rather than to an empty row — see `stateLabel`. */
  var TERMINAL = ["completed", "failed", "cancelled", "interrupted"];

  var snapshot = null;          /* the task list payload */
  var detail = null;            /* the open task, in full */
  var detailEvents = [];        /* its event history, oldest first */
  var detailQuestions = [];     /* the questions it is waiting on, if any */
  var detailResult = null;      /* the latest completed turn, when asked for */
  var detailEvidence = null;    /* one turn's evidence bundle, when asked for */
  var evidenceTurn = null;      /* which turn `detailEvidence` describes */
  var detailAssessment = null;  /* one turn's criteria + evaluation, when asked */
  var assessmentTurn = null;    /* which turn `detailAssessment` describes */
  var chosenOptions = {};       /* question_id -> the option ids ticked */
  var adapters = null;
  var projects = null;
  var loadError = null;
  var openTaskId = null;
  var advancedOpen = false;
  var composerOpen = false;
  var draft = { projectId: null, adapterId: null, prompt: "" };
  var formError = null;

  var timer = null;
  var timerInterval = null;
  var visibilityHandler = null;
  var pending = null;
  var pendingTimer = null;
  var actionError = null;
  var actionNote = null;
  var stopped = false;

  /* Response ordering, exactly as the Spotify and YouTube panels do it — but
     with **one counter per resource**, which those panels did not need because
     they only ever fetch one thing.

     The list and the open task detail are refreshed together on every tick, and
     they are different resources. A newer *list* response says nothing about
     whether a *detail* response is stale. Sharing one counter made it say
     exactly that: the poll issues the detail request first and the list request
     second, so the list holds the higher generation, and whenever the list
     response landed first the detail response was discarded as "old".

     That was not an occasional race. Reading a task detail asks the adapter
     what it saw, which for the Claude adapter runs Git probes, so the detail
     response is reliably the slower of the two — and the detail view sat on
     `running` through every poll while the backend had long since moved on.
     A manual page reload fixed it because a fresh mount has no competing list
     response in flight.

     Two counters. Each resource can still refuse a response older than the one
     it has already applied, which is the property that actually matters. */
  var refreshGeneration = 0;
  var appliedGeneration = 0;
  var detailGeneration = 0;
  var appliedDetailGeneration = 0;
  var inflightRefresh = null;

  /* Where the caret was, so a re-render can put it back. The draft *text* lives
     in browser storage — see the draft section below — and is deliberately not
     written to the task database on every keystroke: a draft is not a follow-up,
     and a half-typed sentence is not something to persist into an append-only
     history. */
  var followupFocus = null;

  /* The last markup written to the panel. Re-rendering identical markup would
     destroy and rebuild every node for no reason — including the textarea the
     person is typing into — so an unchanged render is skipped entirely. This is
     what makes an idle poll cost nothing visible. */
  var lastMarkup = null;


  function esc(value) { return deps.escapeHtml(value); }

  /* --------------------------------------------------------------- drafts
   *
   * Unsent text somebody typed, kept so that backgrounding a phone, losing a
   * connection or reloading the PWA does not throw it away. Until M2I PR4 these
   * lived only in `followupDrafts` above, which survived a poll and nothing
   * else: iOS discards a backgrounded tab's page whenever it feels like it, and
   * the person who came back to an empty box had no way to know their sentence
   * had ever existed.
   *
   * **What goes in browser storage, exactly.** Text a person typed into a task
   * box, under a key naming the task and which of the two boxes it was. That is
   * the whole list. There is no code path here that writes a token — app.js owns
   * the token and this file never reads it — no provider session id, no provider
   * event, no tool approval, and no result. A task's *result* is the workstation's
   * to hold; a half-written question is only ever the person's.
   *
   * Every access is wrapped, for the reason app.js records: on iOS Safari the
   * `localStorage` property access itself throws under Private Browsing and some
   * MDM configurations. When storage is unusable the drafts live in memory for
   * the session, exactly as the token does — the panel still works, it just
   * cannot survive a reload, and nothing pretends otherwise.
   */

  var draftMemory = {};
  var storageWorks = null;

  function storageAvailable() {
    if (storageWorks !== null) { return storageWorks; }
    try {
      var probe = DRAFT_PREFIX + "probe";
      global.localStorage.setItem(probe, "1");
      global.localStorage.removeItem(probe);
      storageWorks = true;
    } catch (error) {
      storageWorks = false;
    }
    return storageWorks;
  }

  /* One key per task **and** per operation. Both halves are load-bearing: a key
     without the task id carries one task's words into another task's box, and a
     key without the operation lets a half-typed answer to a question reappear as
     a follow-up after the question has been answered and closed. */
  function draftKey(taskId, operation) {
    return DRAFT_PREFIX + operation + "." + taskId;
  }

  function readDraft(taskId, operation) {
    if (!taskId) { return ""; }
    var key = draftKey(taskId, operation);
    if (Object.prototype.hasOwnProperty.call(draftMemory, key)) {
      return draftMemory[key];
    }
    if (!storageAvailable()) { return ""; }
    try {
      var stored = global.localStorage.getItem(key);
      return typeof stored === "string" ? stored.slice(0, MAX_DRAFT_CHARS) : "";
    } catch (error) {
      storageWorks = false;
      return "";
    }
  }

  function writeDraft(taskId, operation, text) {
    if (!taskId) { return; }
    var key = draftKey(taskId, operation);
    /* Bounded here rather than trusted from the caller. A textarea has a
       `maxlength`, and a `maxlength` is a hint a paste can exceed. */
    var value = String(text || "").slice(0, MAX_DRAFT_CHARS);
    if (!value) { clearDraft(taskId, operation); return; }
    draftMemory[key] = value;
    if (!storageAvailable()) { return; }
    try {
      global.localStorage.setItem(key, value);
    } catch (error) {
      /* A quota refusal is not a data-loss event: the memory copy above is
         already holding it for this session. */
      storageWorks = false;
    }
  }

  function clearDraft(taskId, operation) {
    if (!taskId) { return; }
    var key = draftKey(taskId, operation);
    delete draftMemory[key];
    if (!storageAvailable()) { return; }
    try {
      global.localStorage.removeItem(key);
    } catch (error) {
      storageWorks = false;
    }
  }

  /* Every draft this panel owns, dropped. Called on sign-out, where the rule is
     the same one that governs everything else here: what somebody asked the
     workstation to do is the most personal content in this product, and a
     signed-out device keeps none of it.

     It enumerates by prefix rather than by remembering which keys it wrote,
     because the keys that matter most to remove are the ones written by a
     *previous* page load — the ones an in-memory list would have forgotten. */
  function clearAllDrafts() {
    draftMemory = {};
    if (!storageAvailable()) { return; }
    try {
      var doomed = [];
      for (var index = 0; index < global.localStorage.length; index += 1) {
        var key = global.localStorage.key(index);
        if (typeof key === "string" && key.indexOf(DRAFT_PREFIX) === 0) {
          doomed.push(key);
        }
      }
      for (var removed = 0; removed < doomed.length; removed += 1) {
        global.localStorage.removeItem(doomed[removed]);
      }
    } catch (error) {
      storageWorks = false;
    }
  }

  /* ------------------------------------------------------------ request ids
   *
   * One key per task and operation, kept until the server has actually accepted
   * the thing it identifies.
   *
   * The previous shape was a single module-level slot shared by every write and
   * cleared on any response at all — including a refusal. That is backwards: a
   * refusal is precisely when somebody retries, and a retry carrying a *new* key
   * is a retry the server cannot recognise as one. It is kept through a refusal,
   * through a timeout and through a dropped connection, and released only when
   * the write was accepted.
   *
   * The content that minted it is kept beside it, so editing the text after a
   * conflict mints a new key rather than reusing one the server has already
   * bound to different words — which is the case the server answers with
   * `IdempotencyConflict`, and answering it by changing the key is the honest
   * resolution.
   */

  var requestKeys = {};

  function requestScope(operation, taskId) {
    return operation + ":" + (taskId || "");
  }

  function requestIdFor(operation, taskId, content) {
    var scope = requestScope(operation, taskId);
    var held = requestKeys[scope];
    if (held && held.content === content) { return held.id; }
    var id = "pwa-" + Date.now().toString(36) + "-" +
      Math.floor(Math.random() * 1e9).toString(36);
    requestKeys[scope] = { id: id, content: content };
    return id;
  }

  function releaseRequestId(operation, taskId) {
    delete requestKeys[requestScope(operation, taskId)];
  }

  /* ---------------------------------------------------------- reading state */

  function tasks() { return (snapshot && snapshot.tasks) || []; }
  function counts() { return (snapshot && snapshot.counts) || {}; }
  function adapterList() { return (adapters && adapters.adapters) || []; }
  function projectList() { return (projects && projects.projects) || []; }

  function projectById(projectId) {
    var list = projectList();
    for (var index = 0; index < list.length; index += 1) {
      if (list[index].project_id === projectId) { return list[index]; }
    }
    return null;
  }

  function defaultAdapterFor(projectId) {
    /* The adapter this project delegates to, when the host said and this build
       has it available.

       The PWA is the surface that *may* name an adapter — it shows the choice
       and the person makes it — so this is a default, not a rule; the dropdown
       still offers every registered adapter and Task Core still refuses one the
       project does not permit.

       It exists because M2I.5 Gate B registers a second Claude adapter, and the
       old default was "the first available one in the list". With two
       registered that would open the composer on `claude-agent-sdk` for a
       project that only permits `claude-code`, and the person would learn about
       it by pressing Play and being refused. Opening on the adapter the project
       actually delegates to is the same information, one step earlier. */
    var project = projectById(projectId);
    var delegated = project && project.delegated_adapter;
    if (!delegated) { return null; }
    var list = adapterList();
    for (var index = 0; index < list.length; index += 1) {
      if (list[index].adapter_id === delegated && list[index].available) {
        return delegated;
      }
    }
    return null;
  }

  function isTerminal(state) { return TERMINAL.indexOf(state) !== -1; }

  function anyActive() {
    var rows = tasks();
    for (var index = 0; index < rows.length; index += 1) {
      if (rows[index].bucket === "active") { return true; }
    }
    return false;
  }

  function busy(key) { return pending === key; }
  function locked() { return pending !== null; }

  function capabilityOf(task, name) {
    var caps = (task && task.adapter_capabilities) || {};
    return caps[name] === true;
  }

  /* ------------------------------------------------------------- rendering */

  function badge(text, tone) {
    return '<span class="badge' + (tone ? " " + tone : "") + '">' + esc(text) + "</span>";
  }

  /* Every state gets its own words and its own tone. Two distinctions are
     load-bearing and easy to lose:

       interrupted vs failed — the first means Cofferdam stopped underneath the
       task, the second means the task itself went wrong. Showing both as "error"
       would send someone debugging work that was never given a chance to run.

       waiting vs running — the first needs a person, the second does not. A
       waiting task that looked busy would sit there until somebody happened to
       open it. */
  function stateLabel(state) {
    switch (state) {
      case "created": return { text: "created", tone: "" };
      case "queued": return { text: "queued", tone: "" };
      case "starting": return { text: "starting…", tone: "warn" };
      case "running": return { text: "running", tone: "ok" };
      case "waiting_for_user": return { text: "needs you", tone: "warn" };
      /* Deliberately not "needs you". A finished turn needs nobody — the
         session is simply still open if the person wants it. Saying "needs you"
         here is what made the panel state a falsehood about a task that had
         done exactly what it was asked. */
      case "ready_for_followup": return { text: "turn complete", tone: "ok" };
      case "cancelling": return { text: "cancelling…", tone: "warn" };
      case "completed": return { text: "completed", tone: "ok" };
      case "failed": return { text: "failed", tone: "err" };
      case "cancelled": return { text: "cancelled", tone: "" };
      case "interrupted": return { text: "interrupted", tone: "err" };
      case "recovery_required": return { text: "needs a decision", tone: "warn" };
      /* An unknown state means this shell is older than the server. Saying so is
         better than rendering an empty badge that looks like nothing happened. */
      default: return { text: state || "unknown", tone: "warn" };
    }
  }

  function waitingLabel(reason) {
    switch (reason) {
      case "clarification": return "waiting for an answer";
      case "approval": return "waiting for your approval";
      case "authentication": return "waiting for sign-in on the workstation";
      case "privileged_action": return "waiting for a privileged action";
      case "adapter_input": return "waiting for input";
      default: return "waiting for you";
    }
  }

  function elapsed(task) {
    var started = task.created_at;
    if (!started) { return ""; }
    var began = Date.parse(started);
    if (isNaN(began)) { return ""; }
    var end = task.completed_at ? Date.parse(task.completed_at) : Date.now();
    if (isNaN(end)) { return ""; }
    var seconds = Math.max(0, Math.round((end - began) / 1000));
    if (seconds < 60) { return seconds + "s"; }
    if (seconds < 3600) { return Math.floor(seconds / 60) + "m"; }
    if (seconds < 86400) { return Math.floor(seconds / 3600) + "h"; }
    return Math.floor(seconds / 86400) + "d";
  }

  function taskRow(task) {
    var label = stateLabel(task.state);
    var meta = [];
    if (task.project_display_name || task.project_id) {
      meta.push(task.project_display_name || task.project_id);
    }
    if (task.adapter_display_name || task.adapter_id) {
      meta.push(task.adapter_display_name || task.adapter_id);
    }
    var age = elapsed(task);
    if (age) { meta.push(age); }

    var activity = task.state === "waiting_for_user"
      ? waitingLabel(task.waiting_reason)
      : task.latest_activity;

    return '<li class="task-row' + (isTerminal(task.state) ? " terminal" : "") +
      (task.state === "waiting_for_user" ? " needs-you" : "") + '">' +
      '<button class="task-open" data-task-open="' + esc(task.task_id) + '"' +
      (locked() ? " disabled" : "") + '>' +
      '<span class="task-line">' +
      '<span class="task-title">' + esc(task.title || task.task_id) + "</span>" +
      badge(label.text, label.tone) +
      "</span>" +
      (meta.length ? '<span class="task-meta">' + esc(meta.join(" · ")) + "</span>" : "") +
      (activity ? '<span class="task-activity">' + esc(activity) + "</span>" : "") +
      "</button></li>";
  }

  function taskGroup(title, bucket, empty) {
    var rows = tasks().filter(function (task) { return task.bucket === bucket; });
    if (!rows.length) {
      return '<div class="task-group"><h3>' + esc(title) + "</h3>" +
        '<p class="muted">' + esc(empty) + "</p></div>";
    }
    return '<div class="task-group"><h3>' + esc(title) +
      ' <span class="muted">(' + rows.length + ")</span></h3>" +
      '<ul class="task-list">' + rows.map(taskRow).join("") + "</ul></div>";
  }

  /* ------------------------------------------------------------- composer */

  function adapterOptions() {
    var list = adapterList();
    if (!list.length) { return ""; }
    return list.map(function (adapter) {
      var selected = adapter.adapter_id === draft.adapterId ? " selected" : "";
      var suffix = adapter.available ? "" : " (unavailable)";
      return '<option value="' + esc(adapter.adapter_id) + '"' + selected +
        (adapter.available ? "" : " disabled") + ">" +
        esc(adapter.display_name || adapter.adapter_id) + esc(suffix) + "</option>";
    }).join("");
  }

  function projectOptions() {
    return projectList().map(function (project) {
      var selected = project.project_id === draft.projectId ? " selected" : "";
      return '<option value="' + esc(project.project_id) + '"' + selected + ">" +
        esc(project.display_name || project.project_id) + "</option>";
    }).join("");
  }

  function selectedAdapter() {
    var list = adapterList();
    for (var index = 0; index < list.length; index += 1) {
      if (list[index].adapter_id === draft.adapterId) { return list[index]; }
    }
    return null;
  }

  function adapterNote() {
    var adapter = selectedAdapter();
    if (!adapter) { return ""; }
    var html = "";
    if (adapter.description) {
      html += '<p class="muted hint">' + esc(adapter.description) + "</p>";
    }
    /* Said plainly, on the screen where somebody is about to use it. A test
       adapter that looked like a real one would be the single most misleading
       thing this panel could show. */
    if (adapter.validation_only) {
      html += '<p class="task-validation-note"><strong>This is a validation adapter.</strong> ' +
        "It runs no program, calls no model and changes nothing on the workstation. " +
        "It exists to check that tasks behave correctly.</p>";
      if (adapter.scenarios && adapter.scenarios.length) {
        html += '<p class="muted hint">Begin the prompt with <code>scenario: ' +
          esc(adapter.scenarios.map(function (item) { return item.scenario; }).join(" | ")) +
          "</code> to choose what it does.</p>";
      }
    }
    /* An adapter that runs a real program says what it will refuse to do, on
       the screen where somebody is about to write a prompt asking for it. The
       sentences come from the server: this panel does not know what "Claude
       Code" is, and must not start guessing on its behalf. */
    if (adapter.limitations && adapter.limitations.length) {
      html += '<ul class="task-limitations">' +
        adapter.limitations.slice(0, 8).map(function (item) {
          return "<li>" + esc(item) + "</li>";
        }).join("") + "</ul>";
    }
    return html;
  }

  function composer() {
    if (!composerOpen) {
      return '<div class="task-new"><button id="taskCompose" class="primary"' +
        (locked() ? " disabled" : "") + ">New task</button></div>";
    }

    var noProjects = !projectList().length;
    var noAdapters = !adapterList().length;

    if (noProjects || noAdapters) {
      /* Honest empty state. Neither of these is a bug, and both have a specific
         cause a person can act on — so neither gets a generic "nothing here". */
      return '<div class="task-new task-new-open">' +
        '<h3>New task</h3>' +
        (noProjects
          ? '<p class="muted">No projects are configured on this workstation yet. ' +
            "Projects are set up on the host, in the task project file — they are " +
            "deliberately not something a phone can add.</p>"
          : "") +
        (noAdapters
          ? '<p class="muted">No task adapters are registered in this build. ' +
            "Task Core is present and working; the adapter that does real work is a " +
            "separate milestone.</p>"
          : "") +
        '<button id="taskComposeCancel" class="ghost">Close</button></div>';
    }

    var length = draft.prompt.length;
    return '<div class="task-new task-new-open"><h3>New task</h3>' +
      (formError ? '<p class="media-note err">' + esc(formError) + "</p>" : "") +
      '<label class="field"><span class="field-label">Project</span>' +
      '<select id="taskProject"' + (locked() ? " disabled" : "") + ">" +
      projectOptions() + "</select></label>" +
      '<label class="field"><span class="field-label">Adapter</span>' +
      '<select id="taskAdapter"' + (locked() ? " disabled" : "") + ">" +
      adapterOptions() + "</select></label>" +
      adapterNote() +
      '<label class="field"><span class="field-label">What should it do?</span>' +
      '<textarea id="taskPrompt" rows="4" maxlength="' + MAX_PROMPT_CHARS + '"' +
      (locked() ? " disabled" : "") + ' placeholder="Describe the task in your own words."' +
      ">" + esc(draft.prompt) + "</textarea></label>" +
      '<p class="muted hint">' + length + " / " + MAX_PROMPT_CHARS + " characters. " +
      "This text is sent to the adapter you chose. It is never run as a command.</p>" +
      '<div class="task-new-actions">' +
      '<button id="taskStart" class="primary"' + (locked() ? " disabled" : "") + ">" +
      (busy("create") ? "Starting…" : "Start task") + "</button>" +
      '<button id="taskComposeCancel" class="ghost"' + (locked() ? " disabled" : "") +
      ">Cancel</button></div></div>";
  }

  /* --------------------------------------------------------------- detail */

  function eventLine(event) {
    var when = event.created_at ? new Date(event.created_at).toLocaleTimeString() : "";
    var evidence = (event.evidence || []).map(function (item) {
      /* The distinction the whole evidence model exists for, rendered rather
         than hidden: something Cofferdam watched happen reads differently from
         something an adapter said happened. */
      return '<span class="task-evidence' + (item.verified ? " verified" : "") + '">' +
        esc(item.evidence_type) + ": " + esc(item.identifier || "—") +
        (item.verified ? " (observed)" : " (adapter says)") + "</span>";
    }).join("");
    return '<li class="task-event"><span class="task-event-head">' +
      '<span class="task-event-type">' + esc(event.event_type) + "</span>" +
      '<span class="muted">' + esc(when) + "</span></span>" +
      (event.text ? '<span class="task-event-text">' + esc(event.text) + "</span>" : "") +
      (event.detail ? '<span class="muted">' + esc(event.detail) + "</span>" : "") +
      (evidence ? '<span class="task-evidence-row">' + evidence + "</span>" : "") +
      "</li>";
  }

  /* Waiting reasons whose answer is a secret, and which therefore must never be
     given a text box.

     The foundation named these in `SECRET_BEARING_WAITING_REASONS` and this
     panel did not read the list, because the only adapter that existed never
     produced one. An adapter that runs Claude Code does: an expired login puts
     a task in `waiting_for_user(authentication)`, and a textarea labelled "Your
     answer" under that heading is an invitation to type a password into a task
     history.

     So the box is withheld and a sentence is shown instead. The answer to an
     authentication wait is an action at the workstation — Cofferdam does not
     want the secret, has nowhere to put it, and says so. */
  var SECRET_WAITING_REASONS = ["authentication", "privileged_action"];

  function waitingForSecret(task) {
    return task.state === "waiting_for_user" &&
      SECRET_WAITING_REASONS.indexOf(task.waiting_reason) !== -1;
  }

  /* The question this task is blocked on, or null.

     Read from the clarifications route rather than inferred from the task state,
     because the two answer different questions. `waiting_for_user(clarification)`
     says the server is waiting for somebody; only the question itself carries the
     words, the options and — critically — the `question_id` an answer has to be
     addressed to. A panel that inferred one from the other would have a box with
     nowhere to send what was typed into it, which is exactly what this panel had
     before M2I PR4: it rendered "Your answer" for a clarification and posted it
     to `/followups`, a route the server refuses outright while a question is
     open. */
  function pendingQuestion() {
    for (var index = 0; index < detailQuestions.length; index += 1) {
      if (detailQuestions[index].status === "pending") { return detailQuestions[index]; }
    }
    return null;
  }

  /* One question, as a form.

     Everything rendered comes from the normalized clarification the server sent:
     bounded question text, Cofferdam's own option ids, and its own answer-mode
     vocabulary. Nothing here reads a provider field, because there is none in the
     payload to read — `PendingClarification.to_dict` carries no session id, no
     tool input and no raw provider object.

     The three modes get three different controls, and an unrecognised one falls
     back to a text box rather than to nothing: a question this build cannot
     classify is still a question somebody should be able to answer. */
  function questionForm(question) {
    var mode = question.answer_mode;
    var options = question.options || [];
    var chosen = chosenOptions[question.question_id] || [];
    var multiple = mode === "multiple_choice";
    var html = '<div class="task-question" data-question="' +
      esc(question.question_id) + '">' +
      '<p class="task-question-text">' + esc(question.question) + "</p>";

    if (options.length) {
      html += '<ul class="task-options">' + options.map(function (option) {
        var ticked = chosen.indexOf(option.option_id) !== -1;
        return '<li class="task-option">' +
          '<label><input type="' + (multiple ? "checkbox" : "radio") + '"' +
          ' name="taskOption" class="task-option-input"' +
          ' value="' + esc(option.option_id) + '"' +
          (ticked ? " checked" : "") + (locked() ? " disabled" : "") + ">" +
          '<span class="task-option-label">' + esc(option.label) + "</span>" +
          (option.description
            ? '<span class="muted">' + esc(option.description) + "</span>"
            : "") +
          "</label></li>";
      }).join("") + "</ul>";
    }

    /* The free-text box appears when the server says this question accepts one.
       Withheld otherwise, so a fixed-choice question does not get a field whose
       contents the server would refuse. */
    if (question.allows_free_text || !options.length) {
      html += '<label class="field"><span class="field-label">Your answer</span>' +
        '<textarea id="taskAnswerText" rows="3" maxlength="' + MAX_DRAFT_CHARS + '"' +
        (locked() ? " disabled" : "") + "></textarea></label>";
    }

    /* Said once, on the screen where somebody is about to type. The backend
       enforces it — an approval-shaped field is refused by name — and saying so
       here is what stops somebody trying.

       "the agent", not a provider's name. This panel names no specific agent
       anywhere: which one is running is a fact the backend supplies through the
       adapter catalogue, and a sentence that hard-coded one would be wrong the
       first time a second adapter asked a question. A test asserts the whole
       file contains neither word. */
    html += '<p class="muted hint">Answering a question gives the agent ' +
      "information, not permission. It cannot approve a tool, and Cofferdam " +
      "will not ask you to.</p>";

    if (!question.schema_verified) {
      /* The honest half of the question channel. A shape this build has not seen
         against a real provider session is rendered, and labelled as such,
         rather than presented as though it were the verified one. */
      html += '<p class="media-note warn">Cofferdam has not verified this ' +
        "question's shape against a real session. Read it before answering.</p>";
    }

    html += '<button id="taskAnswerSend" class="primary"' +
      (locked() ? " disabled" : "") + ">" +
      (busy("answer") ? "Sending…" : "Send answer") + "</button></div>";
    return html;
  }

  function detailActions(task) {
    var buttons = [];
    var question = pendingQuestion();
    if (question && !waitingForSecret(task)) {
      /* A real question, with a real place to send the answer. This branch comes
         first because it is the one that must win: while a question is open the
         follow-up route refuses, so offering a follow-up box here would offer a
         button whose only outcome is a refusal. */
      buttons.push(questionForm(question));
      if (!task.terminal && capabilityOf(task, "cancel")) {
        buttons.push(
          '<button id="taskCancel" class="ghost"' + (locked() ? " disabled" : "") + ">" +
          (busy("cancel") ? "Cancelling…" : "Cancel task") + "</button>"
        );
      }
      return '<div class="task-actions">' + buttons.join("") + "</div>";
    }
    if (waitingForSecret(task)) {
      buttons.push(
        '<p class="media-note warn task-secret-wait">' +
        "<strong>Cofferdam will not ask you for this here.</strong> " +
        "Finish this on the workstation itself. Never type a password, " +
        "one-time code, passkey or token into a task." +
        "</p>"
      );
    } else if (
      task.state === "waiting_for_user" && capabilityOf(task, "clarifications")
    ) {
      /* The adapter asks structured questions, the task is waiting on one, and
         this panel has not got it yet — the questions are a separate authenticated
         read and it may not have landed, or a restart may have closed the question
         between the two.

         No box. An answer to a structured question is addressed to a specific
         `question_id`, and there is nowhere to send text typed without one; the
         follow-up route would refuse it, by design, for exactly as long as the
         question is open. Saying so is better than offering a control that can
         only fail. */
      buttons.push(
        '<p class="media-note warn task-question-missing">' +
        "<strong>This task is waiting on a question.</strong> " +
        "Cofferdam is fetching it — pull to refresh if it does not appear. " +
        "If Cofferdam restarted, the question was closed with the session and " +
        "cannot be answered.</p>"
      );
    } else if (task.state === "waiting_for_user" && capabilityOf(task, "followup")) {
      /* An adapter that waits for a person without asking a *structured*
         question — the Claude Code transport has no channel for one, so its wait
         is answered by an ordinary follow-up. Unchanged from M2G, and the
         `clarifications` check above is what keeps the two apart: a structured
         question must never be answered through this route. */
      buttons.push(
        '<div class="task-followup">' +
        '<label class="field"><span class="field-label">Your answer</span>' +
        '<textarea id="taskFollowupText" rows="3" maxlength="' + MAX_FOLLOWUP_CHARS + '"' +
        (locked() ? " disabled" : "") + "></textarea></label>" +
        '<button id="taskFollowupSend" class="primary"' + (locked() ? " disabled" : "") + ">" +
        (busy("followup") ? "Sending…" : "Send answer") + "</button></div>"
      );
    } else if (task.state === "ready_for_followup" && capabilityOf(task, "followup")) {
      /* The turn is done and nothing was asked. Different words, a different
         field label, and a way out that is not cancellation. */
      buttons.push(
        '<p class="media-note task-turn-complete"><strong>Turn complete.</strong> ' +
        "You can send an optional follow-up in the same session, " +
        "or finish the task.</p>" +
        '<div class="task-followup">' +
        '<label class="field"><span class="field-label">Follow-up (optional)</span>' +
        '<textarea id="taskFollowupText" rows="3" maxlength="' + MAX_FOLLOWUP_CHARS + '"' +
        (locked() ? " disabled" : "") + "></textarea></label>" +
        '<button id="taskFollowupSend" class="primary"' + (locked() ? " disabled" : "") + ">" +
        (busy("followup") ? "Sending…" : "Send follow-up") + "</button>" +
        '<button id="taskFinish" class="ghost"' + (locked() ? " disabled" : "") + ">" +
        (busy("finish") ? "Finishing…" : "Finish task") + "</button></div>"
      );
    }
    /* Cancel is offered only where it can work: a finished task has nothing to
       cancel, and an adapter that cannot cancel must not be given a button that
       would only produce a refusal. */
    if (!task.terminal && capabilityOf(task, "cancel")) {
      buttons.push(
        '<button id="taskCancel" class="ghost"' + (locked() ? " disabled" : "") + ">" +
        (busy("cancel") ? "Cancelling…" : "Cancel task") + "</button>"
      );
    }
    if (task.final_result) {
      buttons.push('<button id="taskCopyResult" class="ghost">Copy result</button>');
      /* The result route, on demand rather than on every poll.

         What it adds over the result already on the task is the *turn* the answer
         came from and how many turns there have been — which is the difference
         between "here is a result" and "here is the latest completed turn's
         result", and the second is what the route actually means. Asking for it
         is a read: it changes no state and drives no adapter, which is why it is
         safe to offer as a button. Polling it as well would be a third request
         per tick to tell a phone something it can already see. */
      buttons.push(
        '<button id="taskShowResult" class="ghost"' + (locked() ? " disabled" : "") + ">" +
        (busy("result") ? "Checking…" : "Latest result") + "</button>"
      );
    }
    /* Evidence, on demand and for one turn at a time.

       On demand rather than on every poll for the reason the result button is:
       it is a third request per tick to show something nobody has asked to see.
       One turn at a time because the route is turn-qualified, and it is
       turn-qualified because "which turn does this evidence belong to" is the
       question M2K PR2 exists to answer exactly. A button that quietly merged
       turns would undo that on the way to the screen.

       There is no mutation control anywhere in this section — nothing to
       approve, dismiss, re-run, mark verified or override. This surface reports
       what was recorded; it does not act on it. */
    if (turnsSoFar(task)) {
      buttons.push(
        '<button id="taskShowEvidence" class="ghost"' + (locked() ? " disabled" : "") + ">" +
        (busy("evidence")
          ? "Reading…"
          : "Evidence — turn " + String(nextEvidenceTurn(task))) +
        "</button>"
      );
      /* The assessment, on demand and for one turn at a time, for exactly the
         reasons the evidence button is. There is no re-run control here and
         there is not going to be one: an evaluation is immutable, and a browser
         must not be able to ask for a second opinion on frozen facts. */
      buttons.push(
        '<button id="taskShowAssessment" class="ghost"' + (locked() ? " disabled" : "") + ">" +
        (busy("assessment")
          ? "Reading…"
          : "Assessment — turn " + String(nextAssessmentTurn(task))) +
        "</button>"
      );
    }
    return buttons.length ? '<div class="task-actions">' + buttons.join("") + "</div>" : "";
  }

  /* How many turns this task is known to have had.

     Taken from the result payload when somebody has asked for it, and otherwise
     assumed to be one — a task that has started has a turn. Deliberately not
     derived from the event history: an event does not say which turn it belongs
     to, and inferring one on the client would be the exact task-scoped-versus-
     turn-scoped guess the server now refuses to make. */
  function turnsSoFar(task) {
    if (detailResult && detailResult.task_id === task.task_id) {
      return detailResult.turn_count || 1;
    }
    return task.state === "created" ? 0 : 1;
  }

  /* Which turn the button will ask for: the one after whatever is on screen,
     wrapping back to the first. One button, every turn reachable, and the
     number is always visible so nobody has to guess which one they are reading. */
  function nextEvidenceTurn(task) {
    var total = turnsSoFar(task);
    if (!evidenceTurn || evidenceTurn >= total) { return 1; }
    return evidenceTurn + 1;
  }

  function nextAssessmentTurn(task) {
    var total = turnsSoFar(task);
    if (!assessmentTurn || assessmentTurn >= total) { return 1; }
    return assessmentTurn + 1;
  }

  function interruptionNote(task) {
    if (task.state === "interrupted") {
      return '<p class="media-note warn"><strong>Cofferdam restarted while this task was ' +
        "running, so it stopped.</strong> It was not resumed and it did not fail — nothing " +
        "is known about what it would have done next. Start it again if you still want it.</p>";
    }
    if (task.state === "recovery_required") {
      return '<p class="media-note warn"><strong>This task survived a restart and needs a ' +
        "decision.</strong> Cofferdam will not resume it on its own.</p>";
    }
    return "";
  }

  /* What the result route said, when somebody asked it.

     Rendered as the server's own words rather than reinterpreted: `result_meaning`
     says which of the two things a reader is holding — the latest completed turn,
     or the final outcome of a task that is over — and `task_terminal` says which
     without anybody having to infer it from the state name. Reproducing that
     reasoning here would be a second implementation of a distinction the backend
     already publishes as fields. */
  function resultBlock(task) {
    if (!detailResult || detailResult.task_id !== task.task_id) { return ""; }
    var turns = detailResult.turn_count;
    var facts = [];
    if (typeof turns === "number") {
      facts.push(turns + (turns === 1 ? " turn" : " turns"));
    }
    if (detailResult.turn_number) { facts.push("turn " + detailResult.turn_number); }
    facts.push(detailResult.task_terminal ? "task finished" : "task still open");
    if (detailResult.follow_up_available) { facts.push("follow-up available"); }
    return '<div class="task-block task-result-detail"><h4>Latest result</h4>' +
      '<p class="muted">' + esc(facts.join(" · ")) + "</p>" +
      (detailResult.result_meaning
        ? '<p class="muted hint">' + esc(detailResult.result_meaning) + "</p>"
        : "") +
      (detailResult.result
        ? '<pre class="task-text">' + esc(detailResult.result) + "</pre>"
        : '<p class="muted">This task produced no result text.</p>') +
      (detailResult.failure_summary
        ? '<p class="media-note err">' + esc(detailResult.failure_summary) + "</p>"
        : "") +
      "</div>";
  }

  /* ------------------------------------------------------------- evidence */

  /* The words this panel is allowed to use, and the ones it is not.

     What the bundle proves is narrow, and the language has to be exactly as
     narrow. A `git status` observation proves that a path changed. It does not
     prove the file was created, modified, deleted or renamed — the status
     letters are not in the durable record — so "Path agreed" is the whole of
     what may be said, and "Operation not established" is said out loud beside
     it rather than left for a reader to wonder about.

     There is deliberately no PASS, FAIL, SUCCESS, TRUSTED, LYING, no
     confidence, no score and no risk level anywhere in this section. Not
     because those would be unkind, but because none of them is a thing the
     evidence supports, and a phone screen is exactly where an unsupported word
     becomes a decision. */
  var RELATIONSHIP_WORDS = {
    path_agreed: {
      label: "Path agreed",
      tone: "ok",
      hint: "The worker and Cofferdam name the same file."
    },
    /* A disagreement between two records, and deliberately worded as one.

       It is NOT a failure, an accusation or a verdict: a worker that modified a
       file and then deleted it produced this and did nothing wrong. The tone is
       "warn" rather than "err" for that reason — this is something to look at,
       not something that went wrong. */
    claim_conflict: {
      label: "Records differ",
      tone: "warn",
      hint: "The worker and Cofferdam describe different operations on this file. Both records are kept as they were."
    },
    claim_only: {
      label: "Claim only",
      tone: "",
      hint: "The worker reported this. Cofferdam has no machine observation of it in this turn."
    },
    observed_only: {
      label: "Observed only",
      tone: "",
      hint: "Cofferdam saw this path change. No claim in this turn names it."
    }
  };

  var ATTRIBUTION_WORDS = {
    exact: "This turn's events are known exactly.",
    legacy_unknown:
      "Legacy turn attribution unavailable — this turn ran before Cofferdam " +
      "recorded turn boundaries, so no machine observation can be attributed " +
      "to it. Its claims are shown; the absence of observations here is not " +
      "evidence about the work."
  };

  /* What the machine said it did to a path (M2K PR3). Neutral verbs, and the
     word "observed" in front of every one of them, so a reader is never left to
     wonder whether this is what somebody claimed or what Cofferdam saw. */
  var CHANGE_WORDS = {
    created: "Machine observed: created",
    modified: "Machine observed: modified",
    deleted: "Machine observed: deleted",
    renamed: "Machine observed: renamed",
    unknown: "Machine observed a change of an unrecognised kind"
  };

  var OPERATION_WORDS = {
    "true": "Operation agreed",
    "false": "Operation differs",
    unknown: "Operation not established"
  };

  var COMPLETENESS_WORDS = {
    complete: "Every reported claim was stored.",
    incomplete: "Claim set incomplete — some of what the worker reported was not stored.",
    legacy_unknown: "Claim set completeness unavailable for a legacy turn.",
    ingestion_missing:
      "No claim report was recorded for this turn. That is not the same as " +
      "an empty one: completeness is unknown."
  };

  var LIMITATION_WORDS = {
    legacy_turn_attribution_unavailable: "Legacy turn attribution unavailable.",
    claim_ingestion_record_missing: "No claim ingestion record for this turn.",
    claim_set_incomplete: "Claim set incomplete.",
    unsupported_observation_shape:
      "Cofferdam recorded an observation this build cannot interpret as a path change.",
    machine_observations_incomplete:
      "Cofferdam recorded only some of the changes Git reported.",
    claims_truncated: "More claims than this view shows.",
    observations_truncated: "More observations than this view shows.",
    relationship_paths_truncated: "More paths than this view shows.",
    relationship_sources_truncated: "Some paths have more sources than are listed.",
    events_truncated: "More events in this turn than were scanned."
  };

  /* ----------------------------------------------------------- assessment */

  /* The words this panel may use, and the one distinction it must never lose.

     `unverified` and `not_met` are different in the way that matters most on a
     phone screen. `not_met` is a finding about the work: the machine looked
     completely and the required change is not there. `unverified` is a statement
     about Cofferdam: the evidence could not decide. Rendering them alike — same
     colour, same icon, same shape of sentence — would turn every limit of the
     observer into an accusation about the worker, which is the failure the
     three-valued vocabulary exists to prevent.

     So they get different tones, different words, and `unverified` gets the
     neutral tone rather than the error one. There is deliberately no PASS, FAIL,
     SUCCESS or ERROR anywhere in this section, no aggregate, no count of how
     many were met, no percentage, no confidence and no risk. None of those is a
     thing the evaluator produced, and a screen is exactly where an unsupported
     word becomes a decision. */
  var RESULT_WORDS = {
    met: {
      label: "Met",
      tone: "ok",
      hint: "Machine-observed evidence for this turn satisfies it."
    },
    not_met: {
      label: "Not met",
      tone: "warn",
      hint: "The machine observation was complete enough to rule it out."
    },
    /* Neutral, never `err`. This is about Cofferdam's reach, not the work. */
    unverified: {
      label: "Could not verify",
      tone: "",
      hint: "The stored evidence cannot decide this either way."
    }
  };

  /* Closed reason codes, rendered as short sentences. Every one of them is a
     statement about evidence, never about the worker. */
  var REASON_WORDS = {
    machine_change_observed: "A resulting change for this path was observed.",
    machine_operation_observed: "The required operation was observed.",
    machine_rename_observed: "An explicit rename with these endpoints was observed.",
    complete_resulting_change_absent:
      "The observation was complete and no resulting change for this path is there.",
    complete_incompatible_operation:
      "The observation was complete and every recorded operation differs from the one required.",
    complete_rename_not_observed:
      "The observation was complete and no rename with these endpoints was recorded.",
    manual_criterion: "A person has to check this one. Cofferdam cannot.",
    unsupported_capability: "This build cannot evaluate this kind of criterion.",
    evidence_not_attributable:
      "This turn's events cannot be identified exactly, so nothing can be attributed to it.",
    machine_observations_incomplete: "Cofferdam recorded only some of what Git reported.",
    unsupported_observation_shape:
      "Cofferdam recorded an observation this build cannot interpret.",
    committed_range_not_recorded:
      "No committed-work observation was taken for this turn.",
    committed_range_incomplete: "The committed-work observation was not complete.",
    committed_range_history_diverged:
      "The history moved, so there is no before-and-after to compare.",
    pre_work_boundary_not_clean:
      "The project already had uncommitted changes when this turn began, so a " +
      "change cannot be attributed to it — and neither can its absence.",
    resulting_operation_not_observed:
      "The path changed, but the kind of change was not recorded.",
    worktree_not_observed:
      "Nothing examined the working tree for this turn, so absence there proves nothing."
  };

  var CRITERIA_STATE_WORDS = {
    present: null,
    not_provided:
      "No structured acceptance criteria were supplied for this turn. Nothing " +
      "was checked, and nothing about the work follows from that.",
    legacy_unknown:
      "Acceptance criteria were not recorded for this historical turn. It ran " +
      "before Cofferdam stored them, so there is no question to answer here."
  };

  var EVALUATION_STATE_WORDS = {
    criteria_legacy_unknown:
      "No evaluation, because this turn was never given criteria to evaluate.",
    turn_not_closed:
      "This turn is still running. Evaluation happens once a turn has finished.",
    /* Operational, and worth somebody looking. Deliberately not phrased as a
       criterion result: there is no result record at all, which is a different
       statement from a result that says the evidence could not decide. */
    not_recorded:
      "Evaluation not recorded. This turn has criteria and has finished, so a " +
      "record was expected — it may not have been written yet."
  };

  /* What was expected, in the criterion's own structured terms. Rendered from
     the stored fields rather than re-described, so the screen cannot claim a
     requirement nobody wrote. */
  function expectedText(item) {
    if (item.kind === "manual") { return esc(item.description || "—"); }
    if (item.predicate === "path_changed") {
      return "<code>" + esc(item.path) + "</code> changed";
    }
    if (item.predicate === "path_operation") {
      return "<code>" + esc(item.path) + "</code> " + esc(item.operation || "");
    }
    if (item.predicate === "rename") {
      return "<code>" + esc(item.path) + "</code> renamed to <code>" +
        esc(item.to_path) + "</code>";
    }
    return esc(item.predicate || item.kind || "—");
  }

  /* --------------------------------------------- acceptance (M2K PR22) */

  /* Two dimensions, and the screen must not merge them.

     `not_assessable` is not a bad outcome — it is the absence of one. Rendering
     it beside met/not-met/incomplete as though it were a fourth verdict would
     tell somebody their work fell short when what actually happened is that
     Cofferdam could not work out what was required of it.

     Every word here is scoped to *this turn's active requirements*. There is
     deliberately no "task passed", no "succeeded", no PASS/FAIL, no score and no
     percentage: the aggregate is a target-turn answer and a screen is exactly
     where an unsupported word becomes a global verdict nobody decided. */
  var ACCEPTANCE_WORDS = {
    met: {
      label: "Requirements met at this turn",
      tone: "ok",
      hint: "Every requirement active at this turn is established as met."
    },
    not_met: {
      label: "A requirement is not met at this turn",
      tone: "warn",
      hint: "At least one active requirement was ruled out by machine evidence."
    },
    /* Neutral, never `err`. This is Cofferdam's reach, not a finding. */
    incomplete: {
      label: "Requirement assessment incomplete",
      tone: "",
      hint: "Nothing was ruled out, and at least one requirement could not be decided."
    }
  };

  /* Why there is no outcome at all. Kept apart from `incomplete` on purpose:
     these say the requirement set itself could not be established, which is a
     statement about the record rather than about the work. The API keeps the
     exact code; these are the human phrasings for it. */
  var ACCEPTANCE_REASON_WORDS = {
    no_structured_criteria:
      "No structured requirements were declared, so there is nothing to assess.",
    continuity_not_declared:
      "The requirement lineage for this turn was never declared.",
    continuity_legacy_unknown:
      "This turn predates requirement lineage, so it cannot be reconstructed.",
    predecessor_unavailable:
      "A turn this one depends on could not be resolved.",
    turn_not_closed:
      "This turn is still running. Acceptance is answered once it has finished.",
    evaluation_not_recorded:
      "The turn evaluation this depends on has not been recorded yet.",
    evaluation_inconsistent:
      "A stored evaluation record does not satisfy its own invariants.",
    unsupported_evaluator_version:
      "A stored evaluation was written by evaluator semantics this build does not know.",
    final_state_inconsistent:
      "A stored end-of-turn observation does not satisfy its own invariants.",
    unsupported_final_state_observer_version:
      "A stored observation was written by semantics this build does not know.",
    final_state_lineage_mismatch:
      "A stored observation was taken for a different requirement set.",
    final_state_path_missing:
      "A stored observation is missing a path it claims to cover.",
    assessment_input_invalid:
      "The derived assessment this depends on does not satisfy its own contract.",
    unsupported_assessment_version:
      "The derived assessment was produced by semantics this build does not know."
  };

  /* Reasons that mean a stored record disagrees with itself, rather than that
     Cofferdam simply has not got there yet. Shown in the error tone, because
     somebody should look — and never prettified into ordinary uncertainty. */
  var ACCEPTANCE_STRUCTURAL = {
    evaluation_inconsistent: true,
    unsupported_evaluator_version: true,
    final_state_inconsistent: true,
    unsupported_final_state_observer_version: true,
    final_state_lineage_mismatch: true,
    final_state_path_missing: true,
    assessment_input_invalid: true,
    unsupported_assessment_version: true,
    malformed_lineage: true,
    cycle_detected: true,
    lineage_depth_exceeded: true,
    duplicate_active_criterion: true
  };

  /* Tri-state, and `null` is never rendered as "No". Whether a person is needed
     is unknown exactly when the requirement set is. */
  function requiresHumanText(value) {
    if (value === true) { return "Yes — a requirement needs a person to check it."; }
    if (value === false) { return "No — nothing here needs a person."; }
    return "Unknown — the requirement set could not be established.";
  }

  function acceptanceBlock(view) {
    var acceptance = view.acceptance;
    if (!acceptance) { return ""; }

    var rows = "";
    if (acceptance.availability === "assessable") {
      var words = ACCEPTANCE_WORDS[acceptance.outcome] ||
        { label: acceptance.outcome, tone: "", hint: "" };
      rows += '<p class="task-acceptance-outcome">' + badge(words.label, words.tone) +
        '<span class="muted hint">' + esc(words.hint) + "</span></p>";
    } else {
      var reason = acceptance.availability_reason;
      var tone = ACCEPTANCE_STRUCTURAL[reason] ? "err" : "";
      rows += '<p class="task-acceptance-outcome">' +
        badge("Not assessable", tone) +
        '<span class="muted hint">' +
        esc(ACCEPTANCE_REASON_WORDS[reason] || reason || "") + "</span></p>";
      if (acceptance.unavailable_cause) {
        rows += '<p class="muted hint">Underlying cause: ' +
          esc(ACCEPTANCE_REASON_WORDS[acceptance.unavailable_cause] ||
              acceptance.unavailable_cause) +
          (acceptance.unavailable_at_turn_number
            ? " (found at turn " + esc(String(acceptance.unavailable_at_turn_number)) + ")"
            : "") + "</p>";
      }
    }

    var counts = acceptance.counts;
    if (counts) {
      rows += '<p class="muted hint task-acceptance-counts">' +
        esc(String(counts.total)) + " active — " +
        esc(String(counts.met)) + " met, " +
        esc(String(counts.not_met)) + " not met, " +
        esc(String(counts.unverified)) + " could not verify.</p>";
    } else {
      rows += '<p class="muted hint task-acceptance-counts">' +
        "Requirement counts unknown — the active set could not be established.</p>";
    }

    rows += '<p class="muted hint task-acceptance-human">' +
      esc(requiresHumanText(acceptance.requires_human)) + "</p>";

    var handles = "<details class=\"task-assessment-audit\"><summary>Acceptance identifiers</summary>" +
      '<ul class="task-evidence-list">' +
      "<li>aggregator version <code>" +
      esc(String(acceptance.aggregator_version)) + "</code></li>" +
      "<li>assessment <code>" + esc(acceptance.assessment_fingerprint) + "</code></li>" +
      "<li>acceptance <code>" + esc(acceptance.acceptance_fingerprint) + "</code></li>" +
      "</ul></details>";

    return '<div class="task-acceptance"><h5>Acceptance at this turn</h5>' +
      rows + handles + "</div>";
  }

  function assessmentBlock(task) {
    if (!detailAssessment || detailAssessment.task_id !== task.task_id) { return ""; }
    var view = detailAssessment;
    var criteria = view.criteria || {};
    var evaluation = view.evaluation || {};
    var items = criteria.items || [];
    var results = {};
    (evaluation.results || []).forEach(function (row) {
      results[row.criterion_id] = row;
    });

    var body;
    if (criteria.state !== "present") {
      body = '<p class="media-note">' + esc(CRITERIA_STATE_WORDS[criteria.state] ||
        criteria.state) + "</p>";
    } else {
      body = '<ul class="task-assessment-list">' + items.map(function (item) {
        var row = results[item.criterion_id];
        var words = row
          ? (RESULT_WORDS[row.result] || { label: row.result, tone: "", hint: "" })
          : null;
        return '<li class="task-assessment-item">' +
          '<span class="task-assessment-expected">' + expectedText(item) + "</span>" +
          (words
            ? badge(words.label, words.tone) +
              '<span class="muted hint">' +
              esc(REASON_WORDS[row.reason] || row.reason) + "</span>"
            : '<span class="muted hint">No result recorded for this criterion.</span>') +
          "</li>";
      }).join("") + "</ul>";
    }

    var note = "";
    if (!evaluation.recorded && EVALUATION_STATE_WORDS[evaluation.state]) {
      note = '<p class="media-note' +
        (evaluation.state === "not_recorded" ? " warn" : "") + '">' +
        esc(EVALUATION_STATE_WORDS[evaluation.state]) + "</p>";
    }

    /* Audit handles, tucked away. Deterministic identities and nothing more —
       not a trust score, not a confidence, not proof of anything. */
    var handles = "";
    if (criteria.snapshot_id || evaluation.evaluation_id) {
      handles = "<details class=\"task-assessment-audit\"><summary>Audit identifiers</summary>" +
        '<ul class="task-evidence-list">' +
        (criteria.snapshot_id
          ? "<li>criteria snapshot <code>" + esc(criteria.snapshot_id) + "</code></li>" +
            "<li>criteria fingerprint <code>" + esc(criteria.criteria_fingerprint) + "</code></li>"
          : "") +
        (evaluation.recorded
          ? "<li>evaluation <code>" + esc(evaluation.evaluation_id) + "</code></li>" +
            "<li>evaluation fingerprint <code>" + esc(evaluation.evaluation_fingerprint) + "</code></li>" +
            "<li>evaluator version <code>" + esc(String(evaluation.evaluator_version)) + "</code></li>" +
            "<li>evidence <code>" + esc(evaluation.evidence_input_fingerprint) +
            "</code> (assembler " + esc(String(evaluation.assembler_version)) + ")</li>"
          : "") +
        "</ul></details>";
    }

    return '<div class="task-block task-assessment-detail"><h4>Assessment — turn ' +
      esc(String(view.turn_number)) + "</h4>" + note + body + handles +
      acceptanceBlock(view) + "</div>";
  }

  function evidenceRelationships(bundle) {
    var groups = bundle.relationships || [];
    if (!groups.length) {
      return '<p class="muted">Nothing was claimed or observed for this turn.</p>';
    }
    return '<ul class="task-evidence-groups">' + groups.map(function (group) {
      var words = RELATIONSHIP_WORDS[group.relationship] || {
        label: group.relationship, tone: "", hint: ""
      };
      var kinds = (group.observed_kinds || []).map(function (k) {
        return CHANGE_WORDS[k] || k;
      }).join(", ");
      var counts = [];
      if (group.claim_count) {
        counts.push(group.claim_count + (group.claim_count === 1 ? " claim" : " claims"));
      }
      if (group.observation_count) {
        counts.push(
          group.observation_count +
          (group.observation_count === 1 ? " observation" : " observations")
        );
      }
      if (group.sources_truncated) { counts.push("more not listed"); }
      return '<li class="task-evidence-group">' +
        '<span class="task-evidence-path">' + esc(group.path) + "</span>" +
        badge(words.label, words.tone) +
        (counts.length
          ? '<span class="muted">' + esc(counts.join(" · ")) + "</span>"
          : "") +
        '<span class="muted hint">' + esc(words.hint) + "</span>" +
        /* Said for every group, including the agreeing ones. Especially the
           agreeing ones: "Path agreed" is the row somebody is most likely to
           read as "verified". Since M2K PR3 this can also say the operation
           agreed or differed, where the machine evidence supports it. */
        '<span class="muted hint">' +
        esc(OPERATION_WORDS[group.operation_agreement] || OPERATION_WORDS.unknown) +
        (kinds ? " · " + esc(kinds) : "") +
        ".</span>" +
        "</li>";
    }).join("") + "</ul>";
  }

  function evidenceBlock(task) {
    if (!detailEvidence || detailEvidence.task_id !== task.task_id) { return ""; }
    var bundle = detailEvidence;
    var claims = bundle.claims || [];
    var observations = bundle.observations || [];
    var ingestion = bundle.ingestion || {};
    var limits = bundle.limitations || [];

    return '<div class="task-block task-evidence-detail"><h4>Evidence — turn ' +
      esc(String(bundle.turn_number)) + "</h4>" +

      '<p class="muted hint">' +
      esc(ATTRIBUTION_WORDS[bundle.turn_attribution] || bundle.turn_attribution) +
      "</p>" +

      "<h5>Worker claims</h5>" +
      (claims.length
        ? '<ul class="task-evidence-list">' + claims.map(function (claim) {
            return "<li>" + esc(claim.operation) + " " + esc(claim.path) +
              (claim.to_path ? " → " + esc(claim.to_path) : "") +
              ' <span class="muted">(reported by the adapter, not verified)</span></li>';
          }).join("") + "</ul>"
        : '<p class="muted">No claims recorded for this turn.</p>') +

      "<h5>Machine observations</h5>" +
      (observations.length
        ? '<ul class="task-evidence-list">' + observations.map(function (item) {
            var what = item.change_kind
              ? (CHANGE_WORDS[item.change_kind] || item.change_kind)
              : "Machine observed: this path changed";
            /* A two-letter status can prove two things — RM is renamed AND
               modified — so the raw status is shown beside the primary word
               rather than letting the word stand for the whole fact. */
            var raw = item.change_status
              ? ' <span class="muted">[' + esc(item.change_status) + "]</span>"
              : "";
            var rename = item.change_kind === "renamed" && item.previous_path
              ? " (" + esc(item.previous_path) + " → " + esc(item.path) + ")"
              : "";
            return "<li>" + esc(item.path) + rename + raw +
              ' <span class="muted">' + esc(what) +
              " — Cofferdam ran git status itself</span></li>";
          }).join("") + "</ul>"
        : bundle.repository_reported_clean
          ? '<p class="muted">Cofferdam looked and the working tree was clean.</p>'
          : '<p class="muted">No machine observations for this turn.</p>') +

      "<h5>Relationships and gaps</h5>" +
      evidenceRelationships(bundle) +

      (bundle.machine_observations_complete === false
        ? '<p class="muted hint">Cofferdam recorded only some of the changes Git ' +
          "reported for this turn, so a file with no observation here may simply " +
          "not have been looked at.</p>"
        : "") +

      "<h5>Claim ingestion</h5>" +
      '<p class="muted">' +
      esc(COMPLETENESS_WORDS[ingestion.state] || ingestion.state || "unknown") +
      "</p>" +
      (typeof ingestion.submitted === "number" && ingestion.submitted
        ? '<p class="muted hint">' + esc(
            ingestion.accepted + " of " + ingestion.submitted + " stored" +
            (ingestion.truncated ? ", report truncated" : "")
          ) + "</p>"
        : "") +

      (limits.length
        ? "<h5>Limitations</h5><ul class=\"task-evidence-list\">" +
          limits.map(function (code) {
            return "<li>" + esc(LIMITATION_WORDS[code] || code) + "</li>";
          }).join("") + "</ul>"
        : "") +

      '<p class="muted hint">Assembled from stored records only — no repository ' +
      "was read to produce this. Assembler v" + esc(String(bundle.assembler_version)) +
      ".</p>" +
      "</div>";
  }

  function detailView() {
    var task = detail;
    if (!task) { return ""; }
    var label = stateLabel(task.state);
    var rows = [
      ["Task", task.task_id],
      ["Project", task.project_display_name || task.project_id],
      ["Adapter", task.adapter_display_name || task.adapter_id],
      ["Created", task.created_at ? new Date(task.created_at).toLocaleString() : "—"],
      ["Started", task.started_at ? new Date(task.started_at).toLocaleString() : "—"],
      ["Finished", task.completed_at ? new Date(task.completed_at).toLocaleString() : "—"]
    ];

    return '<div class="task-detail" id="taskDetail">' +
      '<div class="task-detail-head">' +
      '<button id="taskBack" class="ghost">‹ All tasks</button>' +
      badge(label.text, label.tone) +
      "</div>" +
      '<h3>' + esc(task.title || "Task") + "</h3>" +
      interruptionNote(task) +
      (task.state === "waiting_for_user"
        ? '<p class="media-note warn"><strong>' + esc(waitingLabel(task.waiting_reason)) +
          ".</strong> This task is paused until you answer.</p>"
        : "") +
      (task.failure
        ? '<p class="media-note err"><strong>' + esc(task.failure.message) + "</strong>" +
          (task.failure.detail ? " " + esc(task.failure.detail) : "") + "</p>"
        : "") +
      '<dl class="task-facts">' +
      rows.map(function (row) {
        return "<dt>" + esc(row[0]) + "</dt><dd>" + esc(row[1] || "—") + "</dd>";
      }).join("") +
      "</dl>" +
      (task.prompt
        ? '<div class="task-block"><h4>What you asked</h4><pre class="task-text">' +
          esc(task.prompt) + "</pre></div>"
        : "") +
      (task.latest_meaningful_output && !task.final_result
        ? '<div class="task-block"><h4>Latest</h4><pre class="task-text">' +
          esc(task.latest_meaningful_output) + "</pre></div>"
        : "") +
      (task.final_result
        ? '<div class="task-block"><h4>Result</h4><pre class="task-text" id="taskResultText">' +
          esc(task.final_result) + "</pre></div>"
        : "") +
      resultBlock(task) +
      assessmentBlock(task) +
      evidenceBlock(task) +
      detailActions(task) +
      /* The raw stream, behind a disclosure. Available for when the summary is
         not enough, and never the first thing anybody has to read. */
      '<details class="task-advanced"' + (advancedOpen ? " open" : "") + ">" +
      "<summary>Advanced — event history (" + detailEvents.length + ")</summary>" +
      (detailEvents.length
        ? '<ul class="task-events">' + detailEvents.map(eventLine).join("") + "</ul>"
        : '<p class="muted">No events recorded.</p>') +
      "</details></div>";
  }

  /* ----------------------------------------------------------------- render */

  function messages() {
    var html = "";
    if (actionError) {
      html += '<p class="media-note err">' + esc(actionError.message) +
        (actionError.detail ? " " + esc(actionError.detail) : "") + "</p>";
    }
    if (actionNote) {
      html += '<p class="media-note">' + esc(actionNote) + "</p>";
    }
    if (loadError) {
      html += '<p class="media-note err">' + esc(loadError) + "</p>";
    }
    return html;
  }

  function listView() {
    if (!tasks().length) {
      return composer() +
        '<p class="muted">No tasks yet. When you start one it appears here, ' +
        "with its own history and whatever it produced.</p>";
    }
    return composer() +
      taskGroup("Active", "active", "Nothing is running.") +
      taskGroup("Waiting for you", "waiting", "Nothing is waiting.") +
      taskGroup("Finished", "finished", "Nothing has finished yet.");
  }

  /* Read whatever is in the live follow-up box into the draft store.
     
     Called immediately before any re-render. It does not depend on `input`
     events having fired: assigning `innerHTML` destroys the textarea whether or
     not the browser has told us anything about it, so the value has to be taken
     from the node while the node still exists. */
  function captureDraft() {
    if (!deps || !openTaskId) { return; }
    /* Asked of the document, not of `deps.el`. That helper memoises — and
       creates — an element for any id, which is right for the fixed shell nodes
       it was written for and wrong for a node that only exists while the
       follow-up form is on screen. Fabricating an empty one and storing its
       value **erased the draft** every time the panel rendered a view without
       the form, which is exactly what happens on the way back to a task. */
    var operation = openOperation();
    var box = draftBox(operation);
    if (!box || typeof box.value !== "string") { return; }
    writeDraft(openTaskId, operation, box.value);
    followupFocus = null;
    var doc = deps.doc ? deps.doc() : (global.document || null);
    var focused = doc && doc.activeElement;
    if (focused === box || (focused && focused.id === draftBoxId(operation))) {
      followupFocus = {
        id: draftBoxId(operation),
        start: typeof box.selectionStart === "number" ? box.selectionStart : null,
        end: typeof box.selectionEnd === "number" ? box.selectionEnd : null
      };
    }
  }

  /* Which of the two boxes the open task is currently showing.

     Driven by whether the server says a question is open, never by which box
     happens to be in the DOM: the DOM lags the server by one render, and the
     draft has to be filed under the operation the *server* is waiting for or a
     poll that arrives mid-sentence files it under the wrong one. */
  function openOperation() {
    return pendingQuestion() ? OP_CLARIFICATION : OP_FOLLOWUP;
  }

  function draftBoxId(operation) {
    return operation === OP_CLARIFICATION ? "taskAnswerText" : "taskFollowupText";
  }

  /* The live box for one operation, or null when the panel is not showing it. */
  function draftBox(operation) {
    var doc = global.document;
    if (doc && typeof doc.getElementById === "function") {
      return doc.getElementById(draftBoxId(operation));
    }
    return null;
  }

  function draftFor(taskId, operation) {
    return readDraft(taskId, operation || OP_FOLLOWUP);
  }

  /* A task that has ended keeps no unsent drafts.

     Called wherever the server's own view of a task is adopted. The alternative
     — leaving them — is the quietly dishonest one: a cancelled task would still
     have somebody's half-written follow-up waiting under it, and the next time
     they opened it the panel would show text that can no longer be sent
     anywhere. Clearing it here means "the box is gone because the task is over",
     which is true, rather than "the box is gone because you cannot see it",
     which is what an unrendered draft amounts to.

     The held request ids go with them. A key identifies an attempt at a task
     that can still take one. */
  function settleTerminalDrafts(task) {
    if (!task || !task.task_id || !isTerminal(task.state)) { return; }
    clearDraft(task.task_id, OP_FOLLOWUP);
    clearDraft(task.task_id, OP_CLARIFICATION);
    releaseRequestId(OP_FOLLOWUP, task.task_id);
    delete chosenOptions[task.task_id];
  }

  /* Drop a draft the server has **accepted**, at every layer it lives in.

     This exists because clearing the store is not enough, and the phone found
     out the hard way. The draft text is deliberately not part of the markup —
     see `render` — so `clearDraft` empties memory and storage while the live
     textarea keeps holding the accepted words. The next `render` then calls
     `captureDraft`, which reads that node and writes them straight back. The
     draft came back, and because the request id had been released with it, the
     next tap on Send submitted the same sentence under a new key: a second
     provider turn, real model usage, and nothing on screen to suggest it had
     happened.

     So the node is cleared first, and only then the store.

     `accepted` is the text the server took. Anything else in the box now is
     newer than the answer — somebody's next message typed while the request was
     in flight — and is left alone. Returns whether it cleared, so a caller can
     be tested on the distinction rather than assuming it. */
  function clearAcceptedDraft(taskId, operation, accepted) {
    /* Only ever the box belonging to the task that was accepted. A different
       task open on screen has its own draft and its own box. */
    var box = openTaskId === taskId ? draftBox(operation) : null;
    var current = box && typeof box.value === "string"
      ? box.value
      : draftFor(taskId, operation);
    if (current !== accepted) { return false; }
    if (box) { box.value = ""; }
    clearDraft(taskId, operation);
    return true;
  }

  /* Copy the stored draft into the freshly built textarea. */
  function applyDraft() {
    if (!openTaskId) { return; }
    var operation = openOperation();
    var box = draftBox(operation);
    if (!box || typeof box.value !== "string") { return; }
    var stored = draftFor(openTaskId, operation);
    if (box.value !== stored) { box.value = stored; }
  }

  /* Put the person back where they were. The textarea is a new node after a
     re-render, so focus and caret have to be re-applied or every poll would
     interrupt typing even though the text survived. */
  function restoreFocus() {
    if (!followupFocus || !deps) { return; }
    var doc = global.document;
    var box = doc && typeof doc.getElementById === "function"
      ? doc.getElementById(followupFocus.id)
      : null;
    if (!box || typeof box.focus !== "function") { followupFocus = null; return; }
    box.focus();
    if (followupFocus.start !== null && typeof box.setSelectionRange === "function") {
      try { box.setSelectionRange(followupFocus.start, followupFocus.end); } catch (error) { /* not a text field */ }
    } else if (followupFocus.start !== null) {
      box.selectionStart = followupFocus.start;
      box.selectionEnd = followupFocus.end;
    }
    followupFocus = null;
  }

  function render() {
    if (!deps) { return; }
    var host = deps.el("tasksSections");
    if (!host) { return; }

    if (snapshot === null && !loadError) {
      host.innerHTML = '<p class="muted">Loading…</p>';
      lastMarkup = null;
      return;
    }

    captureDraft();
    var markup = messages() + (openTaskId && detail ? detailView() : listView());
    if (markup === lastMarkup) {
      /* Nothing a person could see has changed. Writing it anyway would throw
         away the textarea, the caret and the focus to produce the same pixels,
         which is precisely what made a typed draft disappear on a polling
         tick. */
      followupFocus = null;
      return;
    }
    lastMarkup = markup;
    host.innerHTML = markup;
    /* The draft is applied to the node **after** the markup is written, and is
       deliberately not part of the markup itself.
       
       Two reasons, and the second is the one that matters. Putting it in the
       HTML would make the markup change on every keystroke, so the
       identical-render skip above would never fire while somebody was typing
       and the form would be rebuilt under them on every poll — focus restored
       each time, but rebuilt. Keeping it out means the markup is stable while
       typing and an idle poll writes nothing at all. It also keeps a
       half-written sentence out of a string that gets compared, cached and
       potentially logged. */
    applyDraft();

    restoreFocus();

    var observed = deps.el("tasksObserved");
    if (observed) {
      var total = tasks().length;
      observed.textContent = snapshot
        ? total + (total === 1 ? " task" : " tasks") + " · checked " +
          new Date().toLocaleTimeString()
        : "";
    }
  }

  /* ------------------------------------------------------- response ordering */

  function nextGeneration() {
    refreshGeneration += 1;
    return refreshGeneration;
  }

  function nextDetailGeneration() {
    detailGeneration += 1;
    return detailGeneration;
  }

  function adopt(payload, generation) {
    if (generation < appliedGeneration) { return false; }
    appliedGeneration = generation;
    snapshot = payload;
    loadError = null;
    return true;
  }

  function abortInflightRefresh() {
    if (inflightRefresh) {
      try { inflightRefresh.abort(); } catch (error) { /* already settled */ }
      inflightRefresh = null;
    }
  }

  function newAbortController() {
    if (typeof global.AbortController !== "function") { return null; }
    try { return new global.AbortController(); } catch (error) { return null; }
  }

  /* ------------------------------------------------------------------ load */

  function loadCatalogues() {
    /* Adapters and projects change when the *host* changes, not while somebody
       is looking at a phone, so they are read once at mount rather than polled.
       Refresh re-reads them, which is the honest place for that cost. */
    return deps.api("/api/task-adapters").then(function (response) {
      if (response.ok) { adapters = response.payload; }
      return deps.api("/api/task-projects");
    }).then(function (response) {
      if (response.ok) { projects = response.payload; }
      if (!draft.projectId && projectList().length) {
        draft.projectId = projectList()[0].project_id;
      }
      if (!draft.adapterId) {
        draft.adapterId = defaultAdapterFor(draft.projectId);
      }
      if (!draft.adapterId) {
        var available = adapterList().filter(function (a) { return a.available; });
        if (available.length) { draft.adapterId = available[0].adapter_id; }
      }
    }).catch(function (error) {
      if (error && error.message === "unauthorized") { throw error; }
      /* A catalogue that cannot be read leaves the list working; the composer
         says why it cannot offer anything rather than the panel failing. */
    });
  }

  function load() {
    var generation = nextGeneration();
    abortInflightRefresh();
    var controller = newAbortController();
    inflightRefresh = controller;
    var options = controller ? { signal: controller.signal } : undefined;

    return deps.api("/api/tasks", options).then(function (response) {
      if (inflightRefresh === controller) { inflightRefresh = null; }
      if (generation < appliedGeneration) { return; }
      if (!response.ok) {
        appliedGeneration = generation;
        loadError = "Cofferdam could not read the task list.";
        snapshot = null;
      } else {
        adopt(response.payload, generation);
      }
      render();
      reschedule();
    }).catch(function (error) {
      if (inflightRefresh === controller) { inflightRefresh = null; }
      if (error && error.message === "unauthorized") {
        loadError = "Sign in again — the device token was rejected.";
        snapshot = null;
        stopPolling();
        render();
        return;
      }
      if (error && (error.name === "AbortError" || error.aborted)) { return; }
      if (generation < appliedGeneration) { return; }
      loadError = "Cofferdam could not reach the workstation to read tasks.";
      snapshot = null;
      render();
    });
  }

  function loadDetail(taskId) {
    var generation = nextDetailGeneration();
    var requested = taskId;
    return deps.api("/api/tasks/" + encodeURIComponent(taskId)).then(function (response) {
      if (!response.ok) {
        loadError = "That task could not be read.";
        render();
        return null;
      }
      /* Two guards, and they are different questions. The generation keeps an
         older detail response from painting over a newer one. The id check
         keeps a response for a task the person has since navigated away from
         out of the view entirely. */
      if (generation < appliedDetailGeneration) { return null; }
      if (openTaskId !== requested) { return null; }
      appliedDetailGeneration = generation;
      detail = response.payload.task;
      settleTerminalDrafts(detail);
      return deps.api(
        "/api/tasks/" + encodeURIComponent(taskId) + "/events?after=0&limit=200"
      );
    }).then(function (response) {
      if (response && response.ok && openTaskId === requested) {
        detailEvents = response.payload.events || [];
      }
      /* Questions are read only when the task says it is waiting on one, and
         only for an adapter that asks them. Two conditions rather than one
         because they cost different things: the state check keeps this off every
         ordinary poll, and the capability check keeps it off adapters whose
         transport has no question channel — the Claude Code adapter waits for
         people too, and asking it for structured questions would be a request
         per poll that can only ever return an empty list. */
      if (
        openTaskId === requested && detail &&
        detail.state === "waiting_for_user" &&
        capabilityOf(detail, "clarifications")
      ) {
        return deps.api(
          "/api/tasks/" + encodeURIComponent(taskId) + "/clarifications"
        );
      }
      /* Not waiting any more: whatever was on screen is history. Clearing it
         here rather than leaving it is what stops an answered question from
         still offering its form after the turn has moved on. */
      if (openTaskId === requested) { detailQuestions = []; }
      return null;
    }).then(function (response) {
      if (response && response.ok && openTaskId === requested) {
        detailQuestions = (response.payload && response.payload.clarifications) || [];
      }
      render();
      reschedule();
      return detail;
    }).catch(function (error) {
      if (error && error.message === "unauthorized") {
        /* Not a generic failure, and not silence either. The shell has already
           cleared the token and put the connection into "token rejected"; this
           makes the panel say the same thing rather than leaving the last good
           detail on screen looking current. */
        loadError = "Sign in again — the device token was rejected.";
        stopPolling();
        render();
        return null;
      }
      loadError = "Cofferdam could not reach the workstation to read that task.";
      render();
      return null;
    });
  }

  /* --------------------------------------------------------------- actions */

  function beginPending(key, timeoutMs) {
    if (pending !== null) { return false; }   /* one action at a time */
    pending = key;
    actionError = null;
    actionNote = null;
    formError = null;
    abortInflightRefresh();
    stopPolling();
    if (pendingTimer) { global.clearTimeout(pendingTimer); }
    pendingTimer = global.setTimeout(function () {
      if (pending !== null) {
        endPending();
        actionError = {
          message: "That did not finish in time, so Cofferdam cannot say whether it worked.",
          detail: "Refresh to see what actually happened before trying again."
        };
        render();
      }
    }, timeoutMs || ACTION_TIMEOUT_MS);
    render();
    return true;
  }

  function endPending() {
    pending = null;
    if (pendingTimer) { global.clearTimeout(pendingTimer); pendingTimer = null; }
    reschedule();
  }

  function failureOf(response) {
    var error = (response.payload && response.payload.error) || {};
    return {
      code: error.code || null,
      message: error.message || "That was refused.",
      detail: error.detail || null
    };
  }

  function startTask() {
    if (!draft.projectId || !draft.adapterId) {
      formError = "Choose a project and an adapter first.";
      render();
      return Promise.resolve(null);
    }
    if (!draft.prompt.trim()) {
      formError = "Describe what the task should do.";
      render();
      return Promise.resolve(null);
    }
    if (draft.prompt.length > MAX_PROMPT_CHARS) {
      formError = "That is longer than " + MAX_PROMPT_CHARS + " characters.";
      render();
      return Promise.resolve(null);
    }
    if (!beginPending("create", CREATE_TIMEOUT_MS)) { return Promise.resolve(null); }

    var generation = nextGeneration();
    /* Keyed on the prompt, with no task id: there is no task yet, and the whole
       point of the key is that the server can recognise a repeat before one
       exists. Editing the prompt after a refusal mints a new key, because it is
       a different request — the server would otherwise answer the second attempt
       with an idempotency conflict rather than creating anything. */
    var content = draft.projectId + "|" + draft.adapterId + "|" + draft.prompt;
    return deps.api("/api/tasks", {
      body: {
        project_id: draft.projectId,
        adapter_id: draft.adapterId,
        prompt: draft.prompt,
        client_request_id: requestIdFor("create", null, content)
      }
    }).then(function (response) {
      endPending();
      if (!response.ok) {
        actionError = failureOf(response);
        /* The key is kept. A refusal is exactly when somebody presses the button
           again, and a retry carrying a new key is a retry the server cannot
           recognise as one — which is how a "that timed out" turns into two
           tasks. It is released when the prompt changes, or when a create is
           accepted. */
        return load().then(function () { return null; });
      }
      releaseRequestId("create", null);
      var payload = response.payload || {};
      /* The server's own verdict on whether anything was created. A retry that
         matched an earlier request says so rather than implying a second task. */
      actionNote = payload.created
        ? "Task started."
        : "That task was already started — this was the same request.";
      draft.prompt = "";
      composerOpen = false;
      openTaskId = payload.task ? payload.task.task_id : null;
      detail = payload.task || null;
      settleTerminalDrafts(detail);
      detailEvents = [];
      detailEvidence = null;
      evidenceTurn = null;
      detailAssessment = null;
      assessmentTurn = null;
      appliedGeneration = generation;
      render();
      if (openTaskId) { loadDetail(openTaskId); }
      return load();
    }).catch(function (error) {
      endPending();
      if (error && error.message === "unauthorized") { return null; }
      /* The key is deliberately *kept* on a network failure: the request may
         well have reached the server, and retrying with the same key is what
         makes finding out safe. */
      actionError = { message: "Cofferdam could not reach the workstation.", detail: null };
      render();
      return null;
    });
  }

  function sendFollowup() {
    if (!openTaskId) { return Promise.resolve(null); }
    var taskId = openTaskId;
    /* Refused here as well as by the server. While a question is open the
       follow-up route answers `task_clarification_pending`, and a panel that
       could send one anyway would be a panel that turns somebody's answer into
       a refusal for reasons they cannot see. */
    if (pendingQuestion()) {
      actionError = {
        message: "This task is waiting for an answer to a question.",
        detail: "Answer the question above — a follow-up cannot answer it."
      };
      render();
      return Promise.resolve(null);
    }
    /* The live box first, the stored draft second. They agree except in the
       moment between a keystroke and the next capture. */
    var box = draftBox(OP_FOLLOWUP);
    var text = box && typeof box.value === "string"
      ? box.value
      : draftFor(taskId, OP_FOLLOWUP);
    text = String(text || "").slice(0, MAX_DRAFT_CHARS);
    writeDraft(taskId, OP_FOLLOWUP, text);

    if (!text.trim()) {
      actionError = { message: "Write something to send first.", detail: null };
      render();
      return Promise.resolve(null);
    }
    if (!beginPending("followup")) { return Promise.resolve(null); }

    /* The draft is NOT cleared here. Clearing on submit means a refusal, a
       dropped connection or a 422 loses what somebody wrote — and the moment
       most likely to fail is the one where the text matters most. It is cleared
       after the server accepts it, and only then. */
    var sent = text;
    return deps.api("/api/tasks/" + encodeURIComponent(taskId) + "/followups", {
      body: {
        followup: sent,
        /* Keyed on the text as well as the task, so a retry of *this* message
           reuses one key and an edited message gets a new one. The server binds
           a key to a payload hash and answers the same key with different words
           as a conflict, which is the case this keying makes unreachable. */
        client_request_id: requestIdFor(OP_FOLLOWUP, taskId, sent)
      }
    }).then(function (response) {
      endPending();
      if (!response.ok) {
        /* Key kept, draft kept. Both survive a refusal for the same reason: the
           person is about to try again, and neither their words nor the server's
           ability to recognise the repeat should depend on the first attempt
           having succeeded. */
        actionError = failureOf(response);
        render();
        return loadDetail(taskId);
      }
      releaseRequestId(OP_FOLLOWUP, taskId);
      /* Accepted. Cleared at the node as well as the store — see
         `clearAcceptedDraft` — and only if what is there is still the text the
         server took. */
      clearAcceptedDraft(taskId, OP_FOLLOWUP, sent);
      actionNote = "Sent.";
      detail = response.payload.task;
      settleTerminalDrafts(detail);
      render();
      return loadDetail(taskId).then(function () { return load(); });
    }).catch(function (error) {
      endPending();
      if (error && error.message === "unauthorized") { return null; }
      actionError = { message: "Cofferdam could not reach the workstation.", detail: null };
      render();
      return null;
    });
  }

  /* Answer one specific question on one specific task.

     A different route, a different body and a different draft from a follow-up,
     all the way down — which is the point. The body is two fields and there is no
     third: no session id, no tool, no path, no allow/deny. The server refuses an
     unexpected key rather than ignoring it, and refuses an approval-shaped one by
     name; this panel has nowhere to put one because nothing here constructs one.

     There is deliberately **no** `client_request_id`. The answer route does not
     accept one and does not need one: a question is single-use, so a repeat
     arrives at a question whose status is no longer `pending` and is refused
     truthfully. That refusal *is* the idempotency, and it is the server's, which
     is where it belongs. */
  function answerClarification() {
    if (!openTaskId) { return Promise.resolve(null); }
    var question = pendingQuestion();
    if (!question) {
      actionError = {
        message: "There is no open question on this task any more.",
        detail: "It may have been answered, superseded, or closed by a restart."
      };
      render();
      return Promise.resolve(null);
    }
    var taskId = openTaskId;
    var questionId = question.question_id;
    var box = draftBox(OP_CLARIFICATION);
    var text = box && typeof box.value === "string"
      ? box.value
      : draftFor(taskId, OP_CLARIFICATION);
    text = String(text || "").slice(0, MAX_DRAFT_CHARS);
    writeDraft(taskId, OP_CLARIFICATION, text);

    var picked = (chosenOptions[questionId] || []).slice(0);
    if (!picked.length && !text.trim()) {
      actionError = {
        message: (question.options || []).length
          ? "Choose an option, or write an answer."
          : "Write an answer first.",
        detail: null
      };
      render();
      return Promise.resolve(null);
    }
    if (!beginPending("answer")) { return Promise.resolve(null); }

    var body = {};
    if (text.trim()) { body.answer = text; }
    if (picked.length) { body.option_ids = picked; }
    var sent = text;

    return deps.api(
      "/api/tasks/" + encodeURIComponent(taskId) +
      "/clarifications/" + encodeURIComponent(questionId) + "/answer",
      { body: body }
    ).then(function (response) {
      endPending();
      if (!response.ok) {
        /* Draft kept, exactly as for a follow-up. A refused answer is still
           somebody's words about a question that may still be open. */
        actionError = failureOf(response);
        render();
        return loadDetail(taskId);
      }
      /* The same clear, through the same helper. The question form usually
         disappears on the next render, which hid this instance of the defect on
         the phone — the node was gone before `captureDraft` could read it. That
         is luck, not a difference in the code, and the two paths should not
         differ on it. */
      clearAcceptedDraft(taskId, OP_CLARIFICATION, sent);
      delete chosenOptions[questionId];
      actionNote = "Answer sent.";
      detail = response.payload.task;
      settleTerminalDrafts(detail);
      detailQuestions = [];
      render();
      return loadDetail(taskId).then(function () { return load(); });
    }).catch(function (error) {
      endPending();
      if (error && error.message === "unauthorized") { return null; }
      actionError = { message: "Cofferdam could not reach the workstation.", detail: null };
      render();
      return null;
    });
  }

  /* Ask the result route what the latest completed turn produced.

     A read: it changes nothing, drives no adapter, and is the reason it is safe
     to offer as a button rather than fold into the poll. The provider session id
     the response carries is dropped here rather than merely left unrendered — a
     field that never enters this panel's state is one no future render can put on
     a screen. */
  function showResult() {
    if (!openTaskId) { return Promise.resolve(null); }
    if (!beginPending("result")) { return Promise.resolve(null); }
    var taskId = openTaskId;
    return deps.api("/api/tasks/" + encodeURIComponent(taskId) + "/result")
      .then(function (response) {
        endPending();
        if (!response.ok) {
          actionError = failureOf(response);
          render();
          return null;
        }
        var payload = (response.payload && response.payload.result) || {};
        detailResult = {
          task_id: payload.task_id,
          task_state: payload.task_state,
          task_terminal: payload.task_terminal,
          outcome: payload.outcome,
          turn_number: payload.turn_number,
          turn_count: payload.turn_count,
          result: payload.result,
          failure_summary: payload.failure_summary,
          follow_up_available: payload.follow_up_available,
          result_meaning: payload.result_meaning
        };
        render();
        return detailResult;
      }).catch(function (error) {
        endPending();
        if (error && error.message === "unauthorized") { return null; }
        actionError = {
          message: "Cofferdam could not reach the workstation.", detail: null
        };
        render();
        return null;
      });
  }

  /* Ask the evidence route what one turn claimed, what Cofferdam observed, and
     how the two relate.

     A read in the strongest sense the product has: the server assembles it from
     stored rows, runs no Git, opens no file and calls no provider, so pressing
     this cannot change what it reports. It is the reason this is a button and
     not a confirmation dialog.

     The fields copied into panel state are chosen rather than spread. Anything
     not named here cannot reach a render, which is the same discipline
     `showResult` applies to `provider_session_id`. */
  /* The assessment fetch. Named-field copy, exactly as `showEvidence` does it:
     a field the server adds later cannot reach a render until somebody names it
     here, and nothing about the response is reinterpreted on the way in. */
  function showAssessment() {
    if (!openTaskId || !detail) { return Promise.resolve(null); }
    if (!beginPending("assessment")) { return Promise.resolve(null); }
    var taskId = openTaskId;
    var turn = nextAssessmentTurn(detail);
    return deps.api(
      "/api/tasks/" + encodeURIComponent(taskId) + "/turns/" +
      encodeURIComponent(String(turn)) + "/assessment"
    ).then(function (response) {
      endPending();
      if (!response.ok) {
        actionError = failureOf(response);
        render();
        return null;
      }
      var view = (response.payload && response.payload.assessment) || {};
      var criteria = view.criteria || {};
      var evaluation = view.evaluation || {};
      /* Named-field copy for acceptance too, and `null` is copied as `null`
         rather than defaulted: `counts` and `requires_human` are tri-state, and
         "unknown" collapsing into "zero" or "no" here would undo the whole
         distinction the API went to trouble to keep. */
      var acceptance = view.acceptance || null;
      detailAssessment = {
        task_id: view.task_id,
        turn_number: view.turn_number,
        criteria: {
          state: criteria.state,
          recorded: criteria.recorded,
          snapshot_id: criteria.snapshot_id,
          criteria_fingerprint: criteria.criteria_fingerprint,
          criterion_count: criteria.criterion_count,
          items: (criteria.items || []).map(function (item) {
            return {
              criterion_id: item.criterion_id,
              ordinal: item.ordinal,
              kind: item.kind,
              predicate: item.predicate,
              path: item.path,
              to_path: item.to_path,
              operation: item.operation,
              description: item.description
            };
          })
        },
        evaluation: {
          state: evaluation.state,
          recorded: evaluation.recorded,
          evaluation_id: evaluation.evaluation_id,
          evaluator_version: evaluation.evaluator_version,
          criteria_state: evaluation.criteria_state,
          assembler_version: evaluation.assembler_version,
          evidence_input_fingerprint: evaluation.evidence_input_fingerprint,
          evaluation_fingerprint: evaluation.evaluation_fingerprint,
          result_count: evaluation.result_count,
          results: (evaluation.results || []).map(function (row) {
            return {
              criterion_id: row.criterion_id,
              ordinal: row.ordinal,
              result: row.result,
              reason: row.reason
            };
          })
        },
        acceptance: acceptance === null ? null : {
          aggregator_version: acceptance.aggregator_version,
          availability: acceptance.availability,
          availability_reason: acceptance.availability_reason,
          unavailable_cause: acceptance.unavailable_cause,
          unavailable_at_turn_number: acceptance.unavailable_at_turn_number,
          outcome: acceptance.outcome,
          counts: acceptance.counts === null || acceptance.counts === undefined
            ? null
            : {
                total: acceptance.counts.total,
                met: acceptance.counts.met,
                not_met: acceptance.counts.not_met,
                unverified: acceptance.counts.unverified
              },
          requires_human: acceptance.requires_human === undefined
            ? null
            : acceptance.requires_human,
          assessment_fingerprint: acceptance.assessment_fingerprint,
          acceptance_fingerprint: acceptance.acceptance_fingerprint
        }
      };
      assessmentTurn = view.turn_number;
      render();
      return detailAssessment;
    }).catch(function (error) {
      endPending();
      if (error && error.message === "unauthorized") { return null; }
      actionError = {
        message: "Cofferdam could not reach the workstation.", detail: null
      };
      render();
      return null;
    });
  }

  function showEvidence() {
    if (!openTaskId || !detail) { return Promise.resolve(null); }
    if (!beginPending("evidence")) { return Promise.resolve(null); }
    var taskId = openTaskId;
    var turn = nextEvidenceTurn(detail);
    return deps.api(
      "/api/tasks/" + encodeURIComponent(taskId) + "/turns/" +
      encodeURIComponent(String(turn)) + "/evidence"
    ).then(function (response) {
      endPending();
      if (!response.ok) {
        actionError = failureOf(response);
        render();
        return null;
      }
      var bundle = (response.payload && response.payload.evidence) || {};
      detailEvidence = {
        task_id: bundle.task_id,
        turn_number: bundle.turn_number,
        turn_attribution: bundle.turn_attribution,
        assembler_version: bundle.assembler_version,
        input_fingerprint: bundle.input_fingerprint,
        repository_reported_clean: bundle.repository_reported_clean,
        machine_observations_complete: bundle.machine_observations_complete,
        ingestion: bundle.ingestion || {},
        claims: bundle.claims || [],
        observations: bundle.observations || [],
        relationships: bundle.relationships || [],
        limitations: bundle.limitations || []
      };
      evidenceTurn = bundle.turn_number;
      render();
      return detailEvidence;
    }).catch(function (error) {
      endPending();
      if (error && error.message === "unauthorized") { return null; }
      actionError = {
        message: "Cofferdam could not reach the workstation.", detail: null
      };
      render();
      return null;
    });
  }

  function finishTask() {
    if (!openTaskId) { return Promise.resolve(null); }
    if (!beginPending("finish")) { return Promise.resolve(null); }
    var taskId = openTaskId;
    return deps.api("/api/tasks/" + encodeURIComponent(taskId) + "/finish", {
      body: {}
    }).then(function (response) {
      endPending();
      if (!response.ok) {
        actionError = failureOf(response);
        render();
        return loadDetail(taskId);
      }
      detail = response.payload.task;
      settleTerminalDrafts(detail);
      /* Repeated, not upgraded, for the same reason cancel repeats: the
         server's observed state is the only one worth showing. */
      actionNote = detail.state === "completed"
        ? "Task finished. The session was closed."
        : "The task is " + detail.state + ".";
      render();
      return loadDetail(taskId).then(function () { return load(); });
    }).catch(function (error) {
      endPending();
      if (error && error.message === "unauthorized") { return null; }
      actionError = { message: "Cofferdam could not reach the workstation.", detail: null };
      render();
      return null;
    });
  }

  function cancelTask() {
    if (!openTaskId) { return Promise.resolve(null); }
    if (!beginPending("cancel")) { return Promise.resolve(null); }
    var taskId = openTaskId;
    return deps.api("/api/tasks/" + encodeURIComponent(taskId) + "/cancel", {
      body: {}
    }).then(function (response) {
      endPending();
      if (!response.ok) {
        actionError = failureOf(response);
        render();
        return loadDetail(taskId);
      }
      detail = response.payload.task;
      settleTerminalDrafts(detail);
      /* The server's observed state, repeated rather than upgraded: a task that
         is still `cancelling` is not cancelled, and saying so is the point. */
      actionNote = detail.state === "cancelled"
        ? "Task cancelled."
        : "Cancellation requested. The task is " + detail.state + ".";
      render();
      return loadDetail(taskId).then(function () { return load(); });
    }).catch(function (error) {
      endPending();
      if (error && error.message === "unauthorized") { return null; }
      actionError = { message: "Cofferdam could not reach the workstation.", detail: null };
      render();
      return null;
    });
  }

  function copyResult() {
    var node = global.document && global.document.getElementById("taskResultText");
    if (!node || !global.navigator || !global.navigator.clipboard) { return; }
    try {
      global.navigator.clipboard.writeText(node.textContent || "");
      actionNote = "Result copied.";
      render();
    } catch (error) { /* a clipboard the browser refused is not a task failure */ }
  }

  /* --------------------------------------------------------------- polling */

  function visible() {
    var doc = global.document;
    return !doc || doc.visibilityState !== "hidden";
  }

  function wanted() {
    if (stopped || pending !== null) { return null; }
    return anyActive() ? ACTIVE_POLL_MS : POLL_MS;
  }

  function stopPolling() {
    if (timer) { global.clearInterval(timer); timer = null; }
    timerInterval = null;
  }

  function reschedule() {
    var interval = wanted();
    if (interval === null) { stopPolling(); return; }
    if (interval === timerInterval && timer) { return; }
    stopPolling();
    timerInterval = interval;
    timer = global.setInterval(function () {
      if (pending !== null || !visible()) { return; }
      if (openTaskId) { loadDetail(openTaskId); }
      load();
    }, interval);
  }

  /* ----------------------------------------------------------------- wiring */

  function mount(dependencies) {
    deps = dependencies;
    stopped = false;

    var root = deps.el("tasksPanel");
    if (root) {
      root.addEventListener("click", function (event) {
        var target = event.target;
        if (!target) { return; }

        var open = target.closest ? target.closest("[data-task-open]") : null;
        if (open) {
          /* Keep whatever is in the box before leaving the current task. Drafts
             are per task, so nothing here can carry into the one being opened. */
          captureDraft();
          openTaskId = open.getAttribute("data-task-open");
          detail = null;
          detailEvents = [];
          /* Evidence belongs to one turn of one task. Carrying it across would
             put the previous task's claims under this task's heading. */
          detailEvidence = null;
          evidenceTurn = null;
          detailAssessment = null;
          assessmentTurn = null;
          render();
          loadDetail(openTaskId);
          return;
        }

        switch (target.id) {
          case "tasksRefresh":
            loadCatalogues().then(function () {
              return openTaskId ? loadDetail(openTaskId) : load();
            }).catch(function () { /* rendered by load's own failure path */ });
            return;
          case "taskCompose": composerOpen = true; formError = null; render(); return;
          case "taskComposeCancel": composerOpen = false; formError = null; render(); return;
          case "taskStart": startTask(); return;
          case "taskBack":
            captureDraft();
            openTaskId = null;
            detail = null;
            detailEvents = [];
            detailEvidence = null;
            evidenceTurn = null;
            render();
            load();
            return;
          case "taskFollowupSend": sendFollowup(); return;
          case "taskAnswerSend": answerClarification(); return;
          case "taskFinish": finishTask(); return;
          case "taskCancel": cancelTask(); return;
          case "taskCopyResult": copyResult(); return;
          case "taskShowResult": showResult(); return;
          case "taskShowEvidence": showEvidence(); return;
          case "taskShowAssessment": showAssessment(); return;
          default: return;
        }
      });

      /* Which options are ticked, held here rather than read off the DOM at
         submit time. The DOM is destroyed on every render that changes anything,
         so a choice made before a poll would be a choice lost by it — the same
         defect the draft store exists to fix, in a control that cannot hold its
         own text. */
      root.addEventListener("change", function (event) {
        var target = event.target;
        if (!target || target.className !== "task-option-input") { return; }
        var question = pendingQuestion();
        if (!question) { return; }
        var id = String(target.value || "");
        if (!id) { return; }
        var multiple = question.answer_mode === "multiple_choice";
        var current = chosenOptions[question.question_id] || [];
        if (!multiple) {
          chosenOptions[question.question_id] = target.checked ? [id] : [];
          return;
        }
        var without = current.filter(function (entry) { return entry !== id; });
        chosenOptions[question.question_id] = target.checked
          ? without.concat([id])
          : without;
      });

      root.addEventListener("input", function (event) {
        if (!event.target) { return; }
        if (event.target.id === "taskPrompt") {
          /* Held in a variable rather than re-rendered per keystroke: a render
             would rebuild the textarea and lose the caret mid-sentence. */
          draft.prompt = String(event.target.value || "");
        }
      });

      root.addEventListener("change", function (event) {
        if (!event.target) { return; }
        if (event.target.id === "taskProject") {
          draft.projectId = String(event.target.value || "");
          /* Follow the new project's delegation. Only when the host named one
             and it is available: otherwise whatever the person already chose
             stays chosen, because silently changing somebody's selection is
             worse than leaving a default they can see and correct. */
          var delegated = defaultAdapterFor(draft.projectId);
          if (delegated) { draft.adapterId = delegated; }
          render();
        } else if (event.target.id === "taskAdapter") {
          draft.adapterId = String(event.target.value || "");
          render();
        }
      });

      root.addEventListener("toggle", function (event) {
        if (event.target && event.target.className === "task-advanced") {
          advancedOpen = !!event.target.open;
        }
      }, true);
    }

    /* Coming back to the app refreshes immediately.

       Polling has always stopped while the tab is hidden, which is right — a
       phone in a pocket is not asking for anything. What was missing is the
       other half: on returning, the panel waited out the rest of an interval
       before asking, so somebody who unlocked their phone to check on a task saw
       state up to ten seconds stale and had no way to know it. A task that
       finished while the screen was off is exactly the case somebody opens the
       app to see.

       One refresh, not a new timer: `reschedule` is still the only thing that
       creates an interval, and `beginPending` still suppresses reads while a
       write is in flight. */
    var doc = global.document;
    if (doc && typeof doc.addEventListener === "function") {
      visibilityHandler = function () {
        if (stopped || pending !== null || !visible()) { return; }
        if (openTaskId) { loadDetail(openTaskId); }
        load();
      };
      doc.addEventListener("visibilitychange", visibilityHandler);
    }

    reschedule();
    return loadCatalogues().then(function () { return load(); });
  }

  function stop() {
    stopped = true;
    stopPolling();
    abortInflightRefresh();
    pending = null;
    if (pendingTimer) { global.clearTimeout(pendingTimer); pendingTimer = null; }
    /* Everything goes with the token. What somebody asked the workstation to do
       is the most personal content in this product, and a signed-out device
       keeps none of it and makes no further requests. */
    snapshot = null;
    detail = null;
    detailEvents = [];
    detailQuestions = [];
    detailResult = null;
    detailEvidence = null;
    evidenceTurn = null;
    detailAssessment = null;
    assessmentTurn = null;
    chosenOptions = {};
    adapters = null;
    projects = null;
    loadError = null;
    actionError = null;
    actionNote = null;
    formError = null;
    openTaskId = null;
    composerOpen = false;
    draft = { projectId: null, adapterId: null, prompt: "" };
    requestKeys = {};
    /* Including what is on disk. A draft is the one thing this panel keeps
       outside its own memory, so it is the one thing that would otherwise
       survive a sign-out — and "the previous person's half-written instruction
       to an agent" is precisely the content that must not. */
    clearAllDrafts();
    var doc = global.document;
    if (visibilityHandler && doc && typeof doc.removeEventListener === "function") {
      doc.removeEventListener("visibilitychange", visibilityHandler);
    }
    visibilityHandler = null;
    render();
  }

  global.CofferdamTasks = {
    mount: mount,
    refresh: load,
    stop: stop,
    openTask: function (taskId) {
      openTaskId = taskId;
      detailEvidence = null;
      evidenceTurn = null;
      detailAssessment = null;
      assessmentTurn = null;
      return loadDetail(taskId);
    }
  };
})(window);
