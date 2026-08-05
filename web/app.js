/* Cofferdam workstation PWA (M1).
 *
 * Talks only to this origin. The device token lives in localStorage on the
 * phone and is sent as a Bearer header for API calls, and as a WebSocket
 * subprotocol for the event channel (so it never lands in a URL or access log).
 */
(function (global) {
  "use strict";

  var TOKEN_KEY = "cofferdam.token";
  var SUBPROTOCOL = "cofferdam-token";

  var el = function (id) { return document.getElementById(id); };
  var token = null;
  var socket = null;
  var reconnectDelay = 1000;
  var heartbeatTimer = null;

  /* ---------------------------------------------------------------- helpers */

  function toast(message, kind) {
    var node = document.createElement("div");
    node.className = "toast " + (kind || "ok");
    node.textContent = message;
    el("toasts").appendChild(node);
    setTimeout(function () { node.remove(); }, 5000);
  }

  function setConn(state, text) {
    el("dot").className = "dot" + (state ? " " + state : "");
    el("connText").textContent = text;
  }

  function bytes(value) {
    if (typeof value !== "number") { return "—"; }
    var units = ["B", "KB", "MB", "GB", "TB"], index = 0, size = value;
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
    return size.toFixed(size >= 10 || index === 0 ? 0 : 1) + " " + units[index];
  }

  function duration(seconds) {
    if (typeof seconds !== "number") { return "—"; }
    var days = Math.floor(seconds / 86400), hours = Math.floor((seconds % 86400) / 3600), mins = Math.floor((seconds % 3600) / 60);
    if (days) { return days + "d " + hours + "h"; }
    if (hours) { return hours + "h " + mins + "m"; }
    return mins + "m";
  }

  function clockTime(iso) {
    if (!iso) { return ""; }
    var parsed = new Date(iso);
    return isNaN(parsed.getTime()) ? "" : parsed.toLocaleTimeString();
  }

  function api(path, options) {
    var settings = options || {};
    settings.headers = Object.assign({}, settings.headers || {}, {
      "Authorization": "Bearer " + token
    });
    if (settings.body !== undefined) {
      settings.headers["Content-Type"] = "application/json";
      settings.body = JSON.stringify(settings.body);
      settings.method = settings.method || "POST";
    }
    return fetch(path, settings).then(function (response) {
      if (response.status === 401) {
        forgetToken("Token rejected — enter it again.");
        throw new Error("unauthorized");
      }
      return response.json().then(function (payload) {
        return { ok: response.ok, status: response.status, payload: payload };
      });
    });
  }

  /* ------------------------------------------------------------------ views */

  function card(label, value, sub) {
    return '<div class="card"><div class="label">' + label + '</div><div class="value">' +
      value + "</div>" + (sub ? '<div class="sub">' + sub + "</div>" : "") + "</div>";
  }

  function escapeHtml(value) {
    return String(value === undefined || value === null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function renderStatus(data) {
    var host = data.host || {};
    var service = data.service || {};
    var memory = (typeof host.memory_used_bytes === "number" && typeof host.memory_total_bytes === "number")
      ? bytes(host.memory_used_bytes) + " / " + bytes(host.memory_total_bytes) : "—";
    var disk = (typeof host.disk_used_bytes === "number" && typeof host.disk_total_bytes === "number")
      ? bytes(host.disk_used_bytes) + " / " + bytes(host.disk_total_bytes) : "—";

    el("statusCards").innerHTML = [
      card("Host", escapeHtml(host.hostname || "—"), escapeHtml(host.platform || "")),
      card("Session", escapeHtml(host.session_type || "—"), escapeHtml("adapter: " + (host.adapter || "—"))),
      card("CPU", typeof host.cpu_percent === "number" ? host.cpu_percent.toFixed(0) + "%" : "—", ""),
      card("Memory", memory, ""),
      card("Disk", disk, ""),
      card("Uptime", duration(host.uptime_seconds), "Cofferdam API v" + escapeHtml(service.api_version || "?"))
    ].join("");

    el("stubBanner").hidden = !host.stub;
    el("hostNotes").innerHTML = (host.notes || []).map(function (note) {
      return "<li>" + escapeHtml(note) + "</li>";
    }).join("");

    var apps = data.applications || [];
    // Which applications this host can launch right now decides which browser
    // profiles are offerable, so the registry view is re-rendered with it.
    availableApplications = apps;
    if (registriesLoaded) { renderRegistries(); }

    var select = el("appSelect");
    var previous = select.value;
    select.innerHTML = apps.length
      ? apps.map(function (name) { return '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + "</option>"; }).join("")
      : '<option value="">no applications detected</option>';
    if (previous && apps.indexOf(previous) !== -1) { select.value = previous; }

    // Every control mirrors a capability the host reports right now. A host
    // with no logged-in desktop session reports them all false, so the phone
    // never offers a button that cannot do anything.
    var capabilities = host.capabilities || {};
    var canOpenApp = apps.length > 0 && capabilities.open_application !== false;
    el("btnOpenApp").disabled = !canOpenApp;
    select.disabled = !canOpenApp;
    el("btnScreenshot").disabled = capabilities.screenshot === false;
    el("btnOpenUrl").disabled = capabilities.open_url === false;
    el("urlInput").disabled = capabilities.open_url === false;
    renderUnavailableCapabilities(host, capabilities);
    renderProfileSelect();
  }

  // Capabilities the host reports as false are demoted out of the primary
  // control row into a collapsed area, with the host's own reason.
  //
  // Screen capture on this Wayland desktop is the case that forced it: the
  // backend correctly reports `screenshot: false`, but a disabled Screenshot
  // button still sat at the top of Control as the most prominent thing on the
  // page — reading as a broken feature rather than as one this desktop does not
  // offer. The capability stays truthful and stays visible; it just stops
  // being advertised as a normal action.
  var CAPABILITY_LABELS = {
    screenshot: "Screenshot",
    open_application: "Open app",
    open_url: "Open URL"
  };

  function renderUnavailableCapabilities(host, capabilities) {
    var container = el("capabilitiesUnavailable");
    var list = el("capabilityReasons");
    var slot = el("screenshotSlot");
    if (!container || !list) { return; }

    var unavailable = Object.keys(CAPABILITY_LABELS).filter(function (key) {
      return capabilities[key] === false;
    });

    // The host publishes prose reasons in `notes`; they are shown verbatim
    // rather than re-worded into something that might overstate the cause.
    var notes = (host.notes || []).slice();
    list.innerHTML = unavailable.map(function (key) {
      var reason = notes.filter(function (note) {
        return note.toLowerCase().indexOf(key.replace("_", " ")) !== -1
          || (key === "screenshot" && note.toLowerCase().indexOf("screen capture") !== -1);
      })[0];
      return "<li><strong>" + escapeHtml(CAPABILITY_LABELS[key]) + "</strong>" +
        (reason ? "<span class=\"muted\"> — " + escapeHtml(reason) + "</span>" : "") + "</li>";
    }).join("");

    el("capabilityCount").textContent = unavailable.length ? String(unavailable.length) : "";
    container.hidden = unavailable.length === 0;
    // Hidden, not merely disabled: a greyed-out primary button still claims a
    // place in the interface that this host cannot honour.
    if (slot) { slot.hidden = capabilities.screenshot === false; }
  }

  /* -------------------------------------------------------------- registries
   *
   * Everything below is READ-ONLY on purpose. M2A has no registry write API, so
   * the UI must not grow an edit form that would have nowhere to send itself.
   * Two states are called out explicitly rather than left to inference:
   * agent profiles are placeholders with no execution behind them, and
   * conversation routes are templates that route nothing. A card that merely
   * looked inert would still imply the feature exists.
   *
   * The titles and notes below also have to keep the three layers apart. None
   * of these sections is a runtime resource: a display entry is a label with
   * nothing discovered behind it yet, and a browser profile is a launch
   * preference, not an open window. Runtime discovery is a later milestone, and
   * until it exists the UI must not read as though it had already happened.
   */

  var REGISTRIES = [
    { name: "devices", title: "Devices", note: "Declared by you, not discovered. No addresses, credentials, or power control." },
    { name: "displays", title: "Display labels", note: "Optional labels, waiting for a display to be discovered. Not a list of connected displays." },
    { name: "applications", title: "Application definitions", note: "Code-owned allowlist: what Cofferdam can launch. Not what is installed, and not what is running." },
    { name: "browser_profiles", title: "Browser launch preferences", note: "Which browser opens a URL, and which domains it may open. Not an open window or a running process." },
    { name: "agent_profiles", title: "Agent profile placeholders", note: "Placeholders. No agent execution exists in this build." },
    { name: "conversation_routes", title: "Route templates", note: "Templates only. Nothing is routed in this build." }
  ];

  var registryData = {};
  var registriesLoaded = false;
  var availableApplications = [];

  function badge(text, kind) {
    return '<span class="badge' + (kind ? " " + kind : "") + '">' + escapeHtml(text) + "</span>";
  }

  function aliasLine(item) {
    return (item.aliases && item.aliases.length)
      ? "also called: " + item.aliases.map(escapeHtml).join(", ")
      : "";
  }

  function applicationById(id) {
    var registry = registryData.applications;
    if (!registry || registry.status !== "ok") { return null; }
    var found = registry.items.filter(function (item) { return item.id === id; });
    return found.length === 1 ? found[0] : null;
  }

  function applicationIsAvailable(application) {
    return !!application && availableApplications.indexOf(application.adapter_key) !== -1;
  }

  /* Plain text — callers that build HTML escape it themselves. */
  function policyText(policy) {
    if (!policy) { return ""; }
    if (policy.mode === "allow-all") { return "any http(s) site"; }
    return "only " + (policy.domains || []).join(", ") + " (and their subdomains)";
  }

  function describeItem(name, item) {
    var parts = [];
    var badges = [];

    if (!item.enabled) { badges.push(badge("disabled")); }

    if (name === "devices") {
      parts.push(escapeHtml(item.kind) + " · " + escapeHtml(item.platform));
      if (item.notes) { parts.push(escapeHtml(item.notes)); }
    } else if (name === "displays") {
      parts.push("on " + escapeHtml(item.device_id));
      if (item.match && item.match.connector_hint) {
        parts.push("connector hint " + escapeHtml(item.match.connector_hint));
      }
    } else if (name === "applications") {
      parts.push("adapter " + escapeHtml(item.adapter_key));
      // "available" alone reads as "running" to anyone who has not read the
      // three-layer model. This is a statement about the *definition* — the
      // executable was found, so a launch would work. Whether an instance is
      // running is runtime inventory, which does not exist yet.
      badges.push(availableApplications.indexOf(item.adapter_key) !== -1
        ? badge("installed — can launch", "ok")
        : badge("not installed here", "warn"));
    } else if (name === "browser_profiles") {
      var application = applicationById(item.application_id);
      parts.push("uses " + escapeHtml(application ? application.name : item.application_id));
      parts.push("allows " + escapeHtml(policyText(item.domain_policy)));
      if (item.preferred_display_id) {
        parts.push("prefers " + escapeHtml(item.preferred_display_id) + " (metadata only — no window is moved)");
      }
      if (item.default_for_url) { badges.push(badge("default for URLs", "ok")); }
      if (item.enabled && !applicationIsAvailable(application)) {
        badges.push(badge("browser unavailable", "warn"));
      }
    } else if (name === "agent_profiles") {
      parts.push("adapter kind " + escapeHtml(item.adapter_kind));
      badges.push(badge("not implemented", "warn"));
    } else if (name === "conversation_routes") {
      parts.push("from " + escapeHtml(item.source_kind) + " → " + escapeHtml(item.target_agent_profile_id));
      parts.push("return: " + escapeHtml(item.return_mode));
      badges.push(badge("template only", "warn"));
    }

    var alias = aliasLine(item);
    if (alias) { parts.push(alias); }

    return '<li class="reg-item' + (item.enabled ? "" : " off") + '">' +
      '<div class="title"><strong>' + escapeHtml(item.name) + "</strong>" +
      '<span class="id">' + escapeHtml(item.id) + "</span>" + badges.join("") + "</div>" +
      '<div class="meta">' + parts.join(" · ") + "</div></li>";
  }

  function renderRegistrySection(descriptor) {
    var entry = registryData[descriptor.name];
    var body;

    if (!entry) {
      body = '<p class="reg-note">Loading…</p>';
    } else if (entry.status === "unreachable") {
      body = '<p class="reg-note">Could not be loaded from the service.</p>';
    } else if (entry.status === "error") {
      // The service already reduced the failure to a safe, bounded sentence;
      // show it verbatim so the file can actually be fixed.
      body = '<p class="reg-note">' + escapeHtml(entry.error || "This registry's configuration is invalid.") + "</p>";
    } else if (!entry.items.length) {
      // Empty is a valid, fully working machine — not a missing feature and not
      // something to fill with sample data. Say so, so nobody "fixes" it by
      // copying the committed examples in.
      body = '<p class="reg-note">Nothing configured — this is normal, and everything still works.</p>';
    } else {
      body = '<ul class="reg-list">' +
        entry.items.map(function (item) { return describeItem(descriptor.name, item); }).join("") +
        "</ul>";
    }

    var status = !entry ? badge("loading")
      : entry.status === "ok" ? badge(entry.items.length + " item" + (entry.items.length === 1 ? "" : "s"))
      : badge("invalid", "err");

    return '<div class="reg"><div class="reg-head"><h3>' + escapeHtml(descriptor.title) + "</h3>" +
      status + '</div><p class="reg-note">' + escapeHtml(descriptor.note) + "</p>" + body + "</div>";
  }

  function renderRegistries() {
    el("registrySections").innerHTML = REGISTRIES.map(renderRegistrySection).join("");
    renderProfileSelect();
  }

  function loadRegistries() {
    return api("/api/registries").then(function (response) {
      var summaries = (response.payload && response.payload.registries) || [];
      var pending = summaries.map(function (summary) {
        if (summary.status !== "ok") {
          registryData[summary.name] = {
            status: "error",
            items: [],
            error: summary.error ? summary.error.message : null
          };
          return Promise.resolve();
        }
        return api("/api/registries/" + summary.name).then(function (detail) {
          registryData[summary.name] = {
            status: detail.ok ? "ok" : "error",
            items: (detail.payload && detail.payload.items) || [],
            error: detail.ok ? null : "This registry could not be read."
          };
        }).catch(function () {
          registryData[summary.name] = { status: "unreachable", items: [], error: null };
        });
      });
      return Promise.all(pending);
    }).then(function () {
      registriesLoaded = true;
      renderRegistries();
    }).catch(function (error) {
      if (error.message === "unauthorized") { return; }
      REGISTRIES.forEach(function (descriptor) {
        if (!registryData[descriptor.name]) {
          registryData[descriptor.name] = { status: "unreachable", items: [], error: null };
        }
      });
      registriesLoaded = true;
      renderRegistries();
    });
  }

  /* Profile picker for Open URL. Only enabled profiles are offered, and one
     whose browser is missing is shown but not selectable — hiding it would
     make a configured profile look like it had never existed. */
  function enabledProfiles() {
    var registry = registryData.browser_profiles;
    if (!registry || registry.status !== "ok") { return []; }
    return registry.items.filter(function (item) { return item.enabled; });
  }

  function renderProfileSelect() {
    var select = el("profileSelect");
    var profiles = enabledProfiles();
    var previous = select.value;

    var defaults = profiles.filter(function (item) { return item.default_for_url; });
    var defaultLabel = defaults.length === 1
      ? "Default — " + defaults[0].name
      : "Default browser";

    var options = ['<option value="">' + escapeHtml(defaultLabel) + "</option>"];
    profiles.forEach(function (profile) {
      var application = applicationById(profile.application_id);
      var usable = applicationIsAvailable(application);
      options.push('<option value="' + escapeHtml(profile.id) + '"' + (usable ? "" : " disabled") + ">" +
        escapeHtml(profile.name) + (usable ? "" : " — unavailable") + "</option>");
    });
    select.innerHTML = options.join("");
    if (previous && profiles.some(function (p) { return p.id === previous; })) { select.value = previous; }

    select.disabled = profiles.length === 0;
    renderProfileHint();
  }

  function selectedProfile() {
    var id = el("profileSelect").value;
    if (!id) { return null; }
    var matches = enabledProfiles().filter(function (item) { return item.id === id; });
    return matches.length === 1 ? matches[0] : null;
  }

  function renderProfileHint() {
    var profile = selectedProfile();
    var hint = el("profileHint");
    if (!profile) {
      var defaults = enabledProfiles().filter(function (item) { return item.default_for_url; });
      hint.textContent = defaults.length === 1
        ? "Uses “" + defaults[0].name + "”, your configured default for URLs."
        : registriesLoaded && enabledProfiles().length === 0
          ? "No browser profiles configured — the host's usual browser is used."
          : "Uses the host's usual browser.";
      return;
    }
    var text = "Allows " + policyText(profile.domain_policy) + ".";
    if (profile.preferred_display_id) {
      text += " Prefers " + profile.preferred_display_id + " (metadata only — M2A does not move windows).";
    }
    hint.textContent = text;
  }

  var recent = [];

  function renderActions() {
    el("actionList").innerHTML = recent.slice(0, 12).map(function (record) {
      var detail = "";
      if (record.status === "failed" && record.error) {
        detail = record.error.message || "";
      } else if (record.action === "open_url" && record.params) {
        detail = record.params.url || "";
      } else if (record.action === "open_application" && record.params) {
        detail = record.params.application || "";
      }
      return "<li>" +
        '<span class="st ' + escapeHtml(record.status) + '">' + escapeHtml(record.status) + "</span>" +
        "<span>" + escapeHtml(record.action) + "</span>" +
        (detail ? '<span class="detail">' + escapeHtml(detail) + "</span>" : "") +
        '<span class="when">' + escapeHtml(clockTime(record.finished_at || record.started_at)) + "</span>" +
        "</li>";
    }).join("");
  }

  function upsertAction(record) {
    var index = recent.findIndex(function (item) { return item.action_id === record.action_id; });
    if (index === -1) { recent.unshift(record); } else { recent[index] = record; }
    recent = recent.slice(0, 20);
    renderActions();
  }

  function showScreenshot(url) {
    fetch(url, { headers: { "Authorization": "Bearer " + token } })
      .then(function (response) { return response.ok ? response.blob() : Promise.reject(new Error("screenshot fetch failed")); })
      .then(function (blob) {
        var image = el("shotImage");
        if (image.dataset.objectUrl) { URL.revokeObjectURL(image.dataset.objectUrl); }
        var objectUrl = URL.createObjectURL(blob);
        image.dataset.objectUrl = objectUrl;
        image.src = objectUrl;
        el("shotPanel").hidden = false;
        el("shotPanel").scrollIntoView({ behavior: "smooth", block: "nearest" });
      })
      .catch(function () { toast("Could not load the screenshot.", "err"); });
  }

  /* ------------------------------------------------------------- websocket */

  function connect() {
    if (!token) { return; }
    var scheme = location.protocol === "https:" ? "wss:" : "ws:";
    try {
      socket = new WebSocket(scheme + "//" + location.host + "/ws", [SUBPROTOCOL, token]);
    } catch (error) {
      setConn("down", "offline");
      return;
    }

    socket.onopen = function () {
      reconnectDelay = 1000;
      setConn("live", "live");
      clearInterval(heartbeatTimer);
      heartbeatTimer = setInterval(function () {
        if (socket && socket.readyState === WebSocket.OPEN) { socket.send("ping"); }
      }, 20000);
    };

    socket.onmessage = function (event) {
      var message;
      try { message = JSON.parse(event.data); } catch (error) { return; }
      if (message.event === "hello") {
        recent = message.data.recent_actions || [];
        renderActions();
      } else if (message.event === "status") {
        renderStatus(message.data);
      } else if (message.event === "action_started" || message.event === "action_finished") {
        upsertAction(message.data);
      }
    };

    socket.onclose = function (event) {
      clearInterval(heartbeatTimer);
      socket = null;
      if (event.code === 4401) { forgetToken("Token rejected — enter it again."); return; }
      setConn("down", "reconnecting…");
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    };

    socket.onerror = function () { setConn("down", "offline"); };
  }

  /* ------------------------------------------------------------ token flow */

  function forgetToken(message) {
    localStorage.removeItem(TOKEN_KEY);
    token = null;
    if (socket) { socket.close(); socket = null; }
    // Drop the machine's registry contents with the token: they describe the
    // user's devices and displays and should not survive a sign-out.
    registryData = {};
    registriesLoaded = false;
    availableApplications = [];
    // The live inventory is the same kind of thing, only more so: it lists this
    // machine's displays and running applications. It goes with the token, and
    // its polling stops so a signed-out device makes no further requests.
    if (global.CofferdamLive) { global.CofferdamLive.stop(); }
    el("registrySections").innerHTML = '<p class="muted">Loading…</p>';
    el("app").hidden = true;
    el("setup").hidden = false;
    setConn("down", "not connected");
    if (message) {
      el("setupError").textContent = message;
      el("setupError").hidden = false;
    }
  }

  function start(candidate) {
    token = candidate;
    return api("/api/status").then(function (response) {
      if (!response.ok) { throw new Error("status failed"); }
      localStorage.setItem(TOKEN_KEY, candidate);
      el("setup").hidden = true;
      el("setupError").hidden = true;
      el("app").hidden = false;
      renderStatus(response.payload);
      return api("/api/actions");
    }).then(function (response) {
      recent = (response.payload && response.payload.actions) || [];
      renderActions();
      connect();
      // Registries are not needed to show the host, so they load after it and
      // a failure here never blocks the rest of the UI.
      loadRegistries();
      // Same for the live inventory: it is a separate view of a separate
      // layer, and a discovery failure must not take the control panel down.
      if (global.CofferdamLive) {
        global.CofferdamLive.mount({ api: api, escapeHtml: escapeHtml, el: el })
          .catch(function () { /* live.js renders its own failure state */ });
      }
    });
  }

  /* ---------------------------------------------------------------- wiring */

  function runAction(path, body, button) {
    button.disabled = true;
    return api(path, { body: body || {} }).then(function (response) {
      var record = response.payload;
      if (record && record.action_id) { upsertAction(record); }
      if (response.ok) {
        toast(record.action.replace(/_/g, " ") + " ok", "ok");
        if (record.result && record.result.screenshot_url) { showScreenshot(record.result.screenshot_url); }
      } else {
        var error = (record && (record.error || (record.error === undefined && record.detail))) || {};
        toast(error.message || "Action failed.", "err");
      }
    }).catch(function (error) {
      if (error.message !== "unauthorized") { toast("Request failed.", "err"); }
    }).then(function () {
      button.disabled = false;
    });
  }

  el("saveToken").addEventListener("click", function () {
    var candidate = el("tokenInput").value.trim();
    if (!candidate) { return; }
    el("setupError").hidden = true;
    start(candidate).catch(function () {
      el("setupError").textContent = "That token was not accepted.";
      el("setupError").hidden = false;
    });
  });

  el("tokenInput").addEventListener("keydown", function (event) {
    if (event.key === "Enter") { el("saveToken").click(); }
  });

  el("btnScreenshot").addEventListener("click", function () {
    runAction("/api/actions/screenshot", {}, el("btnScreenshot"));
  });

  el("btnOpenApp").addEventListener("click", function () {
    var application = el("appSelect").value;
    if (!application) { return; }
    runAction("/api/actions/open-application", { application: application }, el("btnOpenApp"));
  });

  el("profileSelect").addEventListener("change", renderProfileHint);

  el("urlForm").addEventListener("submit", function (event) {
    event.preventDefault();
    var url = el("urlInput").value.trim();
    if (!url) { return; }
    // Omitted entirely when no profile is picked, so the request stays byte-for
    // byte the pre-M2A one and takes the service's default/legacy path.
    var body = { url: url };
    var profileId = el("profileSelect").value;
    if (profileId) { body.browser_profile_id = profileId; }
    runAction("/api/actions/open-url", body, el("btnOpenUrl"));
  });

  el("forgetToken").addEventListener("click", function () { forgetToken(null); });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && token && (!socket || socket.readyState > WebSocket.OPEN)) { connect(); }
  });

  var stored = localStorage.getItem(TOKEN_KEY);
  if (stored) {
    start(stored).catch(function () { forgetToken("Stored token was rejected."); });
  } else {
    forgetToken(null);
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () { /* installability is optional */ });
  }
})(window);
