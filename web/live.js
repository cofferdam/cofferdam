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
 */
(function (global) {
  "use strict";

  /* Conservative on purpose: each poll costs the workstation a walk of its
     process table. Fast enough that the view is current, slow enough that a
     phone left open on a desk is not a background load. The refresh button is
     there for when the user wants "now". */
  var POLL_MS = 30000;

  var KINDS = [
    {
      key: "displays",
      title: "Connected displays",
      note: "Panels this desktop session is driving right now."
    },
    {
      key: "applications",
      title: "Running applications",
      note: "Applications with live processes. Not the same as installed — see Configuration below."
    },
    {
      key: "processes",
      title: "Processes",
      note: "Processes owned by the Cofferdam user, identified by PID and start time."
    },
    {
      key: "windows",
      title: "Windows",
      note: "Windows belonging to running applications."
    }
  ];

  /* How many processes to draw before offering "show all". A desktop runs a
     few hundred; rendering them all into a phone on first paint is slow and
     nobody reads past the first screen anyway. */
  var PROCESS_PREVIEW = 40;

  var deps = null;
  var snapshot = null;
  var loading = false;
  var loadError = null;
  var expanded = {};
  var showAllProcesses = false;
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

  function statusBadge(collection) {
    var status = collection.status;
    if (status === "ok") {
      return badge(collection.count + " found");
    }
    if (status === "partial") {
      return badge(collection.count + " found · incomplete", "warn");
    }
    if (status === "unavailable") {
      return badge("unavailable", "warn");
    }
    return badge("error", "err");
  }

  function observedText(iso) {
    if (!iso) { return ""; }
    var parsed = new Date(iso);
    return isNaN(parsed.getTime()) ? "" : parsed.toLocaleTimeString();
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

    var details = open ? facts([
      ["Connector", value(item.connector)],
      item.drm_connector && item.drm_connector !== item.connector
        ? ["Kernel connector", value(item.drm_connector)] : null,
      ["Manufacturer", value(item.manufacturer)],
      ["Model", value(item.model)],
      ["Serial", value(item.serial)],
      ["Reported name", value(item.display_name)],
      ["Resolution", resolution ? esc(resolution) : value(null)],
      ["Refresh rate", refresh ? esc(refresh) : value(null)],
      ["Scale", value(item.scale)],
      ["Orientation", value(item.orientation)],
      ["Position", item.position ? esc(item.position.x + ", " + item.position.y) : value(null)],
      ["Physical size", item.physical_size_mm
        ? esc(item.physical_size_mm.width + " × " + item.physical_size_mm.height + " mm")
        : value(null)],
      ["Identity", value(item.identity && item.identity.source)
        + " · " + value(item.identity && item.identity.stability)],
      ["Hardware fingerprint", item.identity && item.identity.edid_sha256
        ? esc(item.identity.edid_sha256.slice(0, 16) + "…") : value(null)],
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

    var parts = [];
    parts.push(item.process_count + (item.process_count === 1 ? " process" : " processes"));
    if (has(item.started_at)) { parts.push("since " + observedDate(item.started_at)); }

    var details = open ? facts([
      ["Application definition", has(item.application_id)
        ? esc(item.application_id)
        : '<span class="unset">no definition matched this executable</span>'],
      ["Main process", esc("PID " + item.primary_pid)],
      ["Started", value(item.started_at)],
      ["Processes", esc(String(item.process_count))],
      ["Executable", value(item.executable_path)],
      ["Systemd unit", item.units && item.units.length ? esc(item.units.join(", ")) : value(null)],
      /* "unknown" is rendered as unset prose, never as a negative claim. */
      ["Launch source", item.launch_source === "confirmed_cofferdam"
        ? "Cofferdam started this"
        : item.launch_source === "confirmed_external"
          ? "started outside Cofferdam"
          : '<span class="unset">launch source not confirmed</span>'],
      /* Absent, not zero: window discovery is unavailable on this host, and a
         "0 windows" would be a claim nobody can currently make. */
      ["Windows", has(item.window_count)
        ? esc(String(item.window_count))
        : '<span class="unset">window discovery is unavailable on this host</span>'],
      ["Discovered by", value(item.backend)]
    ]) : "";

    return card(item.resource_id, item.display_name, parts.join(" · "), badges, item, details);
  }

  function observedDate(iso) {
    var parsed = new Date(iso);
    return isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
  }

  function processRow(item) {
    var bits = [
      "PID " + item.pid,
      item.state,
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

  function renderCollection(descriptor) {
    var collection = snapshot && snapshot.collections ? snapshot.collections[descriptor.key] : null;
    var body;

    if (!collection) {
      body = '<p class="reg-note">Loading…</p>';
    } else if (collection.status === "unavailable" || collection.status === "error") {
      /* The reason comes from the backend and is shown verbatim. This is the
         single most important state in the whole view: it is what stops
         "cannot see" being read as "nothing there". */
      body = '<p class="reg-note unavailable">' + esc(collection.reason) + "</p>";
    } else if (!collection.items.length) {
      body = '<p class="reg-note">None found right now.</p>';
    } else if (descriptor.key === "processes") {
      var items = showAllProcesses ? collection.items : collection.items.slice(0, PROCESS_PREVIEW);
      body = '<ul class="proc-list">' + items.map(processRow).join("") + "</ul>";
      if (collection.items.length > PROCESS_PREVIEW) {
        body += '<button class="ghost" type="button" id="toggleProcesses">' +
          (showAllProcesses
            ? "Show fewer"
            : "Show all " + collection.items.length) + "</button>";
      }
    } else {
      var render = descriptor.key === "displays" ? displayCard : applicationCard;
      body = '<ul class="rcards">' + collection.items.map(render).join("") + "</ul>";
    }

    var warnings = collection && collection.warnings && collection.warnings.length
      ? '<ul class="reg-warnings">' + collection.warnings.map(function (text) {
          return "<li>" + esc(text) + "</li>";
        }).join("") + "</ul>"
      : "";

    return '<div class="reg"><div class="reg-head"><h3>' + esc(descriptor.title) + "</h3>" +
      (collection ? statusBadge(collection) : badge("loading")) + "</div>" +
      '<p class="reg-note">' + esc(descriptor.note) + "</p>" + warnings + body + "</div>";
  }

  function render() {
    var root = deps.el("liveSections");
    if (!root) { return; }

    if (loadError) {
      root.innerHTML = '<p class="reg-note unavailable">' + esc(loadError) + "</p>";
    } else {
      root.innerHTML = KINDS.map(renderCollection).join("");
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
