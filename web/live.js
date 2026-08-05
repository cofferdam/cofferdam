/* Cofferdam — the "Live system" area (M2B runtime inventory).
 *
 * Separate from app.js on purpose. app.js renders **configuration**: what the
 * user has declared and what the code can launch. This file renders **runtime
 * resources**: what is connected and running right now. Keeping them in
 * different files keeps the two vocabularies from drifting into each other —
 * "installed — can launch" belongs to one, "running" belongs strictly to the
 * other, and tests scan each file for the words the other must not use.
 *
 * Three rules this view is built around:
 *
 *   1. No sample data, ever. An empty collection renders as empty.
 *   2. `unavailable` is not `empty`. A collection the host cannot answer says
 *      so, in the words the backend supplied, and never as "0 items".
 *   3. Unknown values are absent, not guessed. A field the host did not report
 *      is left out of the card rather than filled with a plausible default.
 *
 * Prominence is a fourth, separate rule
 * -------------------------------------
 * Real-client validation on the phone found the first three all satisfied and
 * the result still wrong as a product: the primary list mixed Opera and Firefox
 * with three GNOME notification helpers, and the page rendered a wall of ~116
 * processes — systemd, D-Bus, PipeWire — before anything a person controls.
 *
 * Cofferdam is a workstation control plane, not a system monitor. So the
 * backend keeps discovering everything and returning everything, and *this*
 * file decides prominence:
 *
 *   * the primary application list is what the backend classified `user_facing`;
 *   * `background` and `unclassified` groups keep their cards, one tap away in
 *     collapsed sections — moved, never dropped;
 *   * the process inspector is collapsed, renders nothing until opened, and
 *     then offers search and a per-application filter;
 *   * a capability the host reports as false is not offered as a normal action.
 *
 * Nothing here filters the API. Every branch below is presentation.
 */
(function (global) {
  "use strict";

  /* Conservative on purpose: each poll costs the workstation a walk of its
     process table. Fast enough that the view is current, slow enough that a
     phone left open on a desk is not a background load. The refresh button is
     there for when the user wants "now". */
  var POLL_MS = 30000;

  /* How many processes to draw once the inspector is opened, before offering
     "show all". Opening is already an explicit action; this second bound keeps
     that action from pasting several hundred rows into a phone at once. */
  var PROCESS_PREVIEW = 40;

  /* Presentation buckets for running applications, in display order. The
     backend supplies the classification and the evidence for it; this table
     only says what each bucket is called and whether it leads the section.
     `primary: true` is the one list a user sees without tapping anything. */
  var APPLICATION_GROUPS = [
    {
      key: "user_facing",
      primary: true,
      title: null,
      note: null
    },
    {
      key: "background",
      primary: false,
      title: "Background services",
      note: "Helpers the desktop starts for itself — notification, update and settings daemons. " +
        "Classified from their own desktop entries (NoDisplay/Hidden, or an autostart entry), " +
        "not from their names."
    },
    {
      key: "unclassified",
      primary: false,
      title: "Other running groups",
      note: "Running and real, but with no decisive evidence either way. Listed here rather " +
        "than promoted into the primary list on a guess."
    }
  ];

  var deps = null;
  var snapshot = null;
  var loading = false;
  var loadError = null;
  var expanded = {};
  var sections = {};
  var showAllProcesses = false;
  var processQuery = "";
  var processInstance = "";
  var timer = null;

  function esc(value) { return deps.escapeHtml(value); }

  /* ------------------------------------------------------------- formatting */

  /* An absent value is rendered as "not reported" — a statement about the
     host, not a placeholder standing in for a value we think we know. */
  function value(raw) {
    if (raw === undefined || raw === null || raw === "") {
      return '<span class="unset">not reported</span>';
    }
    return esc(raw);
  }

  function has(raw) {
    return raw !== undefined && raw !== null && raw !== "";
  }

  function badge(text, kind) {
    return '<span class="badge' + (kind ? " " + kind : "") + '">' + esc(text) + "</span>";
  }

  function observedText(iso) {
    if (!iso) { return ""; }
    var parsed = new Date(iso);
    return isNaN(parsed.getTime()) ? "" : parsed.toLocaleTimeString();
  }

  function observedDate(iso) {
    var parsed = new Date(iso);
    return isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
  }

  function pixels(size) {
    if (!size || !has(size.width) || !has(size.height)) { return null; }
    return size.width + "×" + size.height;
  }

  function facts(rows) {
    var body = rows.filter(function (row) { return row; }).map(function (row) {
      return '<div class="fact"><dt>' + esc(row[0]) + "</dt><dd>" + row[1] + "</dd></div>";
    }).join("");
    return '<dl class="facts">' + body + "</dl>";
  }

  /* A short, stable handle for a resource. The full ID stays in the technical
     details; this is the part a person can read back over a phone call. It is
     a *reference*, not an identity claim — and deliberately not the PID, which
     is reused by the kernel and means nothing across a reboot. */
  function shortRef(resourceId) {
    if (!resourceId) { return ""; }
    var tail = String(resourceId).split("-").pop();
    return "#" + tail.slice(-8);
  }

  /* A nested disclosure inside an already-expanded card. Expanding a card
     should answer "what is this", not open a hardware datasheet; the datasheet
     goes one level further down. */
  function technical(id, rows) {
    var key = "tech:" + id;
    return '<details class="tech"' + (sections[key] ? " open" : "") + ">" +
      '<summary data-section="' + esc(key) + '">Technical details</summary>' +
      facts(rows) + "</details>";
  }

  function collectionOf(key) {
    return snapshot && snapshot.collections ? snapshot.collections[key] : null;
  }

  /* ------------------------------------------------------------------ cards */

  function displayCard(item) {
    var open = !!expanded[item.resource_id];
    var resolution = pixels(item.logical_size);
    var refresh = has(item.refresh_rate_hz) ? item.refresh_rate_hz + " Hz" : null;

    var badges = [];
    /* Classification is shown only when the host actually classified it. */
    if (item.internal === true) { badges.push(badge("built-in")); }
    if (item.internal === false) { badges.push(badge("external")); }
    if (item.primary === true) { badges.push(badge("primary")); }
    if (item.active === false) { badges.push(badge("not in use", "warn")); }
    if (item.identity && item.identity.stability === "weak") {
      badges.push(badge("weak identity", "warn"));
    }

    /* Hardware identity first — every candidate below is something the machine
       reported, never something invented. The model wins when the panel
       actually published a model *name*; when all it published was a numeric
       product code (`model_source` says so), the compositor's own description
       reads better and is equally real. Connector is the last resort and is
       always in the subtitle anyway.

       A user label, when one exists, is shown as an addition below — never in
       place of any of this. */
    var named = has(item.model) && item.model_source !== "edid-product-code";
    var heading = named ? item.model
      : has(item.display_name) ? item.display_name
      : has(item.model) ? item.model
      : item.connector;
    var subtitle = [item.connector, resolution, refresh].filter(has).join(" · ");

    /* Expanded: what the panel *is* and how it is arranged. Serial numbers,
       EDID fingerprints and discovery backends are real and kept — one level
       further in, because they answer a question almost nobody is asking at
       the moment they tap a display. */
    var details = open ? facts([
      ["Connector", value(item.connector)],
      ["Resolution", resolution ? esc(resolution) : value(null)],
      ["Refresh rate", refresh ? esc(refresh) : value(null)],
      ["Orientation", value(item.orientation)],
      ["Position", item.position ? esc(item.position.x + ", " + item.position.y) : value(null)],
      ["Scale", value(item.scale)]
    ]) + technical(item.resource_id, [
      item.drm_connector && item.drm_connector !== item.connector
        ? ["Kernel connector", value(item.drm_connector)] : null,
      ["Manufacturer", value(item.manufacturer)],
      ["Model", value(item.model)],
      ["Serial", value(item.serial)],
      ["Reported name", value(item.display_name)],
      ["Physical size", item.physical_size_mm
        ? esc(item.physical_size_mm.width + " × " + item.physical_size_mm.height + " mm")
        : value(null)],
      ["Identity", value(item.identity && item.identity.source)
        + " · " + value(item.identity && item.identity.stability)],
      ["Hardware fingerprint", item.identity && item.identity.edid_sha256
        ? esc(item.identity.edid_sha256.slice(0, 16) + "…") : value(null)],
      ["Match evidence", value(item.match_method)],
      ["Resource ID", value(item.resource_id)],
      ["Discovered by", value(item.backend)]
    ]) : "";

    return card(item.resource_id, heading, subtitle, badges, item, details);
  }

  function applicationCard(item) {
    var open = !!expanded[item.resource_id];

    var badges = [badge("running")];
    /* Launch attribution is three-valued. Only a confirmed attribution earns a
       badge: `unknown` says nothing here, because a badge reading "not launched
       by Cofferdam" would be a claim the backend cannot make — snapd re-parents
       snap launches and destroys the evidence either way. The expanded facts
       spell the uncertainty out; the badge row stays silent. */
    if (item.launch_source === "confirmed_cofferdam") {
      badges.push(badge("launched by Cofferdam"));
    } else if (item.launch_source === "confirmed_external") {
      badges.push(badge("launched outside Cofferdam"));
    }
    /* An unmatched instance is running and real; we simply cannot say which
       definition it is. Saying that out loud beats guessing. */
    if (!has(item.application_id)) { badges.push(badge("not matched to a definition", "warn")); }

    /* Subtitle carries the short reference, not the PID. The PID is still in
       the technical details, where it belongs: it is an operating-system
       handle, not a name for the thing. */
    var parts = [shortRef(item.resource_id)];
    parts.push(item.process_count + (item.process_count === 1 ? " process" : " processes"));
    if (has(item.started_at)) { parts.push("since " + observedDate(item.started_at)); }

    var details = open ? facts([
      ["Application definition", has(item.application_id)
        ? esc(item.application_id)
        : '<span class="unset">no definition matched this executable</span>'],
      ["Processes", esc(String(item.process_count))],
      ["Started", value(item.started_at)],
      ["Executable", value(item.executable_path)],
      /* Absent, not zero: window discovery is unavailable on this host, and a
         "0 windows" would be a claim nobody can currently make. */
      ["Windows", has(item.window_count)
        ? esc(String(item.window_count))
        : '<span class="unset">window discovery is unavailable on this host</span>']
    ]) + technical(item.resource_id, [
      ["Resource ID", value(item.resource_id)],
      ["Main process", esc("PID " + item.primary_pid)],
      ["Process start time", value(item.started_at)],
      ["Systemd unit", item.units && item.units.length ? esc(item.units.join(", ")) : value(null)],
      ["Mapping evidence", has(item.match_method)
        ? esc(item.match_method)
        : '<span class="unset">no definition matched this executable</span>'],
      /* "unknown" is rendered as unset prose, never as a negative claim. */
      ["Launch source", item.launch_source === "confirmed_cofferdam"
        ? "Cofferdam started this"
        : item.launch_source === "confirmed_external"
          ? "started outside Cofferdam"
          : '<span class="unset">launch source not confirmed</span>'],
      ["Classified as", esc(item.presentation || "unclassified") +
        (has(item.presentation_evidence) ? " · " + esc(item.presentation_evidence) : "")],
      ["Discovered by", value(item.backend)]
    ]) : "";

    return card(item.resource_id, item.display_name, parts.join(" · "), badges, item, details);
  }

  function processRow(item) {
    var bits = [
      "PID " + item.pid,
      item.state,
      has(item.started_at) ? "since " + observedDate(item.started_at) : null,
      item.unit
    ].filter(has);
    return '<li class="proc"><span class="proc-name">' + esc(item.name || item.executable || "—") +
      '</span><span class="proc-meta">' + esc(bits.join(" · ")) + "</span></li>";
  }

  function card(id, heading, subtitle, badges, item, details) {
    var open = !!expanded[id];
    var overlay = item.overlay
      ? '<div class="overlay-label">Your label: <strong>' + esc(item.overlay.label) + "</strong></div>"
      : "";
    return '<li class="rcard' + (open ? " open" : "") + '" data-id="' + esc(id) + '">' +
      '<button class="rcard-head" type="button" data-toggle="' + esc(id) + '">' +
      '<span class="rcard-title">' + esc(heading) + "</span>" +
      '<span class="rcard-sub">' + esc(subtitle) + "</span>" +
      '<span class="rcard-badges">' + badges.join("") + "</span>" +
      "</button>" + overlay +
      (open ? '<div class="rcard-body">' + details + "</div>" : "") +
      "</li>";
  }

  /* ------------------------------------------------------------- collections */

  function unavailableNote(collection) {
    /* The reason comes from the backend and is shown verbatim. This is the
       single most important state in the whole view: it is what stops
       "cannot see" being read as "nothing there". */
    return '<p class="reg-note unavailable">' + esc(collection.reason) + "</p>";
  }

  function renderDisplays() {
    var collection = collectionOf("displays");
    var body;
    if (!collection) {
      body = '<p class="reg-note">Loading…</p>';
    } else if (collection.status === "unavailable" || collection.status === "error") {
      body = unavailableNote(collection);
    } else if (!collection.items.length) {
      body = '<p class="reg-note">None found right now.</p>';
    } else {
      body = '<ul class="rcards">' + collection.items.map(displayCard).join("") + "</ul>";
    }
    return section("Connected displays", collection,
      "Panels this desktop session is driving right now.", body);
  }

  function renderApplications() {
    var collection = collectionOf("applications");
    if (!collection) {
      return section("Running applications", collection,
        "Applications with live processes.", '<p class="reg-note">Loading…</p>');
    }
    if (collection.status === "unavailable" || collection.status === "error") {
      return section("Running applications", collection,
        "Applications with live processes.", unavailableNote(collection));
    }

    var buckets = {};
    collection.items.forEach(function (item) {
      var key = item.presentation || "unclassified";
      if (!buckets[key]) { buckets[key] = []; }
      buckets[key].push(item);
    });

    var body = "";
    APPLICATION_GROUPS.forEach(function (group) {
      var items = buckets[group.key] || [];
      if (group.primary) {
        body += items.length
          ? '<ul class="rcards">' + items.map(applicationCard).join("") + "</ul>"
          : '<p class="reg-note">No user-facing applications are running right now.</p>';
        return;
      }
      if (!items.length) { return; }
      /* Moved, not dropped. The count is on the summary so the section is
         honest while closed — a collapsed section that hid its size would be
         its own small lie. */
      var key = "apps:" + group.key;
      body += '<details class="reg-advanced"' + (sections[key] ? " open" : "") + ">" +
        '<summary data-section="' + esc(key) + '">' + esc(group.title) +
        ' <span class="count">' + items.length + "</span></summary>" +
        '<p class="reg-note">' + esc(group.note) + "</p>" +
        '<ul class="rcards">' + items.map(applicationCard).join("") + "</ul>" +
        "</details>";
    });

    return section("Running applications", collection,
      "Applications with live processes. Not the same as installed — see Configuration below.",
      body, (buckets.user_facing || []).length + " shown");
  }

  function renderProcesses() {
    var collection = collectionOf("processes");
    if (!collection) {
      return section("Processes", collection, "", '<p class="reg-note">Loading…</p>');
    }
    if (collection.status === "unavailable" || collection.status === "error") {
      return section("Processes", collection, "", unavailableNote(collection));
    }

    var open = !!sections.processes;
    var total = collection.items.length;
    var body = '<details class="reg-advanced"' + (open ? " open" : "") + ">" +
      '<summary data-section="processes">Process inspector ' +
      '<span class="count">' + total + "</span></summary>";

    /* Rendering is skipped entirely while closed. The point of collapsing this
       was never to hide a scrollbar — it was to stop a phone building a
       hundred-plus DOM nodes nobody asked for on every poll. */
    if (open) {
      var instances = (collectionOf("applications") || { items: [] }).items;
      var options = ['<option value="">All applications</option>'].concat(
        instances.map(function (instance) {
          return '<option value="' + esc(instance.resource_id) + '"' +
            (processInstance === instance.resource_id ? " selected" : "") + ">" +
            esc(instance.display_name) + "</option>";
        })
      ).join("");

      var needle = processQuery.trim().toLowerCase();
      var shown = collection.items.filter(function (item) {
        if (processInstance && item.application_instance_id !== processInstance) { return false; }
        if (!needle) { return true; }
        var haystack = [item.name, item.executable, item.unit, "pid " + item.pid]
          .filter(has).join(" ").toLowerCase();
        return haystack.indexOf(needle) !== -1;
      });

      body += '<div class="proc-filters">' +
        '<input type="search" id="processQuery" aria-label="Filter processes by name, unit or PID" ' +
        'value="' + esc(processQuery) + '" autocomplete="off">' +
        '<select id="processInstance" aria-label="filter by application">' + options + "</select>" +
        "</div>";

      var visible = showAllProcesses ? shown : shown.slice(0, PROCESS_PREVIEW);
      body += '<p class="reg-note">' +
        esc(shown.length + " of " + total + " processes") + "</p>";
      body += visible.length
        ? '<ul class="proc-list">' + visible.map(processRow).join("") + "</ul>"
        : '<p class="reg-note">No process matches this filter.</p>';
      if (shown.length > PROCESS_PREVIEW) {
        body += '<button class="ghost" type="button" id="toggleProcesses">' +
          (showAllProcesses ? "Show fewer" : "Show all " + shown.length) + "</button>";
      }
    }

    body += "</details>";
    return section("Processes", collection,
      "Every process owned by the Cofferdam user, identified by PID and start time. " +
      "Diagnostic detail — open it when you need it.", body, total + " running");
  }

  /* Windows is a capability status, not a resource list. It has no items and
     will not have any on this host, so it renders as one compact row instead of
     a section-sized empty state — while still saying, in the backend's own
     words, that unavailable is not empty. */
  function renderWindows() {
    var collection = collectionOf("windows");
    if (!collection) { return ""; }
    if (collection.status === "ok" && collection.items.length) {
      return section("Windows", collection, "Windows belonging to running applications.",
        '<ul class="rcards">' + collection.items.map(function (item) {
          return card(item.resource_id, item.title || "window", "", [], item, "");
        }).join("") + "</ul>");
    }
    var key = "windows";
    return '<div class="reg capability-row">' +
      '<details' + (sections[key] ? " open" : "") + ">" +
      '<summary data-section="' + esc(key) + '">Windows ' +
      badge("unavailable", "warn") + "</summary>" +
      '<p class="reg-note unavailable">' + esc(collection.reason) + "</p>" +
      "</details></div>";
  }

  function section(title, collection, note, body, countText) {
    var status = collection
      ? (collection.status === "ok"
          ? badge(countText || (collection.count + " found"))
          : collection.status === "partial"
            ? badge(collection.count + " found · incomplete", "warn")
            : collection.status === "unavailable"
              ? badge("unavailable", "warn")
              : badge("error", "err"))
      : badge("loading");

    var warnings = collection && collection.warnings && collection.warnings.length
      ? '<ul class="reg-warnings">' + collection.warnings.map(function (text) {
          return "<li>" + esc(text) + "</li>";
        }).join("") + "</ul>"
      : "";

    return '<div class="reg"><div class="reg-head"><h3>' + esc(title) + "</h3>" + status + "</div>" +
      (note ? '<p class="reg-note">' + esc(note) + "</p>" : "") + warnings + body + "</div>";
  }

  function render() {
    var root = deps.el("liveSections");
    if (!root) { return; }

    if (loadError) {
      root.innerHTML = '<p class="reg-note unavailable">' + esc(loadError) + "</p>";
    } else {
      root.innerHTML = renderDisplays() + renderApplications() +
        renderWindows() + renderProcesses();
    }

    var stamp = deps.el("liveObserved");
    if (stamp) {
      stamp.textContent = loading
        ? "refreshing…"
        : snapshot ? "observed at " + observedText(snapshot.observed_at) : "";
    }
    var button = deps.el("liveRefresh");
    if (button) { button.disabled = loading; }
  }

  /* ------------------------------------------------------------------ wiring */

  function load(force) {
    loading = true;
    render();
    return deps.api("/api/runtime" + (force ? "?refresh=true" : "")).then(function (response) {
      if (!response.ok) { throw new Error("runtime unavailable"); }
      snapshot = response.payload;
      loadError = null;
    }).catch(function (error) {
      if (error.message === "unauthorized") { throw error; }
      snapshot = null;
      loadError = "The live inventory could not be read from the service.";
    }).then(function () {
      loading = false;
      render();
    });
  }

  function schedule() {
    if (timer) { clearInterval(timer); }
    timer = setInterval(function () {
      if (!document.hidden) { load(false); }
    }, POLL_MS);
  }

  function mount(dependencies) {
    deps = dependencies;

    document.addEventListener("click", function (event) {
      /* Disclosure state lives here rather than in the DOM, because every poll
         replaces the markup. Letting <details> own it would silently close
         whatever the user had open, every thirty seconds. */
      var summary = event.target.closest ? event.target.closest("summary[data-section]") : null;
      if (summary) {
        event.preventDefault();
        var name = summary.getAttribute("data-section");
        sections[name] = !sections[name];
        render();
        return;
      }
      var toggle = event.target.closest ? event.target.closest("[data-toggle]") : null;
      if (toggle) {
        var id = toggle.getAttribute("data-toggle");
        expanded[id] = !expanded[id];
        render();
        return;
      }
      if (event.target.id === "toggleProcesses") {
        showAllProcesses = !showAllProcesses;
        render();
        return;
      }
      if (event.target.id === "liveRefresh") {
        load(true);
      }
    });

    document.addEventListener("input", function (event) {
      if (event.target.id === "processQuery") {
        processQuery = event.target.value;
        showAllProcesses = false;
        render();
        var field = deps.el("processQuery");
        /* Re-rendering replaced the input the user is typing into. */
        if (field) { field.focus(); field.setSelectionRange(field.value.length, field.value.length); }
      }
    });

    document.addEventListener("change", function (event) {
      if (event.target.id === "processInstance") {
        processInstance = event.target.value;
        showAllProcesses = false;
        render();
      }
    });

    schedule();
    return load(false);
  }

  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
    snapshot = null;
    loadError = null;
    render();
  }

  global.CofferdamLive = { mount: mount, refresh: load, stop: stop };
})(window);
