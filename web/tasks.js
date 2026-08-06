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

  /* States, mirrored from the backend's closed vocabulary. Rendering branches on
     these strings, so an unrecognised one must degrade to something honest
     rather than to an empty row — see `stateLabel`. */
  var TERMINAL = ["completed", "failed", "cancelled", "interrupted"];

  var snapshot = null;          /* the task list payload */
  var detail = null;            /* the open task, in full */
  var detailEvents = [];        /* its event history, oldest first */
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
  var pending = null;
  var pendingTimer = null;
  var actionError = null;
  var actionNote = null;
  var stopped = false;

  /* Response ordering, exactly as the Spotify and YouTube panels do it. */
  var refreshGeneration = 0;
  var appliedGeneration = 0;
  var inflightRefresh = null;

  /* One key per created task, minted here and sent with the request, so a
     double tap and a retry are the same request to the server rather than two.
     The server is authoritative; this is what lets it recognise the repeat. */
  var pendingRequestId = null;

  function esc(value) { return deps.escapeHtml(value); }

  /* ---------------------------------------------------------- reading state */

  function tasks() { return (snapshot && snapshot.tasks) || []; }
  function counts() { return (snapshot && snapshot.counts) || {}; }
  function adapterList() { return (adapters && adapters.adapters) || []; }
  function projectList() { return (projects && projects.projects) || []; }

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

  function detailActions(task) {
    var buttons = [];
    if (waitingForSecret(task)) {
      buttons.push(
        '<p class="media-note warn task-secret-wait">' +
        "<strong>Cofferdam will not ask you for this here.</strong> " +
        "Finish this on the workstation itself. Never type a password, " +
        "one-time code, passkey or token into a task." +
        "</p>"
      );
    } else if (task.state === "waiting_for_user" && capabilityOf(task, "followup")) {
      buttons.push(
        '<div class="task-followup">' +
        '<label class="field"><span class="field-label">Your answer</span>' +
        '<textarea id="taskFollowupText" rows="3" maxlength="' + MAX_FOLLOWUP_CHARS + '"' +
        (locked() ? " disabled" : "") + "></textarea></label>" +
        '<button id="taskFollowupSend" class="primary"' + (locked() ? " disabled" : "") + ">" +
        (busy("followup") ? "Sending…" : "Send answer") + "</button></div>"
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
    }
    return buttons.length ? '<div class="task-actions">' + buttons.join("") + "</div>" : "";
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

  function render() {
    if (!deps) { return; }
    var host = deps.el("tasksSections");
    if (!host) { return; }

    if (snapshot === null && !loadError) {
      host.innerHTML = '<p class="muted">Loading…</p>';
      return;
    }

    host.innerHTML = messages() + (openTaskId && detail ? detailView() : listView());

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
      if (error && error.message === "unauthorized") { return; }
      if (error && (error.name === "AbortError" || error.aborted)) { return; }
      if (generation < appliedGeneration) { return; }
      loadError = "Cofferdam could not reach the workstation to read tasks.";
      snapshot = null;
      render();
    });
  }

  function loadDetail(taskId) {
    var generation = nextGeneration();
    return deps.api("/api/tasks/" + encodeURIComponent(taskId)).then(function (response) {
      if (!response.ok) {
        loadError = "That task could not be read.";
        render();
        return null;
      }
      if (generation < appliedGeneration) { return null; }
      appliedGeneration = generation;
      detail = response.payload.task;
      return deps.api(
        "/api/tasks/" + encodeURIComponent(taskId) + "/events?after=0&limit=200"
      );
    }).then(function (response) {
      if (response && response.ok) {
        detailEvents = response.payload.events || [];
      }
      render();
      reschedule();
      return detail;
    }).catch(function (error) {
      if (error && error.message === "unauthorized") { return null; }
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

  /* A per-attempt key. Generated once when the composer's Start is first
     pressed and *kept* until that attempt resolves, so a second tap, a retry
     after a timeout, and a flaky connection all carry the same key — which is
     what lets the server recognise them as one request rather than three. */
  function requestId() {
    if (pendingRequestId === null) {
      pendingRequestId = "pwa-" + Date.now().toString(36) + "-" +
        Math.floor(Math.random() * 1e9).toString(36);
    }
    return pendingRequestId;
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
    return deps.api("/api/tasks", {
      body: {
        project_id: draft.projectId,
        adapter_id: draft.adapterId,
        prompt: draft.prompt,
        client_request_id: requestId()
      }
    }).then(function (response) {
      endPending();
      if (!response.ok) {
        actionError = failureOf(response);
        pendingRequestId = null;
        return load().then(function () { return null; });
      }
      pendingRequestId = null;
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
      detailEvents = [];
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
    var field = global.document && global.document.getElementById("taskFollowupText");
    var text = field ? String(field.value || "") : "";
    if (!text.trim()) {
      actionError = { message: "Write an answer first.", detail: null };
      render();
      return Promise.resolve(null);
    }
    if (!openTaskId) { return Promise.resolve(null); }
    if (!beginPending("followup")) { return Promise.resolve(null); }

    return deps.api("/api/tasks/" + encodeURIComponent(openTaskId) + "/followups", {
      body: { followup: text, client_request_id: requestId() }
    }).then(function (response) {
      endPending();
      pendingRequestId = null;
      if (!response.ok) {
        actionError = failureOf(response);
        render();
        return loadDetail(openTaskId);
      }
      actionNote = "Answer sent.";
      detail = response.payload.task;
      render();
      return loadDetail(openTaskId).then(function () { return load(); });
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
          openTaskId = open.getAttribute("data-task-open");
          detail = null;
          detailEvents = [];
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
            openTaskId = null;
            detail = null;
            detailEvents = [];
            render();
            load();
            return;
          case "taskFollowupSend": sendFollowup(); return;
          case "taskCancel": cancelTask(); return;
          case "taskCopyResult": copyResult(); return;
          default: return;
        }
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
    adapters = null;
    projects = null;
    loadError = null;
    actionError = null;
    actionNote = null;
    formError = null;
    openTaskId = null;
    composerOpen = false;
    draft = { projectId: null, adapterId: null, prompt: "" };
    pendingRequestId = null;
    render();
  }

  global.CofferdamTasks = {
    mount: mount,
    refresh: load,
    stop: stop,
    openTask: function (taskId) {
      openTaskId = taskId;
      return loadDetail(taskId);
    }
  };
})(window);
