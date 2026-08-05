/* Cofferdam workstation PWA (M1).
 *
 * Talks only to this origin. The device token is entered by the user, validated
 * against the API before it is stored, and kept in localStorage on the phone —
 * or in memory for the session when the browser refuses storage. It is sent as
 * a Bearer header for API calls and as a WebSocket subprotocol for the event
 * channel, so it never lands in a URL, history entry or access log.
 *
 * Connection state is explicit and bounded: connecting, authentication
 * required, authentication rejected, unreachable, connected. There is no path
 * that leaves the page on "connecting…" — that was a real fresh-device failure
 * on iOS Safari, where an unguarded `localStorage` read threw out of this
 * module before anything could render a state.
 */
(function (global) {
  "use strict";

  var TOKEN_KEY = "cofferdam.token";
  var SUBPROTOCOL = "cofferdam-token";

  /* Bounded, because "connecting…" forever is not a state — it is the absence
     of one. A fresh iPhone hit exactly that: the shell painted, the header said
     connecting…, and nothing ever changed it. Every path below now ends in one
     of these, and each one tells the user what to do next. */
  var CONN_CONNECTING = "connecting";
  var CONN_AUTH_REQUIRED = "auth_required";
  var CONN_AUTH_REJECTED = "auth_rejected";
  var CONN_UNREACHABLE = "unreachable";
  var CONN_CONNECTED = "connected";

  /* How long any single attempt may sit in `connecting` before it has to say
     something truthful instead. Generous enough for a slow Tailscale link,
     short enough that nobody stares at a dead header. */
  var BOOTSTRAP_TIMEOUT_MS = 8000;
  var SOCKET_OPEN_TIMEOUT_MS = 10000;

  var el = function (id) { return document.getElementById(id); };
  var token = null;
  var socket = null;
  var reconnectDelay = 1000;
  var heartbeatTimer = null;
  var socketTimer = null;
  var socketAttempts = 0;
  var connState = CONN_CONNECTING;

  /* ---------------------------------------------------------------- storage
   *
   * Every `localStorage` access is wrapped, because on iOS Safari the property
   * access itself throws — Private Browsing, "Block All Cookies", and some
   * lockdown/MDM configurations all raise SecurityError rather than returning
   * null. The original boot read `localStorage.getItem(...)` as its very first
   * statement with nothing around it, so that throw escaped the module IIFE,
   * killed the rest of the script, and left the page on its hard-coded
   * "connecting…" with both panels still `hidden`. That is the reported bug.
   *
   * When storage is unusable the token is held in memory for the session
   * instead. The device still works; it just has to be entered again next
   * launch, and the UI says so rather than pretending it saved. */
  var memoryToken = null;
  var storageWorks = null;

  function storageAvailable() {
    if (storageWorks !== null) { return storageWorks; }
    try {
      var probe = "cofferdam.probe";
      global.localStorage.setItem(probe, "1");
      global.localStorage.removeItem(probe);
      storageWorks = true;
    } catch (error) {
      storageWorks = false;
    }
    return storageWorks;
  }

  function readToken() {
    if (!storageAvailable()) { return memoryToken; }
    try {
      return global.localStorage.getItem(TOKEN_KEY);
    } catch (error) {
      storageWorks = false;
      return memoryToken;
    }
  }

  function writeToken(candidate) {
    memoryToken = candidate;
    if (!storageAvailable()) { return; }
    try {
      global.localStorage.setItem(TOKEN_KEY, candidate);
    } catch (error) {
      storageWorks = false;
    }
  }

  function clearStoredToken() {
    memoryToken = null;
    if (!storageAvailable()) { return; }
    try {
      global.localStorage.removeItem(TOKEN_KEY);
    } catch (error) {
      storageWorks = false;
    }
  }

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

  /* The single place a connection state is set. Each state carries its own
     header text, its own explanation, and whether retrying is the user's next
     move — so no code path can leave the UI saying one thing and meaning
     another. `detail` is the reason from the failure site; it is never a token
     and never a URL. */
  var CONN_TEXT = {};
  CONN_TEXT[CONN_CONNECTING] = ["", "connecting…"];
  CONN_TEXT[CONN_AUTH_REQUIRED] = ["down", "not connected"];
  CONN_TEXT[CONN_AUTH_REJECTED] = ["down", "token rejected"];
  CONN_TEXT[CONN_UNREACHABLE] = ["down", "unreachable"];
  CONN_TEXT[CONN_CONNECTED] = ["live", "live"];

  function setConnState(state, detail) {
    connState = state;
    var presentation = CONN_TEXT[state] || CONN_TEXT[CONN_UNREACHABLE];
    setConn(presentation[0], presentation[1]);

    var banner = el("connBanner");
    var retry = el("connRetry");
    if (!banner || !retry) { return; }

    // Retry is offered exactly where retrying is the sensible next action.
    // "Authentication required" is not one of them: the token form below is
    // already the action, and a Retry button beside it would just re-run a
    // request that must fail until a token exists.
    var retryable = state === CONN_UNREACHABLE;
    retry.hidden = !retryable;
    if (detail) {
      banner.textContent = detail;
      banner.hidden = false;
    } else {
      banner.hidden = true;
      banner.textContent = "";
    }
  }

  /* A bounded wrapper: whatever `work` is, it settles. Without this the boot
     had no upper bound at all — a fetch that never resolves (a Tailscale route
     that black-holes, a captive portal swallowing the connection) simply left
     the header on "connecting…" for as long as the page stayed open. */
  function withTimeout(work, milliseconds, label) {
    return new Promise(function (resolve, reject) {
      var settled = false;
      var timer = setTimeout(function () {
        if (settled) { return; }
        settled = true;
        reject(new Error(label));
      }, milliseconds);
      work.then(function (result) {
        if (settled) { return; }
        settled = true;
        clearTimeout(timer);
        resolve(result);
      }, function (error) {
        if (settled) { return; }
        settled = true;
        clearTimeout(timer);
        reject(error);
      });
    });
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

  /* -------------------------------------------------------------- media
   *
   * Cards for the media providers the service offers. The client's whole
   * vocabulary here is a provider id and a phrase: there is no URL, no
   * template, and no query-parameter name anywhere in this file, because the
   * service does not accept one. Anything a card shows comes from the
   * catalogue the API served.
   *
   * The copy is doing real work. "Open" opens an app or a page and nothing
   * more, so every card says so — a card that merely showed a play-looking
   * button would promise something no action in this build performs. A
   * provider whose search Cofferdam cannot build shows the service's reason
   * instead of a disabled box with no explanation.
   */

  var mediaProviders = [];
  var mediaLoaded = false;
  var mediaMaxQuery = 120;
  var mediaPending = {};

  function mediaProviderById(id) {
    var found = mediaProviders.filter(function (item) { return item.id === id; });
    return found.length === 1 ? found[0] : null;
  }

  function mediaCard(provider) {
    var badges = [];
    badges.push(provider.kind === "native_app"
      ? badge("installed app", "ok")
      : badge("opens in Opera"));
    if (!provider.available) { badges.push(badge("unavailable here", "warn")); }

    var canSearch = provider.supported_actions.indexOf("search") !== -1;
    var busy = mediaPending[provider.id] === true;
    var disabled = (!provider.available || busy) ? " disabled" : "";

    var body = '<div class="media-actions">' +
      '<button class="primary" data-media-open="' + escapeHtml(provider.id) + '"' + disabled + ">" +
      (busy ? "Opening…" : "Open") + "</button></div>";

    if (canSearch) {
      body += '<form class="media-search" data-media-search="' + escapeHtml(provider.id) + '">' +
        '<input type="search" name="query" inputmode="search" autocomplete="off" ' +
        'maxlength="' + mediaMaxQuery + '" placeholder="Search ' + escapeHtml(provider.name) + '" ' +
        'aria-label="Search ' + escapeHtml(provider.name) + '"' + disabled + ">" +
        '<button type="submit"' + disabled + ">" + (busy ? "…" : "Search") + "</button></form>";
    } else if (provider.search_unavailable_reason) {
      body += '<p class="media-note">' + escapeHtml(provider.search_unavailable_reason) + "</p>";
    }

    // Structured results (M2B3A.1). Offered only where the service reports the
    // capability as usable, which needs both an official adapter and
    // credentials on this host. Where it is not, one short line says so and
    // Open plus the search page stay exactly as they were — rather than an
    // enabled control that could only fail.
    body += mediaResultsBlock(provider, busy);

    // The host's own reason for an absence, never a guess made here.
    if (!provider.available && provider.unavailable_reason) {
      body += '<p class="media-note">' + escapeHtml(provider.unavailable_reason) + "</p>";
    }

    var limitations = (provider.limitations || []).map(function (line) {
      return "<li>" + escapeHtml(line) + "</li>";
    }).join("");

    return '<div class="media-card' + (provider.available ? "" : " off") + '">' +
      '<div class="media-head"><strong>' + escapeHtml(provider.name) + "</strong>" +
      badges.join("") + "</div>" + body +
      (limitations ? '<ul class="media-limits">' + limitations + "</ul>" : "") +
      "</div>";
  }

  /* ------------------------------------------------- structured results
   *
   * The client's whole vocabulary here is a provider id, a phrase, and the
   * opaque handles the server issued. There is no URL, no URI and no video id
   * anywhere in this file — the service accepts none of them, and the result
   * cards it sends contain none of them.
   */

  /* provider_id -> { state, query, searchId, results, expiresAt, error } */
  var mediaResults = {};

  function capability(provider, name) {
    return !!(provider.capabilities && provider.capabilities[name]);
  }

  function providerHasOfficialSearch(provider) {
    /* Whether an official adapter exists at all, independent of credentials.
       Netflix/Prime/TV+ report false, so they never show a "not configured"
       hint for a feature this build was never going to give them. */
    return provider.offers_structured_search === true;
  }

  function resultLine(result) {
    /* The distinctions that let someone tell four “Gönül Dağı” apart, built
       from bounded fields the server normalized. Every one is escaped. */
    var parts = [];
    if (result.creators && result.creators.length) { parts.push(result.creators.join(", ")); }
    if (result.subtitle && parts.indexOf(result.subtitle) === -1) { parts.push(result.subtitle); }
    if (result.published) { parts.push(result.published); }
    if (typeof result.duration_seconds === "number") {
      var mins = Math.floor(result.duration_seconds / 60);
      var secs = result.duration_seconds % 60;
      parts.push(mins + ":" + (secs < 10 ? "0" : "") + secs);
    }
    return parts.join(" · ");
  }

  var SPOTIFY_PROVIDER_ID = "spotify";

  function spotifyPlaybackReady() {
    return !!(global.CofferdamSpotify && global.CofferdamSpotify.connected());
  }

  /* Play now / Add to queue, and only where they mean something.
   *
   * Two conditions, both structural rather than cosmetic. The provider must be
   * Spotify, because these routes go through the Spotify player and a YouTube
   * result has no business near them. And the result must be a **track**: an
   * album, artist or playlist is a *context* in Spotify's model, which is a
   * different endpoint with different semantics that this milestone deliberately
   * does not implement. Rendering "Play now" on an artist card would be
   * inventing a behaviour nothing has verified — those cards keep Open, which
   * does exactly what it says.
   *
   * When no account is connected the buttons are shown and disabled rather than
   * hidden: the capability is real and one panel away, and a control that
   * silently does not exist is harder to understand than one that says why. */
  function spotifyResultActions(providerId, result, busy) {
    if (providerId !== SPOTIFY_PROVIDER_ID) { return ""; }
    if (result.result_type !== "track") { return ""; }
    var locked = busy || !spotifyPlaybackReady();
    var suffix = ' data-result-id="' + escapeHtml(result.result_id) + '"' +
      (locked ? " disabled" : "") + ">";
    return '<button class="mr-play primary" data-spotify-play="' + escapeHtml(providerId) + '"' +
        suffix + "Play now</button>" +
      '<button class="mr-queue" data-spotify-queue="' + escapeHtml(providerId) + '"' +
        suffix + "Add to queue</button>";
  }

  var YOUTUBE_PROVIDER_ID = "youtube";

  /* Play now / Add to queue on a verified YouTube video result.
   *
   * The same two structural conditions as the Spotify row above, for the same
   * reasons. The provider must be YouTube, because these routes go through the
   * dedicated player and a Spotify track has no business near them. And the
   * result must be a **video**: a channel or playlist card has no video id, so
   * rendering Play now on one would be offering a button whose only outcome is a
   * refusal.
   *
   * Unlike the Spotify row, these are *not* disabled when the player is closed.
   * Play now with no player open is the whole point — it opens one, waits for
   * it, and continues the same request — so disabling them until a player
   * existed would put the product's primary action behind a setup step it does
   * not have. */
  function youtubeResultActions(providerId, result, busy) {
    if (providerId !== YOUTUBE_PROVIDER_ID) { return ""; }
    if (result.result_type !== "video") { return ""; }
    if (!global.CofferdamYouTube) { return ""; }
    var suffix = ' data-result-id="' + escapeHtml(result.result_id) + '"' +
      (busy ? " disabled" : "") + ">";
    return '<button class="mr-play primary" data-youtube-play="' + escapeHtml(providerId) + '"' +
        suffix + "Play now</button>" +
      '<button class="mr-queue" data-youtube-queue="' + escapeHtml(providerId) + '"' +
        suffix + "Add to queue</button>";
  }

  function resultCard(providerId, result, busy) {
    var badges = [badge(result.result_type)];
    if (result.explicit === true) { badges.push(badge("explicit", "warn")); }
    if (result.live_state) { badges.push(badge(result.live_state, "warn")); }
    var line = resultLine(result);
    var openLabel = providerId === SPOTIFY_PROVIDER_ID
      ? "Open in Spotify"
      : (providerId === YOUTUBE_PROVIDER_ID ? "Open in YouTube" : "Open");
    /* A visible row rather than a three-dot menu. Play now is the thing someone
       came here to press, and burying it behind a tap-and-aim on a phone would
       make the primary action the hardest one to reach. The row wraps, so three
       buttons never push the card sideways. */
    return '<li class="mr-item">' +
      '<div class="mr-title"><strong>' + escapeHtml(result.title) + "</strong>" +
      badges.join("") + "</div>" +
      (line ? '<div class="mr-meta">' + escapeHtml(line) + "</div>" : "") +
      '<div class="mr-actions">' +
      spotifyResultActions(providerId, result, busy) +
      youtubeResultActions(providerId, result, busy) +
      '<button class="mr-open" data-open-result="' + escapeHtml(providerId) + '" ' +
      'data-result-id="' + escapeHtml(result.result_id) + '"' + (busy ? " disabled" : "") +
      ">" + openLabel + "</button></div></li>";
  }

  function mediaResultsBlock(provider, busy) {
    if (!capability(provider, "structured_search")) {
      // Specific about which half is missing: a provider may be perfectly
      // launchable while its official search is simply not set up.
      if (providerHasOfficialSearch(provider) && !provider.structured_search_configured) {
        return '<p class="media-note">Structured results not configured — ' +
          "Open and Search still work.</p>";
      }
      return "";
    }

    var state = mediaResults[provider.id] || {};
    var pending = state.state === "searching";
    var locked = pending || busy;

    var html = '<form class="mr-form" data-find-results="' + escapeHtml(provider.id) + '">' +
      '<input type="search" name="query" inputmode="search" autocomplete="off" maxlength="' +
      mediaMaxQuery + '" placeholder="Find on ' + escapeHtml(provider.name) + '" ' +
      'aria-label="Find results on ' + escapeHtml(provider.name) + '"' +
      (locked ? " disabled" : "") + ' value="' + escapeHtml(state.query || "") + '">' +
      '<button type="submit"' + (locked ? " disabled" : "") + ">" +
      (pending ? "Finding…" : "Find") + "</button></form>";

    if (state.state === "searching") {
      html += '<p class="media-note">Searching ' + escapeHtml(provider.name) + "…</p>";
    } else if (state.state === "error") {
      html += '<p class="media-note err">' +
        escapeHtml(state.error || "That search did not work.") +
        ' <button class="mr-retry" data-retry-results="' + escapeHtml(provider.id) +
        '">Try again</button></p>';
    } else if (state.state === "empty") {
      html += '<p class="media-note">No results for “' + escapeHtml(state.query) + "”. " +
        "Try different words.</p>";
    } else if (state.state === "expired") {
      html += '<p class="media-note">That result list has expired. ' +
        '<button class="mr-retry" data-retry-results="' + escapeHtml(provider.id) +
        '">Search again</button></p>';
    } else if (state.state === "ok" && state.results && state.results.length) {
      html += '<p class="media-note">Results for “' + escapeHtml(state.query) + "”. " +
        "Nothing is playing — pick one to open it.</p>";
      html += '<ul class="mr-list">' + state.results.map(function (result) {
        return resultCard(provider.id, result, busy);
      }).join("") + "</ul>";
      if (provider.id === SPOTIFY_PROVIDER_ID && !spotifyPlaybackReady() &&
          state.results.some(function (result) { return result.result_type === "track"; })) {
        // Why the two buttons above are disabled, and where to fix it. Without
        // this the row reads as broken rather than as one setup step away.
        html += '<p class="media-note">Connect your Spotify account in the ' +
          "<strong>Spotify player</strong> panel above to play or queue a track from here. " +
          "Open in Spotify works either way.</p>";
      }
      if (capability(provider, "open_first_result")) {
        // Explicit, never automatic: a provider's ranking is an opinion, and
        // acting on it unasked is how the wrong song opens.
        html += '<button class="mr-first" data-open-first="' + escapeHtml(provider.id) + '"' +
          (busy ? " disabled" : "") + ">Open first result</button>";
      }
    }
    return '<div class="mr-block">' + html + "</div>";
  }

  function setResultState(providerId, next) {
    mediaResults[providerId] = Object.assign({}, mediaResults[providerId] || {}, next);
    renderMedia();
  }

  function findResults(providerId, query) {
    var provider = mediaProviderById(providerId);
    if (!provider || !capability(provider, "structured_search")) { return; }
    var current = mediaResults[providerId] || {};
    if (current.state === "searching") { return; }   // no double submission
    setResultState(providerId, { state: "searching", query: query, results: [], error: null });

    api("/api/media/providers/" + encodeURIComponent(providerId) + "/results/search",
        { body: { query: query } })
      .then(function (response) {
        var record = response.payload;
        if (record && record.action_id) { upsertAction(record); }
        if (!response.ok) {
          var failure = (record && record.error) || {};
          setResultState(providerId, {
            state: "error",
            error: failure.message || "That search did not work."
          });
          return;
        }
        var result = (record && record.result) || {};
        var list = result.results || [];
        setResultState(providerId, {
          state: list.length ? "ok" : "empty",
          searchId: result.search_id,
          results: list,
          expiresAt: result.expires_at,
          error: null
        });
      })
      .catch(function (error) {
        if (error.message === "unauthorized") { return; }
        setResultState(providerId, { state: "error", error: "Request failed." });
      });
  }

  function openResult(providerId, resultId, useFirst) {
    var state = mediaResults[providerId] || {};
    if (!state.searchId) { return; }
    if (useFirst && !(state.results && state.results.length)) {
      // Zero results has no first result. The button is not rendered in that
      // state; this guards the state changing under an already-queued tap.
      toast("There are no results to open.", "err");
      return;
    }
    if (mediaPending[providerId]) { return; }
    mediaPending[providerId] = true;
    renderMedia();

    // Only handles the server issued travel here — never a URL or a URI. The
    // provider id is sent as an assertion of which provider this card belongs
    // to, and the server refuses if it disagrees with the search session; that
    // mismatch check is what stops a result being opened through the wrong
    // provider's adapter.
    var path = "/api/media/searches/" + encodeURIComponent(state.searchId) +
      "/results/" + encodeURIComponent(useFirst ? "first" : resultId) + "/open";

    api(path, { body: { provider_id: providerId } }).then(function (response) {
      var record = response.payload;
      if (record && record.action_id) { upsertAction(record); }
      if (response.ok) {
        toast((record.result && record.result.note) || "Opened.", "ok");
        return;
      }
      var failure = (record && record.error) || {};
      if (failure.code === "media_search_expired" || failure.code === "media_search_not_found") {
        setResultState(providerId, { state: "expired", results: [], searchId: null });
        return;
      }
      toast(failure.message || "That did not open.", "err");
    }).catch(function (error) {
      if (error.message !== "unauthorized") { toast("Request failed.", "err"); }
    }).then(function () {
      mediaPending[providerId] = false;
      renderMedia();
    });
  }

  /* Play now / Add to queue on a verified Spotify track result.
   *
   * The only two things sent are the search id the server issued and the result
   * id it issued — never a Spotify URI, never a track id, never a device. The
   * server rebuilds the URI from the session it privately remembers, so there is
   * no field here for a client to abuse.
   *
   * The outcome is repeated verbatim. `spotify.js` has already reduced the
   * server's observed state to a message; this never upgrades it, which is what
   * keeps "added to the queue" from being read as "now playing". */
  function spotifyResultAction(providerId, resultId, verb) {
    var state = mediaResults[providerId] || {};
    if (!state.searchId || !resultId) { return; }
    if (mediaPending[providerId]) { return; }
    if (!global.CofferdamSpotify) { return; }

    mediaPending[providerId] = true;
    renderMedia();

    var call = verb === "queue"
      ? global.CofferdamSpotify.queueResult(state.searchId, resultId)
      : global.CofferdamSpotify.playResult(state.searchId, resultId);

    call.then(function (outcome) {
      if (!outcome) { return; }
      toast(outcome.message || "Done.", outcome.ok && outcome.outcome !== "not_applied"
        ? "ok" : "err");
    }).catch(function (error) {
      if (error && error.message !== "unauthorized") { toast("Request failed.", "err"); }
    }).then(function () {
      mediaPending[providerId] = false;
      renderMedia();
    });
  }

  /* Play now / Add to queue on a verified YouTube video result.
   *
   * The two things sent are the search id the server issued and the result id it
   * issued — never a YouTube URL, never a video id, never a player command. The
   * server resolves the video from the session it privately remembers, so there
   * is no field here for a client to abuse.
   *
   * Routed through `youtube.js` so the player panel and this card share one
   * pending state and one account of what happened, and so the outcome is
   * repeated verbatim rather than upgraded — which is what keeps "added to the
   * queue" from being read as "now playing", and "autoplay blocked" from being
   * read as "playing". */
  function youtubeResultAction(providerId, resultId, verb) {
    var state = mediaResults[providerId] || {};
    if (!state.searchId || !resultId) { return; }
    if (mediaPending[providerId]) { return; }   /* no double submission */
    if (!global.CofferdamYouTube) { return; }

    mediaPending[providerId] = true;
    renderMedia();

    var call = verb === "queue"
      ? global.CofferdamYouTube.queueResult(state.searchId, resultId)
      : global.CofferdamYouTube.playResult(state.searchId, resultId);

    call.then(function (outcome) {
      if (!outcome) { return; }
      // The server's own sentence, and its own verdict on whether the thing
      // happened. `applied` and `queued` are the only successes; blocked and
      // partial are warnings that say what to do next.
      var good = outcome.outcome === "applied" || outcome.outcome === "queued";
      toast(outcome.note || "Done.", good ? "ok" : "err");
    }).catch(function (error) {
      if (error && error.message !== "unauthorized") { toast("Request failed.", "err"); }
    }).then(function () {
      mediaPending[providerId] = false;
      renderMedia();
    });
  }

  function renderMedia() {
    var container = el("mediaCards");
    if (!container) { return; }
    if (!mediaLoaded) {
      container.innerHTML = '<p class="muted">Loading…</p>';
      return;
    }
    if (!mediaProviders.length) {
      container.innerHTML = '<p class="muted">No media providers are available from this service.</p>';
      return;
    }
    container.innerHTML = mediaProviders.map(mediaCard).join("");
  }

  function loadMedia() {
    return api("/api/media/providers").then(function (response) {
      if (!response.ok) { throw new Error("media unavailable"); }
      var payload = response.payload || {};
      mediaProviders = payload.providers || [];
      if (typeof payload.max_query_length === "number") {
        mediaMaxQuery = payload.max_query_length;
      }
      mediaLoaded = true;
      renderMedia();
    }).catch(function (error) {
      if (error.message === "unauthorized") { return; }
      mediaLoaded = true;
      mediaProviders = [];
      var container = el("mediaCards");
      if (container) {
        container.innerHTML = '<p class="muted">The media catalogue could not be loaded from the service.</p>';
      }
    });
  }

  /* One in-flight action per provider. The flag is set before the request and
     cleared in every exit path, so a double tap on a phone cannot open two
     windows and a failure cannot leave a card stuck on "Opening…". */
  function runMediaAction(providerId, path, body) {
    if (mediaPending[providerId]) { return; }
    var provider = mediaProviderById(providerId);
    if (!provider || !provider.available) { return; }
    mediaPending[providerId] = true;
    renderMedia();

    api(path, { body: body }).then(function (response) {
      var record = response.payload;
      if (record && record.action_id) { upsertAction(record); }
      if (response.ok) {
        // Deliberately not "playing": the result says what actually happened,
        // and the toast repeats it rather than upgrading it.
        var note = (record.result && record.result.note) || "Opened.";
        toast(note, "ok");
      } else {
        var error = (record && record.error) || {};
        toast(error.message || "That did not work.", "err");
      }
    }).catch(function (error) {
      if (error.message !== "unauthorized") { toast("Request failed.", "err"); }
    }).then(function () {
      mediaPending[providerId] = false;
      renderMedia();
    });
  }

  var mediaCardsContainer = el("mediaCards");
  if (mediaCardsContainer) {
    // Delegated, because the cards are re-rendered on every state change.
    mediaCardsContainer.addEventListener("click", function (event) {
      // Structured-result controls (M2B3A.1), checked before the plain Open.
      var openResultButton = event.target.closest("[data-open-result]");
      if (openResultButton) {
        openResult(
          openResultButton.getAttribute("data-open-result"),
          openResultButton.getAttribute("data-result-id"),
          false
        );
        return;
      }
      // Spotify playback on a verified track result (M2D). Checked before the
      // plain Open, and routed through spotify.js so the player panel and this
      // card share one pending state and one account of what happened.
      var playButton = event.target.closest("[data-spotify-play]");
      if (playButton) {
        spotifyResultAction(
          playButton.getAttribute("data-spotify-play"),
          playButton.getAttribute("data-result-id"),
          "play"
        );
        return;
      }
      var queueButton = event.target.closest("[data-spotify-queue]");
      if (queueButton) {
        spotifyResultAction(
          queueButton.getAttribute("data-spotify-queue"),
          queueButton.getAttribute("data-result-id"),
          "queue"
        );
        return;
      }
      // YouTube playback on a verified video result (M2E). Checked before the
      // plain Open for the same reason the Spotify pair is: Play now is what
      // someone came here to press, and Open in YouTube stays available beside
      // it as the explicit "give me the normal watch page" action.
      var youtubePlayButton = event.target.closest("[data-youtube-play]");
      if (youtubePlayButton) {
        youtubeResultAction(
          youtubePlayButton.getAttribute("data-youtube-play"),
          youtubePlayButton.getAttribute("data-result-id"),
          "play"
        );
        return;
      }
      var youtubeQueueButton = event.target.closest("[data-youtube-queue]");
      if (youtubeQueueButton) {
        youtubeResultAction(
          youtubeQueueButton.getAttribute("data-youtube-queue"),
          youtubeQueueButton.getAttribute("data-result-id"),
          "queue"
        );
        return;
      }
      var firstButton = event.target.closest("[data-open-first]");
      if (firstButton) {
        openResult(firstButton.getAttribute("data-open-first"), null, true);
        return;
      }
      var retryButton = event.target.closest("[data-retry-results]");
      if (retryButton) {
        var retryProvider = retryButton.getAttribute("data-retry-results");
        var previous = (mediaResults[retryProvider] || {}).query;
        // Back to a clean input, keeping the words so the user need not retype
        // them. An expired list is cleared: showing stale cards beside "expired"
        // invites a tap that can only fail.
        setResultState(retryProvider, {
          state: null, results: [], searchId: null, error: null, query: previous || ""
        });
        return;
      }

      var button = event.target.closest("[data-media-open]");
      if (!button) { return; }
      runMediaAction(button.getAttribute("data-media-open"), "/api/actions/open-media-provider", {
        provider_id: button.getAttribute("data-media-open")
      });
    });

    mediaCardsContainer.addEventListener("submit", function (event) {
      // Structured search first: both forms live in the same card, so the
      // specific one has to be matched before the search-page fallback.
      var findForm = event.target.closest("[data-find-results]");
      if (findForm) {
        event.preventDefault();
        var findProvider = findForm.getAttribute("data-find-results");
        var phrase = (findForm.query.value || "").trim();
        if (!phrase) { toast("Type something to find.", "err"); return; }
        if (phrase.length > mediaMaxQuery) {
          toast("That search is too long (max " + mediaMaxQuery + " characters).", "err");
          return;
        }
        findResults(findProvider, phrase);
        return;
      }

      var form = event.target.closest("[data-media-search]");
      if (!form) { return; }
      event.preventDefault();
      var providerId = form.getAttribute("data-media-search");
      var query = (form.query.value || "").trim();
      if (!query) {
        toast("Type something to search for.", "err");
        return;
      }
      if (query.length > mediaMaxQuery) {
        // Checked here too so the phone explains it immediately; the service
        // enforces the same bound regardless of what this page does.
        toast("That search is too long (max " + mediaMaxQuery + " characters).", "err");
        return;
      }
      runMediaAction(providerId, "/api/actions/search-media-provider", {
        provider_id: providerId,
        query: query
      });
    });
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
      } else if (record.params && record.params.provider_id) {
        // Media actions. The provider and the words typed are what makes the
        // entry readable; the address they were turned into is the service's
        // business and is not shown here.
        detail = record.params.provider_id +
          (record.params.query ? ": " + record.params.query : "");
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
    if (!token) { setConnState(CONN_AUTH_REQUIRED, null); return; }
    // Scheme follows the page, so an https page never opens a plaintext socket
    // and an http page never asks for a TLS one the server does not speak.
    var scheme = location.protocol === "https:" ? "wss:" : "ws:";
    // Only the first attempt is allowed to show "connecting…". A background
    // retry must not reset an established failure to an optimistic state: with
    // a socket that hangs rather than refusing, that flip-flop reproduced the
    // original bug exactly — a header stuck on "connecting…" forever, this time
    // by a loop rather than by an exception.
    socketAttempts += 1;
    if (socketAttempts === 1) { setConnState(CONN_CONNECTING, null); }
    try {
      // The token rides in the subprotocol, never the URL — a WebSocket URL
      // lands in history, proxy logs and crash reports.
      socket = new WebSocket(scheme + "//" + location.host + "/ws", [SUBPROTOCOL, token]);
    } catch (error) {
      setConnState(CONN_UNREACHABLE, "The live connection could not be opened by this browser.");
      return;
    }

    // A socket that never opens and never closes is the other way to hang
    // forever: `onerror` does not always fire on iOS when the peer is
    // unreachable, so the wait is bounded here rather than left to the browser.
    clearTimeout(socketTimer);
    socketTimer = setTimeout(function () {
      if (socket && socket.readyState === WebSocket.CONNECTING) {
        setConnState(CONN_UNREACHABLE,
          "The live connection timed out. The workstation may be asleep or off the network.");
        try { socket.close(); } catch (error) { /* already gone */ }
      }
    }, SOCKET_OPEN_TIMEOUT_MS);

    socket.onopen = function () {
      clearTimeout(socketTimer);
      reconnectDelay = 1000;
      socketAttempts = 0;
      setConnState(CONN_CONNECTED, null);
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
      clearTimeout(socketTimer);
      socket = null;
      // 4401 is the service closing before upgrade because the token did not
      // match. That is authentication, not connectivity, and retrying it
      // forever would be a lie dressed as persistence.
      if (event.code === 4401) { forgetToken("Token rejected — enter it again."); return; }
      setConnState(CONN_UNREACHABLE,
        "Lost the live connection to the workstation. Retrying…");
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    };

    socket.onerror = function () {
      // Deliberately not a terminal state: `onerror` is always followed by
      // `onclose`, which is where the real reason (including 4401) arrives.
      if (connState === CONN_CONNECTED) { setConnState(CONN_CONNECTING, null); }
    };
  }

  /* ------------------------------------------------------------ token flow */

  function forgetToken(message) {
    clearStoredToken();
    token = null;
    if (socket) { socket.close(); socket = null; }
    // Drop the machine's registry contents with the token: they describe the
    // user's devices and displays and should not survive a sign-out.
    registryData = {};
    registriesLoaded = false;
    availableApplications = [];
    // The media catalogue says which services this machine can reach; it goes
    // with the token like everything else that describes the host.
    mediaProviders = [];
    mediaLoaded = false;
    mediaPending = {};
    // Search results say what someone was looking for. They go with the token,
    // like everything else here that describes the user or the host.
    mediaResults = {};
    if (el("mediaCards")) { el("mediaCards").innerHTML = '<p class="muted">Loading…</p>'; }
    // The live inventory is the same kind of thing, only more so: it lists this
    // machine's displays and running applications. It goes with the token, and
    // its polling stops so a signed-out device makes no further requests.
    if (global.CofferdamLive) { global.CofferdamLive.stop(); }
    // Audio state is host state: which speakers this machine has and how loud
    // they are. It goes with the token, and its polling stops so a signed-out
    // device makes no further requests.
    if (global.CofferdamAudio) { global.CofferdamAudio.stop(); }
    // Spotify player state is the most personal thing on this page: which
    // account is connected, what is playing, and which speakers someone owns.
    // It goes with the token, and its polling stops so a signed-out device
    // makes no further requests against the account.
    if (global.CofferdamSpotify) { global.CofferdamSpotify.stop(); }
    // The YouTube player is the same kind of thing, and just as personal: what
    // somebody is watching and what they lined up to watch next. It goes with
    // the token, and its polling stops so a signed-out device makes no further
    // requests.
    if (global.CofferdamYouTube) { global.CofferdamYouTube.stop(); }
    el("registrySections").innerHTML = '<p class="muted">Loading…</p>';
    el("app").hidden = true;
    el("setup").hidden = false;
    setConnState(message ? CONN_AUTH_REJECTED : CONN_AUTH_REQUIRED, null);
    if (message) {
      el("setupError").textContent = message;
      el("setupError").hidden = false;
    }
    // A device whose browser refuses local storage can still be used; it just
    // cannot remember. Saying so beats silently forgetting on next launch.
    var warning = el("storageWarning");
    if (warning) {
      warning.hidden = storageAvailable();
      warning.textContent = storageAvailable() ? "" :
        "This browser is blocking local storage, so the token will be kept for " +
        "this session only and asked for again next time. Turning off Private " +
        "Browsing, or allowing cookies for this site, lets it be remembered.";
    }
  }

  function start(candidate) {
    token = candidate;
    setConnState(CONN_CONNECTING, null);
    // The token is validated against the real API before it is stored, so a
    // typo never becomes a persisted setting — and the request is bounded, so
    // an unreachable host reports "unreachable" instead of hanging.
    return withTimeout(api("/api/status"), BOOTSTRAP_TIMEOUT_MS, "timeout")
      .then(function (response) {
      if (!response.ok) { throw new Error("status failed"); }
      writeToken(candidate);
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
      // Same for the media catalogue: a service that cannot serve it still has
      // a fully working control panel above.
      loadMedia();
      // Same for the live inventory: it is a separate view of a separate
      // layer, and a discovery failure must not take the control panel down.
      if (global.CofferdamLive) {
        global.CofferdamLive.mount({ api: api, escapeHtml: escapeHtml, el: el })
          .catch(function () { /* live.js renders its own failure state */ });
      }
      // Same for audio: a host whose audio server cannot be read still has a
      // fully working control panel above, and audio.js renders that state
      // itself rather than throwing into this chain.
      if (global.CofferdamAudio) {
        global.CofferdamAudio.mount({ api: api, escapeHtml: escapeHtml, el: el })
          .catch(function () { /* audio.js renders its own failure state */ });
      }
      // Same for the Spotify player: an account that is not connected, or a
      // provider that cannot be reached, is a state spotify.js renders itself.
      // It must never take the control panel down with it.
      if (global.CofferdamSpotify) {
        global.CofferdamSpotify.mount({ api: api, escapeHtml: escapeHtml, el: el })
          .then(function () { renderMedia(); })
          .catch(function () { /* spotify.js renders its own failure state */ });
      }
      // Same again for the YouTube player: a host with no browser to open one
      // in, or a player that is simply not open yet, is a state youtube.js
      // renders itself. It must never take the control panel down with it.
      if (global.CofferdamYouTube) {
        global.CofferdamYouTube.mount({ api: api, escapeHtml: escapeHtml, el: el })
          .then(function () { renderMedia(); })
          .catch(function () { /* youtube.js renders its own failure state */ });
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

  el("connRetry").addEventListener("click", function () {
    var candidate = token || readToken();
    if (candidate) {
      start(candidate).catch(function (error) { bootFailed(error); });
    } else {
      forgetToken(null);
    }
  });

  /* Any unexpected throw during boot lands here instead of escaping the module
     and freezing the page on its initial markup. Whatever went wrong, the user
     gets a state and a way forward — which is the actual requirement,
     independent of which cause produced it. */
  function bootFailed(error) {
    var reason = error && error.message === "timeout"
      ? "The workstation did not answer in time. It may be asleep, or this device may not be on the tailnet yet."
      : "Could not reach the workstation from this device.";
    setConnState(CONN_UNREACHABLE, reason);
    el("setup").hidden = false;
    el("app").hidden = true;
  }

  function boot() {
    var stored = readToken();
    if (stored) {
      start(stored).catch(function (error) {
        // A stored token that the service refuses is an authentication answer;
        // anything else is a reachability answer. Conflating them is what sent
        // a working device to the token form after a network blip.
        if (error && error.message === "unauthorized") { return; }
        if (error && error.message === "status failed") {
          forgetToken("Stored token was rejected.");
          return;
        }
        bootFailed(error);
      });
    } else {
      // Fresh device: no token, so the honest state is "authentication
      // required" and the token form — never an indefinite "connecting…".
      forgetToken(null);
    }
  }

  try {
    boot();
  } catch (error) {
    bootFailed(error);
  }

  /* Last resort. If anything above this line throws in a browser we have not
     seen, the page must still say something true rather than sit on the
     hard-coded "connecting…" it was served with. */
  global.addEventListener("error", function () {
    if (connState === CONN_CONNECTING && el("app").hidden && el("setup").hidden) {
      bootFailed(new Error("script error"));
    }
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () { /* installability is optional */ });
  }
})(window);
