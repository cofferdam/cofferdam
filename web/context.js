/* Cofferdam — the Project context panel (M2J PR4).
 *
 * The only panel in this PWA whose subject is a *boundary* rather than a
 * capability. Everything else here shows you something the host can do; this
 * shows you what would leave the host if an external surface asked, and it is
 * built to teach that distinction rather than to blur it.
 *
 * The rules it is written to:
 *
 *   1. **Read only, and structurally so.** There is no form, no button that
 *      writes, no objective editor, no workspace switch and no syncWorkspace.
 *      The only request this file makes is a GET. M2J PR4 owns the read; every
 *      mutation belongs to a milestone that has not shipped.
 *   2. **Two columns, never one.** Local state and the cloud-safe projection are
 *      rendered side by side and labelled, because the mistake this panel exists
 *      to prevent is somebody assuming the thing they can see locally is the
 *      thing that goes out. They are different objects with different rules.
 *   3. **No claim the sanitizer cannot support.** The words "no secrets",
 *      "sanitized" and "safe to share" do not appear. What appears is what the
 *      projection itself reports: which policy produced it, what it left out and
 *      why, and the residual limitations it carries in its own payload.
 *   4. **Nothing is logged.** No `console` call, same as tasks.js and for the
 *      same reason — this panel handles project memory.
 *   5. **Extracts, not documents.** Projected text is shown collapsed behind a
 *      disclosure. A panel that dumps 16 KiB of Markdown by default is one
 *      nobody reads, and "I skimmed past it" is how a leak stays unnoticed.
 */
(function (global) {
  "use strict";

  var deps = null;
  var stopped = false;
  var state = {
    loading: false,
    error: null,
    reason: null,
    workspace: null,
    context: null,
    projectId: null,
    expanded: {}
  };
  var generation = 0;

  function el(id) { return deps && deps.el ? deps.el(id) : null; }
  function esc(value) { return deps && deps.escapeHtml ? deps.escapeHtml(value) : ""; }

  /* Reason codes the host may return, in the operator's words. The map is
     closed: an unrecognised code renders as itself rather than as a guess, so a
     future reason shows up as something to look at instead of silently reading
     as one of these. */
  var REASONS = {
    project_not_found: "No project is configured under that id.",
    project_disabled: "That project is disabled.",
    workspace_not_configured: "No workspace is configured for that project.",
    workspace_ambiguous: "More than one enabled workspace names that project.",
    workspace_disabled: "The workspace for that project is disabled.",
    workspace_not_active:
      "That project's workspace is not the active one. Project context is read " +
      "from the workspace you are currently working in.",
    context_unavailable: "Project context could not be assembled.",
    projection_failed: "Project context could not be projected.",
    response_too_large:
      "The projection did not fit the response contract and was refused rather " +
      "than trimmed.",
    invalid_project_id: "That is not a valid project id."
  };

  function load() {
    if (!deps || stopped) { return Promise.resolve(); }
    var snapshot = state.workspace;
    var projectId = snapshot && snapshot.project_id;
    if (!projectId) {
      state.loading = false;
      state.context = null;
      render();
      return Promise.resolve();
    }
    var mine = ++generation;
    state.loading = true;
    state.error = null;
    state.reason = null;
    render();
    return deps.api("/api/projects/" + encodeURIComponent(projectId) + "/context")
      .then(function (payload) {
        if (stopped || mine !== generation) { return; }
        state.loading = false;
        state.projectId = projectId;
        state.context = payload && payload.context ? payload.context : null;
        render();
      })
      .catch(function (error) {
        if (stopped || mine !== generation) { return; }
        state.loading = false;
        state.context = null;
        /* The host's reason code, never an exception string. */
        state.reason = (error && (error.code || error.reason)) || null;
        state.error = REASONS[state.reason] || "Project context is unavailable.";
        render();
      });
  }

  function setWorkspace(snapshot) {
    state.workspace = snapshot || null;
    render();
  }

  function localColumn() {
    var workspace = state.workspace || {};
    var working = workspace.working_context || {};
    var rows = [];
    rows.push(["Workspace", workspace.display_name || workspace.workspace_id || "—"]);
    rows.push(["Project", workspace.project_display_name || workspace.project_id || "—"]);
    rows.push(["Objective", working.objective || "none recorded"]);
    rows.push(["Expected next step", working.expected_next_step || "none recorded"]);
    rows.push(["Plan checkpoint", working.plan_checkpoint || "—"]);
    rows.push(["Pending decision", working.pending_decision_ref || "—"]);

    var html = '<div class="ctx-col">';
    html += '<h3>Local state <span class="tag">host only</span></h3>';
    html += '<p class="muted small">Everything Cofferdam holds about this ' +
            'workspace. It stays on this machine.</p><dl class="ctx-list">';
    for (var i = 0; i < rows.length; i += 1) {
      html += "<dt>" + esc(rows[i][0]) + "</dt><dd>" + esc(String(rows[i][1])) + "</dd>";
    }
    html += "</dl></div>";
    return html;
  }

  function partRow(part, index) {
    var open = !!state.expanded[index];
    var bytes = typeof part.content_bytes === "number" ? part.content_bytes : 0;
    var flags = [];
    if (part.truncated) { flags.push("truncated to fit the budget"); }
    if (part.redactions && part.redactions.length) {
      flags.push("paths redacted");
    }
    var html = '<li class="ctx-part">';
    html += '<button class="ghost ctx-toggle" data-ctx-part="' + index + '">' +
            (open ? "Hide" : "Show") + "</button>";
    html += "<code>" + esc(part.source_ref) + "</code>";
    html += ' <span class="muted small">' + esc(String(bytes)) + " B";
    if (flags.length) { html += " · " + esc(flags.join(" · ")); }
    html += "</span>";
    if (open) {
      html += '<pre class="ctx-text">' + esc(part.text || "") + "</pre>";
    }
    html += "</li>";
    return html;
  }

  function cloudColumn() {
    var html = '<div class="ctx-col">';
    html += '<h3>Cloud-safe projection <span class="tag warn">may leave the host</span></h3>';

    if (state.loading) {
      return html + '<p class="muted">Loading…</p></div>';
    }
    if (state.error) {
      return html + '<p class="error">' + esc(state.error) + "</p></div>";
    }
    var context = state.context;
    if (!context) {
      return html + '<p class="muted">No projection available.</p></div>';
    }

    var budget = context.budget || {};
    html += '<p class="muted small">Produced by policy <code>' +
            esc(context.policy_id || "—") + "</code> (v" +
            esc(String(context.version)) + "), " +
            esc(String(budget.consumed || 0)) + " of " +
            esc(String(budget.total || 0)) + " bytes used.</p>";

    var parts = context.parts || [];
    if (!parts.length) {
      html += '<p class="muted">Nothing was eligible for projection.</p>';
    } else {
      html += '<ul class="ctx-parts">';
      for (var i = 0; i < parts.length; i += 1) { html += partRow(parts[i], i); }
      html += "</ul>";
    }

    var omissions = context.omissions || [];
    if (omissions.length) {
      /* Counts by reason rather than a list of every row: the interesting fact
         is "four things were excluded by policy", not four near-identical
         sentences. Nothing is dropped silently, and this is where that shows. */
      var counts = {};
      for (var j = 0; j < omissions.length; j += 1) {
        var reason = omissions[j].reason || "unknown";
        counts[reason] = (counts[reason] || 0) + 1;
      }
      html += '<h4 class="ctx-sub">Left out</h4><ul class="ctx-omit">';
      for (var key in counts) {
        if (Object.prototype.hasOwnProperty.call(counts, key)) {
          html += "<li><code>" + esc(key) + "</code> × " + esc(String(counts[key])) + "</li>";
        }
      }
      html += "</ul>";
    }

    var limitations = context.limitations || [];
    if (limitations.length) {
      html += '<details class="ctx-limits"><summary>What this projection does ' +
              "and does not guarantee (" + esc(String(limitations.length)) + ")</summary><ul>";
      for (var k = 0; k < limitations.length; k += 1) {
        html += "<li>" + esc(limitations[k]) + "</li>";
      }
      html += "</ul></details>";
    }
    return html + "</div>";
  }

  function render() {
    var root = el("contextPanel");
    if (!root) { return; }
    var body = el("contextBody");
    if (!body) { return; }
    body.innerHTML = '<div class="ctx-cols">' + localColumn() + cloudColumn() + "</div>";
  }

  function mount(dependencies) {
    deps = dependencies;
    stopped = false;
    var root = deps.el("contextPanel");
    if (root) {
      root.addEventListener("click", function (event) {
        var target = event.target;
        if (!target || !target.closest) { return; }
        var toggle = target.closest("[data-ctx-part]");
        if (toggle) {
          var index = toggle.getAttribute("data-ctx-part");
          state.expanded[index] = !state.expanded[index];
          render();
          return;
        }
        if (target.closest("#contextRefresh")) { load(); }
      });
    }
    render();
  }

  function stop() {
    stopped = true;
    generation += 1;
    state.loading = false;
    state.error = null;
    state.reason = null;
    state.context = null;
    state.workspace = null;
    state.expanded = {};
    render();
  }

  global.CofferdamContext = {
    mount: mount,
    refresh: load,
    setWorkspace: setWorkspace,
    stop: stop
  };
})(window);
