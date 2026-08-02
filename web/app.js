/* Cofferdam workstation PWA (M1).
 *
 * Talks only to this origin. The device token lives in localStorage on the
 * phone and is sent as a Bearer header for API calls, and as a WebSocket
 * subprotocol for the event channel (so it never lands in a URL or access log).
 */
(function () {
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
    var select = el("appSelect");
    var previous = select.value;
    select.innerHTML = apps.length
      ? apps.map(function (name) { return '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + "</option>"; }).join("")
      : '<option value="">no applications detected</option>';
    if (previous && apps.indexOf(previous) !== -1) { select.value = previous; }
    el("btnOpenApp").disabled = apps.length === 0;

    var capabilities = host.capabilities || {};
    el("btnScreenshot").disabled = capabilities.screenshot === false;
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

  el("urlForm").addEventListener("submit", function (event) {
    event.preventDefault();
    var url = el("urlInput").value.trim();
    if (!url) { return; }
    runAction("/api/actions/open-url", { url: url }, el("btnOpenUrl"));
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
})();
