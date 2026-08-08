/* Cofferdam — the Remote Control panel (M2H Lane A).
 *
 * Its own file beside tasks.js, and the separation is the one the backend
 * already makes and documents: tasks.js describes *delegated work* Cofferdam
 * runs on your behalf; this panel describes a *live interactive session* you
 * drive yourself, somewhere else. Merging them would put two different
 * authorities, two lifecycles and two threat models in one file.
 *
 * The rules, inherited from the panels that earned them plus two this milestone
 * adds:
 *
 *   1. **Nothing is claimed until the server has observed it.** Every control
 *      renders the status the server returned. Pressing Start does not make the
 *      card say "running".
 *   2. **One action at a time, bounded.** Controls disable while a request is in
 *      flight, and a timer gives the panel back if the request never answers.
 *   3. **An older answer never wins.** Status responses carry a monotonic
 *      generation; a response older than the newest applied one is dropped.
 *   4. **Nothing is logged.** There is no `console` call in this file. That rule
 *      is stricter here than anywhere: the value this panel handles for a few
 *      milliseconds is a capability URL.
 *   5. **The link is never fetched except by an explicit press.** Not on render,
 *      not on poll, not on hover, not to decide whether a button is enabled.
 *      `url_available` in the *status* payload — a boolean, never the URL — is
 *      what decides that. The link endpoint is called from exactly one place in
 *      this file, and a test asserts it.
 *   6. **The URL is not kept.** It exists in one local variable, for the length
 *      of one navigation, and is never written to component state, storage, the
 *      DOM, an anchor href, an error message, or this file's own render input.
 *
 * What this panel deliberately cannot do: name a unit, a path, an executable, a
 * flag or a working directory. Every request it makes carries a registered
 * project id and nothing else, exactly like the backend routes it calls.
 */
(function (global) {
  "use strict";

  var deps = null;

  /* A session's lifecycle changes at human speed. Ten seconds is enough to see
     a host move without asking the workstation to describe itself constantly to
     a phone lying on a desk — the same reasoning, and the same number, as the
     Tasks panel. */
  var POLL_MS = 10000;

  /* Faster while something is actually moving, and while a started host has not
     yet published its link. */
  var ACTIVE_POLL_MS = 4000;

  var ACTION_TIMEOUT_MS = 30000;

  /* Longer than the others on purpose: a stop waits out the CLI's shutdown,
     which M2H PR2 measured at about fifteen seconds because the child does not
     exit on SIGTERM. A shorter bound here would report a stop as failed while
     the backend was still, correctly, stopping. */
  var STOP_TIMEOUT_MS = 60000;

  /* Lifecycle strings, mirrored from the backend's closed vocabulary. An
     unrecognised one degrades to "unknown" rather than to an empty card. */
  var LIVE = ["starting", "running", "stopping"];

  /* The confirmed native link contract, mirrored — never loosened.
     Backend authority is cofferdam/workstation/sessions/links.py; this exists
     so a value that somehow arrived malformed is not navigated to. A test
     asserts these agree with the backend constants. */
  var LINK_ORIGINS = ["https://claude.ai", "https://www.claude.ai"];
  var LINK_PATH = "/code";
  var LINK_QUERY_KEY = "environment";
  var LINK_TOKEN = /^[A-Za-z0-9_-]{16,128}$/;

  var projects = null;         /* registered projects, from /api/task-projects */
  var statuses = {};           /* project_id -> last status the server returned */
  var selectedId = null;
  var loadError = null;
  var actionError = null;
  var actionNote = null;
  var pending = null;          /* "start" | "stop" | "open", or null */
  var pendingTimer = null;
  var appliedGeneration = 0;
  var requestGeneration = 0;
  var timer = null;
  var timerInterval = null;
  var stopped = false;
  var infoOpen = false;

  function esc(value) {
    return deps && deps.escapeHtml ? deps.escapeHtml(value) : String(value === undefined ? "" : value);
  }

  /* ------------------------------------------------------------------ state */

  function current() {
    return selectedId ? statuses[selectedId] || null : null;
  }

  function selectedProject() {
    if (!projects || !selectedId) { return null; }
    for (var i = 0; i < projects.length; i += 1) {
      if (projects[i].project_id === selectedId) { return projects[i]; }
    }
    return null;
  }

  function capable() {
    var project = selectedProject();
    return !!(project && project.remote_control_enabled);
  }

  function lifecycle() {
    var status = current();
    return status && status.state ? status.state : "unknown";
  }

  function isLive() {
    return LIVE.indexOf(lifecycle()) !== -1;
  }

  function linkAvailable() {
    var status = current();
    return !!(status && status.url_available === true);
  }

  function stateLabel(state) {
    switch (state) {
      case "stopped": return { text: "stopped", tone: "" };
      case "starting": return { text: "starting…", tone: "warn" };
      case "running": return { text: "running", tone: "ok" };
      case "stopping": return { text: "stopping…", tone: "warn" };
      case "failed": return { text: "failed", tone: "err" };
      case "unknown": return { text: "unknown", tone: "warn" };
      /* Not a state this build knows. Saying so is better than a blank badge. */
      default: return { text: state || "unknown", tone: "warn" };
    }
  }

  /* ---------------------------------------------------------------- rendering */

  function render() {
    var root = deps.el("remotePanel");
    if (!root) { return; }
    var body = root.querySelector ? root.querySelector("[data-remote-body]") : null;
    if (!body) { return; }
    body.innerHTML = view();
  }

  function view() {
    if (projects === null) {
      /* Nothing has been confirmed yet, so there is nothing to keep. A first
         load that failed shows its reason and no card. */
      return loadError
        ? '<p class="muted" role="status">' + esc(loadError) + "</p>" + infoView()
        : '<p class="muted" role="status">Loading…</p>';
    }
    if (!projects.length) {
      return '<p class="muted" role="status">No registered projects.</p>' + infoView();
    }

    /* A failed poll is a banner *above* the card, never a replacement for it.
       Dropping the card would turn "we could not ask" into "there is nothing
       there", which is the same fabrication as painting the host as stopped —
       and it would take the Stop button away at exactly the moment somebody
       might want it. */
    var banner = loadError
      ? '<p class="muted" role="status">' + esc(loadError) + "</p>"
      : "";
    return banner + chooser() + cardView() + infoView();
  }

  function chooser() {
    if (projects.length === 1) { return ""; }
    var options = projects.map(function (project) {
      return '<option value="' + esc(project.project_id) + '"' +
        (project.project_id === selectedId ? " selected" : "") + ">" +
        esc(project.display_name || project.project_id) + "</option>";
    }).join("");
    return '<div class="rc-choose"><label for="rcProject">Project</label>' +
      '<select id="rcProject">' + options + "</select></div>";
  }

  function cardView() {
    var project = selectedProject();
    if (!project) { return ""; }
    var status = current();
    var badge = stateLabel(lifecycle());

    var rows = "";
    rows += row("Remote Control", capable()
      ? '<span class="rc-badge ok">enabled</span>'
      : '<span class="rc-badge">not enabled</span>');
    rows += row("Session", '<span class="rc-badge ' + badge.tone + '">' + esc(badge.text) + "</span>");

    /* Only rendered when the backend actually observed the prompt. An absent
       field is not evidence of anything, so it says nothing. */
    if (status && status.awaiting_consent === true) {
      rows += row("Waiting", '<span class="rc-badge warn">needs Remote Control enabled on the workstation</span>');
    }
    if (isLive()) {
      rows += row("Link", linkAvailable()
        ? '<span class="rc-badge ok">available</span>'
        : '<span class="rc-badge">not published yet</span>');
    }
    if (status && status.last_seen_at) {
      rows += row("Last checked", '<span class="muted">' + esc(shortTime(status.last_seen_at)) + "</span>");
    }
    if (status && status.error) {
      rows += row("Detail", '<span class="muted">' + esc(status.error) + "</span>");
    }

    var notice = "";
    if (actionError) {
      notice = '<p class="rc-error" role="alert">' + esc(actionError) + "</p>";
    } else if (actionNote) {
      notice = '<p class="muted" role="status">' + esc(actionNote) + "</p>";
    }

    return '<div class="rc-card">' +
      '<h3 class="rc-title">' + esc(project.display_name || project.project_id) + "</h3>" +
      '<dl class="rc-rows">' + rows + "</dl>" +
      controls() + notice + "</div>";
  }

  function row(label, value) {
    return "<dt>" + esc(label) + "</dt><dd>" + value + "</dd>";
  }

  function shortTime(iso) {
    var parsed = new Date(iso);
    return isNaN(parsed.getTime()) ? "" : parsed.toLocaleTimeString();
  }

  function controls() {
    var state = lifecycle();
    var busy = pending !== null;

    /* Start: only when the capability is on and nothing is live. A capability
       that was revoked while a host runs still shows Stop below — refusing to
       stop something is a worse permission than refusing to start it. */
    var startOn = capable() && !busy && (state === "stopped" || state === "failed");
    var stopOn = !busy && (isLive() || state === "failed");
    var openOn = !busy && isLive() && linkAvailable();

    return '<div class="rc-actions">' +
      button("rcStart", pending === "start" ? "Starting…" : "Start", startOn, "primary") +
      button("rcStop", pending === "stop" ? "Stopping…" : "Stop", stopOn, "ghost") +
      button("rcOpen", pending === "open" ? "Opening…" : "Open Remote Control", openOn, "primary") +
      "</div>" +
      (capable() ? "" : '<p class="muted">Remote Control is not enabled for this project.</p>');
  }

  function button(id, label, enabled, kind) {
    return '<button id="' + id + '" class="' + kind + '"' +
      (enabled ? "" : " disabled aria-disabled=\"true\"") + ">" + esc(label) + "</button>";
  }

  function infoView() {
    /* Available, not shouted. The boundary matters and a person should be able
       to find it, but a card that warns on every render trains people to stop
       reading warnings. */
    return '<details class="rc-info"' + (infoOpen ? " open" : "") + ' id="rcInfo">' +
      "<summary>About Remote Control</summary>" +
      "<p>Cofferdam opens Claude's own Remote Control environment in a separate tab. " +
      "Cofferdam does not read, store or mirror the conversation.</p>" +
      "<p>Stopping the local host removes this link from Cofferdam, but does not revoke " +
      "an Anthropic environment link that has already been shared elsewhere.</p>" +
      "</details>";
  }

  /* ------------------------------------------------------------------ loading */

  function load() {
    if (stopped) { return Promise.resolve(); }
    requestGeneration += 1;
    var generation = requestGeneration;

    return deps.api("/api/task-projects").then(function (result) {
      if (!result.ok) { throw new Error("projects"); }
      var list = (result.payload && result.payload.projects) || [];
      projects = list;
      if (!selectedId && list.length) { selectedId = list[0].project_id; }
      if (selectedId && !selectedProject()) {
        selectedId = list.length ? list[0].project_id : null;
      }
      if (!selectedId) { loadError = null; render(); return null; }
      return deps.api("/api/remote-control/" + encodeURIComponent(selectedId));
    }).then(function (result) {
      if (result === null) { return; }
      if (generation < appliedGeneration) { return; }
      appliedGeneration = generation;
      if (result.ok && result.payload && result.payload.session) {
        statuses[selectedId] = result.payload.session;
        loadError = null;
      } else if (result.status === 404) {
        statuses[selectedId] = null;
        loadError = "That project is not registered.";
      } else {
        /* A refused *status* is not evidence the host stopped. The last
           confirmed lifecycle stays exactly as it was. */
        loadError = "Could not reach the workstation. Showing the last confirmed state.";
      }
      render();
      reschedule();
    }).catch(function () {
      if (generation < appliedGeneration) { return; }
      loadError = "Could not reach the workstation. Showing the last confirmed state.";
      render();
      reschedule();
    });
  }

  /* ------------------------------------------------------------------ actions */

  function beginPending(kind, timeoutMs) {
    pending = kind;
    actionError = null;
    actionNote = null;
    if (pendingTimer) { global.clearTimeout(pendingTimer); }
    pendingTimer = global.setTimeout(function () {
      /* The request never answered. Give the panel back rather than leaving
         every control disabled forever, and say nothing about the host — we do
         not know. */
      pending = null;
      pendingTimer = null;
      actionError = "That took too long to answer. The workstation may still be working.";
      render();
      reschedule();
    }, timeoutMs);
    render();
    reschedule();
  }

  function endPending() {
    pending = null;
    if (pendingTimer) { global.clearTimeout(pendingTimer); pendingTimer = null; }
  }

  function mutate(kind, path, timeoutMs) {
    if (pending !== null || !selectedId) { return Promise.resolve(); }
    beginPending(kind, timeoutMs);
    var target = selectedId;

    return deps.api("/api/remote-control/" + encodeURIComponent(target) + path, { method: "POST", body: {} })
      .then(function (result) {
        endPending();
        if (result.ok && result.payload && result.payload.session) {
          statuses[target] = result.payload.session;
          appliedGeneration = requestGeneration;
        } else {
          actionError = refusal(result);
        }
        render();
        reschedule();
        return load();
      }).catch(function () {
        endPending();
        actionError = "That request could not be sent.";
        render();
        reschedule();
      });
  }

  function refusal(result) {
    var error = (result && result.payload && result.payload.error) || {};
    if (error.message) { return String(error.message); }
    return "The workstation refused that.";
  }

  /* --------------------------------------------------------- opening the link */

  function validLink(value) {
    if (typeof value !== "string" || value.length > 512) { return false; }
    var origin = null;
    for (var i = 0; i < LINK_ORIGINS.length; i += 1) {
      if (value.indexOf(LINK_ORIGINS[i] + LINK_PATH + "?") === 0) { origin = LINK_ORIGINS[i]; }
    }
    if (origin === null) { return false; }
    var query = value.slice((origin + LINK_PATH + "?").length);
    if (query.indexOf(LINK_QUERY_KEY + "=") !== 0) { return false; }
    var token = query.slice((LINK_QUERY_KEY + "=").length);
    return LINK_TOKEN.test(token);
  }

  /* The only place in this file that calls the link endpoint.
   *
   * Ordering is load-bearing and mobile-driven. The blank tab is opened *first*,
   * synchronously inside the click, because Safari and Chrome on iOS only allow
   * a new tab during a user gesture — open it after the fetch resolves and it is
   * silently blocked. The tab is then severed from this page (`opener = null`,
   * equivalent to `noopener`; the `noopener` *feature string* is not usable here
   * because it makes `open` return null and there would be nothing left to
   * navigate). Referrer suppression comes from the page's own
   * `<meta name="referrer" content="no-referrer">` and the link response's
   * `Referrer-Policy`.
   *
   * The URL lives in one local variable. It is not rendered, not stored, not put
   * in an href, not included in any error text, and is dropped before this
   * function returns.
   */
  function openLink() {
    if (pending !== null || !selectedId) { return Promise.resolve(); }
    if (!isLive() || !linkAvailable()) { return Promise.resolve(); }

    var tab = null;
    try {
      tab = global.open ? global.open("", "_blank") : null;
    } catch (error) { tab = null; }
    if (tab) {
      try { tab.opener = null; } catch (error) { /* already severed */ }
    }

    beginPending("open", ACTION_TIMEOUT_MS);
    var target = selectedId;

    return deps.api("/api/remote-control/" + encodeURIComponent(target) + "/link")
      .then(function (result) {
        endPending();
        var url = result && result.ok && result.payload && result.payload.link
          ? result.payload.link.url : null;

        if (!validLink(url)) {
          url = null;
          if (tab) { try { tab.close(); } catch (error) { /* already gone */ } }
          /* Deliberately not the server's message verbatim and never the value:
             a refusal here is "there isn't one right now", and a failed link
             retrieval says nothing about whether the host is running. */
          actionError = result && result.status === 409
            ? "No link is available right now."
            : "Could not get the link.";
          render();
          reschedule();
          return;
        }

        if (tab) {
          try { tab.location.replace(url); } catch (error) {
            try { tab.close(); } catch (closeError) { /* already gone */ }
            actionError = "Could not open that tab.";
          }
        } else {
          /* No tab was granted — a blocked popup. Say so; do not fall back to
             navigating this page, which would put the capability in Cofferdam's
             own browser history. */
          actionError = "Your browser blocked the new tab. Allow pop-ups for Cofferdam and try again.";
        }
        url = null;
        tab = null;
        actionNote = actionError ? null : "Opened in a new tab.";
        render();
        reschedule();
      }).catch(function () {
        endPending();
        if (tab) { try { tab.close(); } catch (error) { /* already gone */ } }
        tab = null;
        actionError = "Could not get the link.";
        render();
        reschedule();
      });
  }

  /* ---------------------------------------------------------------- polling */

  function visible() {
    var doc = global.document;
    return !doc || doc.visibilityState !== "hidden";
  }

  function wanted() {
    if (stopped || pending !== null) { return null; }
    /* Faster while a host is moving, and while a running host has not yet
       published its link — that is the window a person is actually waiting in. */
    if (isLive() && !linkAvailable()) { return ACTIVE_POLL_MS; }
    return isLive() ? POLL_MS : POLL_MS;
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
      load();
    }, interval);
  }

  /* ----------------------------------------------------------------- wiring */

  function mount(dependencies) {
    deps = dependencies;
    stopped = false;

    var root = deps.el("remotePanel");
    if (root) {
      root.addEventListener("click", function (event) {
        var target = event.target;
        if (!target) { return; }
        switch (target.id) {
          case "rcRefresh": load(); return;
          case "rcStart": mutate("start", "/start", ACTION_TIMEOUT_MS); return;
          case "rcStop": mutate("stop", "/stop", STOP_TIMEOUT_MS); return;
          case "rcOpen": openLink(); return;
          default: return;
        }
      });
      root.addEventListener("change", function (event) {
        var target = event.target;
        if (target && target.id === "rcProject") {
          selectedId = target.value;
          actionError = null;
          actionNote = null;
          render();
          load();
        }
      });
      root.addEventListener("toggle", function (event) {
        var target = event.target;
        if (target && target.id === "rcInfo") { infoOpen = !!target.open; }
      }, true);
    }

    render();
    load();
    reschedule();
  }

  function stop() {
    stopped = true;
    stopPolling();
    if (pendingTimer) { global.clearTimeout(pendingTimer); pendingTimer = null; }
    pending = null;
    projects = null;
    statuses = {};
    selectedId = null;
    loadError = null;
    actionError = null;
    actionNote = null;
    appliedGeneration = 0;
    requestGeneration = 0;
    render();
  }

  global.CofferdamRemote = {
    mount: mount,
    refresh: load,
    stop: stop
  };
})(window);
