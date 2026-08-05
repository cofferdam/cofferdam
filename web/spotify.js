/* Cofferdam — the "Spotify player" area (M2D Spotify playback with user OAuth).
 *
 * Its own file, beside audio.js and live.js, and the separation is the point.
 * audio.js changes *this computer's* speaker: PipeWire, one system volume, one
 * physical output. This file changes *a Spotify account's own player*: a Connect
 * device that might be a phone in another room, with its own volume that has
 * nothing to do with the laptop's. Two sliders both labelled "volume" is a trap,
 * so the product keeps them in two panels with two headings and the code keeps
 * them in two files.
 *
 * The rules this panel is written to, all inherited and all earned:
 *
 *   1. **Nothing is claimed until the server has been observed.** Every player
 *      write on the backend acts, re-reads Spotify's state, and reports what it
 *      *saw*. This file renders that report and never upgrades it. A play button
 *      that flipped to "playing" because it was tapped would be describing the
 *      tap, not the speaker.
 *   2. **One action at a time, bounded.** Controls disable while a request is in
 *      flight and a timer gives the panel back if the request never answers.
 *   3. **The phone cannot finish authorization, so it says so.** The redirect is
 *      a loopback URI; `127.0.0.1` on a phone is the phone. The button opens the
 *      page in Opera on the workstation and the text names that in as many
 *      words, rather than leaving someone waiting for a tab that will not come.
 *   4. **Nothing is logged.** There is no `console` call in this file. What is
 *      playing is a fact about somebody's evening, and a browser console is a
 *      surface neither of us controls.
 */
(function (global) {
  "use strict";

  var deps = null;

  /* Conservative on purpose. Every poll is a call against an account with a
     rolling 30-second rate limit, and playback state is not something a phone
     on a desk needs at animation rates. The progress bar is therefore honest
     rather than smooth: it shows the position at the last observation and says
     when that was. Refresh covers "now". */
  var POLL_MS = 15000;

  /* While an authorization attempt is live the user is actively clicking things
     in another window, so this is the one moment where a faster poll buys
     something: the panel flips to connected on its own. It is bounded by the
     attempt itself, which the server expires. */
  var AUTH_POLL_MS = 3000;

  /* An action that has not answered by this point has failed as far as the user
     is concerned. Without this bound a dropped connection leaves the panel
     permanently disabled — the failure mode that makes people reload and press
     the button twice. */
  var ACTION_TIMEOUT_MS = 12000;

  var snapshot = null;
  var loadError = null;
  var timer = null;
  var timerInterval = null;
  var pending = null;       /* which control is busy: a string key, or null */
  var pendingTimer = null;
  var actionError = null;   /* {message, detail} from the server, verbatim */
  var actionNote = null;    /* the observed outcome of the last action */
  var draftVolume = null;   /* slider position while dragging; never truth */
  var selectedDevice = null;/* the device handle chosen in the picker */
  var stopped = false;

  function esc(value) { return deps.escapeHtml(value); }

  /* ------------------------------------------------------------ reading state */

  function connection() {
    return (snapshot && snapshot.connection) || {};
  }

  function status() {
    return connection().status || null;
  }

  function authorization() {
    return (snapshot && snapshot.authorization) || {};
  }

  function capabilities() {
    return (snapshot && snapshot.capabilities) || {};
  }

  function devices() {
    return (snapshot && snapshot.devices) || [];
  }

  function activeDevice() {
    var wanted = snapshot ? snapshot.active_device_resource_id : null;
    if (!wanted) { return null; }
    var list = devices();
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].resource_id === wanted) { return list[i]; }
    }
    return null;
  }

  function isConnected() {
    return status() === "connected";
  }

  function authorizing() {
    return authorization().pending === true || status() === "authorization_pending";
  }

  /* ---------------------------------------------------------------- formatting */

  function clock(ms) {
    if (typeof ms !== "number" || ms < 0) { return null; }
    var total = Math.floor(ms / 1000);
    var mins = Math.floor(total / 60);
    var secs = total % 60;
    return mins + ":" + (secs < 10 ? "0" : "") + secs;
  }

  function deviceLabel(device) {
    /* The provider's own free-form type, tidied for reading. Not mapped through
       a table: an unrecognised device type should render as itself rather than
       as "unknown". */
    var parts = [];
    if (device.device_type) { parts.push(String(device.device_type).replace(/_/g, " ")); }
    if (typeof device.volume_percent === "number") { parts.push(device.volume_percent + "%"); }
    if (device.is_restricted) { parts.push("remote control not allowed"); }
    if (device.is_private_session) { parts.push("private session"); }
    return parts.join(" · ");
  }

  /* ------------------------------------------------------------------ account */

  function renderDisconnected() {
    return '<div class="sp-account">' +
      '<p class="sp-headline">' + esc("Spotify account not connected") + "</p>" +
      '<p class="muted">' +
        esc("Authorization happens once, in Opera on the workstation. It cannot be " +
            "completed on this phone: Spotify sends the answer back to a loopback " +
            "address that only exists on the workstation itself.") +
      "</p>" +
      '<p class="muted">' +
        esc("Controlling playback needs Spotify Premium. Searching the catalogue does not, " +
            "and keeps working either way.") +
      "</p>" +
      '<button id="spotifyAuthorize" class="primary">Authorize on workstation</button>' +
      "</div>";
  }

  function renderPending() {
    var remaining = authorization().expires_in_seconds;
    var countdown = typeof remaining === "number" && remaining > 0
      ? " " + esc("It expires in about " + remaining + "s.")
      : "";
    return '<div class="sp-account sp-pending">' +
      '<p class="sp-headline">' + esc("Complete authorization in Opera on the workstation.") +
      "</p>" +
      '<p class="muted">' +
        esc("Cofferdam opened the official Spotify page there. Sign in and approve, and this " +
            "panel will connect on its own.") + countdown +
      "</p>" +
      '<button id="spotifyCancelAuth" class="ghost">Cancel</button>' +
      "</div>";
  }

  function renderAccountProblem() {
    /* Every one of these is a distinct, named state from the server, and each
       one needs a different thing from the user. Collapsing them into "Spotify
       error" would send someone to fix the wrong thing. */
    var state = status();
    var detail = connection().detail || null;
    var headline;
    var advice;
    var offerAuthorize = false;

    if (state === "missing_required_scopes") {
      var missing = connection().missing_scopes || [];
      headline = "This Spotify authorization is missing permissions Cofferdam needs.";
      advice = missing.length
        ? "Reconnect and accept all of them: " + missing.join(", ")
        : "Reconnect and accept all of the permissions Spotify asks about.";
      offerAuthorize = true;
    } else if (state === "refresh_failed") {
      headline = "Spotify no longer accepts this authorization.";
      advice = "It may have been revoked from your Spotify account. Connect again to continue.";
      offerAuthorize = true;
    } else if (state === "premium_required") {
      headline = "Spotify playback control requires a Premium account.";
      advice = "Every Spotify player endpoint is Premium-only. Catalogue search still works, " +
        "and Open in Spotify still works.";
    } else if (state === "temporarily_unavailable") {
      headline = "Spotify could not be reached just now.";
      advice = "This is a network or provider problem, not a setting. Try Refresh in a moment.";
    } else {
      headline = "Spotify refused this request.";
      advice = "The two documented causes are an account without Premium, and an app in " +
        "development mode whose allowed-users list does not include this Spotify account.";
    }

    return '<div class="sp-account sp-problem">' +
      '<p class="sp-headline">' + esc(headline) + "</p>" +
      '<p class="muted">' + esc(advice) + "</p>" +
      (detail ? '<p class="muted">' + esc(detail) + "</p>" : "") +
      (offerAuthorize
        ? '<button id="spotifyAuthorize" class="primary">Authorize on workstation</button>'
        : "") +
      "</div>";
  }

  function renderConnectedHeader() {
    var name = connection().display_name;
    var busy = pending !== null;
    return '<div class="sp-account sp-connected">' +
      '<p class="sp-headline">' +
        esc(name ? "Connected as " + name : "Spotify account connected") + "</p>" +
      '<button id="spotifyDisconnect" class="ghost"' + (busy ? " disabled" : "") + ">" +
        esc("Disconnect") + "</button>" +
      "</div>";
  }

  function renderAuthOutcome() {
    /* The last thing an attempt did, when it did not simply succeed. A timed-out
       attempt that vanished with no explanation is how a person concludes the
       button is broken. */
    var last = authorization().last_outcome;
    if (!last || !last.message) { return ""; }
    if (last.state === "connected" && isConnected()) { return ""; }
    if (authorizing()) { return ""; }
    var tone = last.state === "connected" ? "ok" : "warn";
    return '<p class="sp-note ' + esc(tone) + '">' + esc(last.message) + "</p>";
  }

  /* ------------------------------------------------------------------- player */

  function renderNowPlaying() {
    var item = snapshot ? snapshot.now_playing : null;
    if (!snapshot || !snapshot.playback_available || !item) {
      return '<div class="sp-now"><div class="sp-now-title muted">' +
        esc("Nothing is playing on Spotify right now.") + "</div></div>";
    }

    var artists = (item.artists || []).join(", ");
    var meta = [];
    if (artists) { meta.push(artists); }
    if (item.album) { meta.push(item.album); }

    var position = clock(snapshot.progress_ms);
    var length = clock(item.duration_ms);
    var progress = "";
    if (position !== null && length !== null) {
      var percent = item.duration_ms > 0
        ? Math.max(0, Math.min(100, Math.round((snapshot.progress_ms / item.duration_ms) * 100)))
        : 0;
      progress =
        '<div class="sp-progress">' +
          '<div class="sp-progress-bar"><span style="width:' + esc(String(percent)) + '%"></span></div>' +
          '<div class="sp-progress-times muted">' +
            "<span>" + esc(position) + "</span><span>" + esc(length) + "</span>" +
          "</div>" +
        "</div>";
    }

    return '<div class="sp-now">' +
      '<div class="sp-now-title"><strong>' + esc(item.title || "—") + "</strong>" +
        (item.explicit === true ? '<span class="badge warn">explicit</span>' : "") + "</div>" +
      (meta.length ? '<div class="sp-now-meta muted">' + esc(meta.join(" · ")) + "</div>" : "") +
      progress +
      "</div>";
  }

  function renderTransport() {
    var can = capabilities().transport === true;
    var busy = pending !== null;
    var disabled = (!can || busy) ? " disabled" : "";
    var playing = snapshot && snapshot.is_playing === true;
    return '<div class="sp-transport">' +
      '<button id="spotifyPrevious" class="sp-btn"' + disabled +
        ' aria-label="previous track">‹‹</button>' +
      '<button id="spotifyPlayPause" class="sp-btn primary"' + disabled + ">" +
        esc(playing ? "Pause" : "Play") + "</button>" +
      '<button id="spotifyNext" class="sp-btn"' + disabled +
        ' aria-label="next track">››</button>' +
      "</div>";
  }

  function renderVolume() {
    var active = activeDevice();
    var can = capabilities().volume === true;
    var busy = pending !== null;

    if (!active) { return ""; }
    if (!can || active.supports_volume !== true) {
      /* Truthful and specific: Spotify documents `supports_volume`, and a device
         that reports false will refuse a volume change no matter how the button
         looks. Saying which device cannot do it beats a greyed-out slider. */
      return '<p class="sp-unavailable">' +
        esc("This Spotify device does not report volume control, so its level and mute " +
            "cannot be changed from here. Use the device's own controls.") + "</p>";
    }

    var volume = typeof active.volume_percent === "number" ? active.volume_percent : null;
    if (volume === null) {
      return '<p class="sp-unavailable">' +
        esc("This Spotify device did not report a volume level, so it cannot be changed " +
            "from here.") + "</p>";
    }

    var shown = draftVolume === null ? volume : draftVolume;
    var muted = snapshot && snapshot.muted_by_cofferdam === true;
    var rows = [
      '<div class="sp-volume">' +
        '<label class="sp-volume-label" for="spotifyVolume">Spotify volume</label>' +
        '<input type="range" id="spotifyVolume" min="0" max="100" step="1" ' +
          'value="' + esc(String(shown)) + '" ' +
          'aria-label="Spotify player volume percent" ' +
          'aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + esc(String(shown)) + '" ' +
          (busy ? "disabled " : "") + ">" +
        '<output class="sp-volume-value" for="spotifyVolume">' + esc(String(shown)) + "%</output>" +
      "</div>"
    ];
    if (draftVolume !== null && draftVolume !== volume) {
      rows.push('<p class="sp-pending muted">' +
        esc("Release to set " + draftVolume + "% — currently " + volume + "%.") + "</p>");
    }

    rows.push(
      '<div class="sp-mute-row">' +
        '<button id="spotifyMute" class="' + (muted ? "primary" : "") + '"' +
          (busy ? " disabled" : "") + ">" + esc(muted ? "Unmute Spotify" : "Mute Spotify") +
        "</button>" +
        (muted ? '<span class="sp-badge">Muted by Cofferdam</span>' : "") +
      "</div>" +
      /* Said in the panel, not only in the docs. Spotify publishes no mute
         operation; calling this a Spotify mute would be describing a feature
         that does not exist. */
      '<p class="sp-fineprint muted">' +
        esc("Spotify has no mute of its own, so Cofferdam mutes by setting the Spotify " +
            "volume to zero and remembering the level to restore. This does not touch " +
            "this computer's own volume — that is the Audio panel.") + "</p>"
    );
    return rows.join("");
  }

  function renderNoDevice() {
    return '<p class="sp-unavailable">' +
      esc("Spotify has no active device, so there is nowhere for it to play. Open Spotify on " +
          "this computer, your phone, or a speaker — then pick it below or press Refresh. " +
          "Nothing has been started.") + "</p>";
  }

  function renderDevices() {
    var list = devices();
    var busy = pending !== null;
    if (!snapshot || snapshot.devices_available !== true) {
      return '<p class="sp-unavailable">' +
        esc("Cofferdam could not read your Spotify Connect devices just now.") + "</p>";
    }
    if (!list.length) {
      return '<details class="sp-fold"><summary>Spotify Connect devices</summary>' +
        '<p class="sp-unavailable">' +
        esc("Spotify reports no devices. Open Spotify somewhere — a phone, this computer, " +
            "a speaker — and it will appear here.") + "</p></details>";
    }

    var current = snapshot.active_device_resource_id;
    var chosen = selectedDevice || current || "";
    var options = list.map(function (device) {
      var label = (device.name || "Unnamed device") +
        (device.resource_id === current ? " (active)" : "") +
        (device.is_restricted ? " — remote control not allowed" : "");
      return '<option value="' + esc(device.resource_id) + '"' +
        (device.resource_id === chosen ? " selected" : "") +
        (device.is_restricted ? " disabled" : "") + ">" + esc(label) + "</option>";
    });

    var rows = list.map(function (device) {
      return '<div class="sp-device' +
        (device.resource_id === current ? " current" : "") + '">' +
        '<div class="sp-device-name">' + esc(device.name || "Unnamed device") +
          (device.resource_id === current ? ' <span class="sp-badge">Active</span>' : "") +
        "</div>" +
        '<div class="sp-device-sub muted">' + esc(deviceLabel(device)) + "</div>" +
        "</div>";
    });

    return '<details class="sp-fold" id="spotifyDevicesFold" open>' +
      '<summary>Spotify Connect devices <span class="count">' +
        esc(String(list.length)) + "</span></summary>" +
      '<p class="muted">' +
        esc("Choosing a device changes where Spotify plays. It does not change this " +
            "computer's audio output — that is a separate control in the Audio panel.") +
      "</p>" +
      '<div class="sp-device-picker">' +
        '<select id="spotifyDevice" aria-label="Spotify Connect device"' +
          (busy ? " disabled" : "") + ">" + options.join("") + "</select>" +
        '<button id="spotifyTransfer" class="ghost"' + (busy ? " disabled" : "") + ">" +
          esc("Move playback here") + "</button>" +
      "</div>" +
      '<div class="sp-device-list">' + rows.join("") + "</div>" +
      "</details>";
  }

  function renderFeedback() {
    var blocks = [];
    if (actionError) {
      blocks.push('<p class="sp-error">' + esc(actionError.message) +
        (actionError.detail ? ' <span class="muted">' + esc(actionError.detail) + "</span>" : "") +
        "</p>");
    }
    if (actionNote) {
      blocks.push('<p class="sp-note ' + esc(actionNote.tone) + '">' +
        esc(actionNote.message) + "</p>");
    }
    return blocks.join("");
  }

  function renderWarnings() {
    var warnings = (snapshot && snapshot.warnings) || [];
    if (!warnings.length) { return ""; }
    return '<ul class="sp-warnings">' + warnings.map(function (text) {
      return "<li>" + esc(text) + "</li>";
    }).join("") + "</ul>";
  }

  function render() {
    var root = deps.el("spotifySections");
    if (!root) { return; }
    if (loadError) {
      root.innerHTML = '<p class="sp-unavailable">' + esc(loadError) + "</p>";
      return;
    }
    if (!snapshot) {
      root.innerHTML = '<p class="muted">Loading…</p>';
      return;
    }

    var html;
    if (authorizing()) {
      html = renderPending();
    } else if (status() === "disconnected") {
      html = renderDisconnected() + renderAuthOutcome();
    } else if (!isConnected()) {
      html = renderAccountProblem() + renderAuthOutcome();
    } else {
      html = renderConnectedHeader() +
        renderNowPlaying() +
        (activeDevice() ? renderTransport() + renderVolume() : renderNoDevice()) +
        renderFeedback() +
        renderDevices();
    }
    root.innerHTML = html + renderWarnings();

    var stamp = deps.el("spotifyObserved");
    if (stamp) {
      stamp.textContent = snapshot.observed_at
        ? "observed " + new Date(snapshot.observed_at).toLocaleTimeString() : "";
    }
    var refresh = deps.el("spotifyRefresh");
    if (refresh) { refresh.disabled = pending !== null; }
  }

  /* ---------------------------------------------------------------- loading */

  function load(force) {
    return deps.api("/api/spotify/playback" + (force ? "?refresh=true" : ""))
      .then(function (response) {
        if (!response.ok) {
          loadError = "Cofferdam could not read your Spotify player state.";
          snapshot = null;
        } else {
          loadError = null;
          snapshot = response.payload;
          /* A device handle is good for this session only, so a selection that
             no longer resolves is dropped rather than sent back. */
          if (selectedDevice) {
            var stillThere = devices().some(function (device) {
              return device.resource_id === selectedDevice;
            });
            if (!stillThere) { selectedDevice = null; }
          }
        }
        render();
        reschedule();
      }).catch(function (error) {
        if (error && error.message === "unauthorized") { return; }
        loadError = "Cofferdam could not reach the workstation to read your Spotify player.";
        snapshot = null;
        render();
      });
  }

  /* ---------------------------------------------------------------- actions */

  function beginPending(key) {
    if (pending !== null) { return false; }   /* rule 2: one action at a time */
    pending = key;
    actionError = null;
    actionNote = null;
    if (pendingTimer) { global.clearTimeout(pendingTimer); }
    pendingTimer = global.setTimeout(function () {
      if (pending !== null) {
        pending = null;
        pendingTimer = null;
        actionError = {
          message: "That did not finish in time, so Cofferdam cannot say whether it worked.",
          detail: "Refresh to see what Spotify is actually doing before trying again."
        };
        render();
      }
    }, ACTION_TIMEOUT_MS);
    render();
    return true;
  }

  function endPending() {
    pending = null;
    if (pendingTimer) { global.clearTimeout(pendingTimer); pendingTimer = null; }
  }

  function describeOutcome(result) {
    /* The server compared what it asked for against what it then observed. This
       only picks a tone; it never promotes an outcome. */
    var outcome = result && result.outcome;
    if (outcome === "applied") { return { tone: "ok", message: result.message }; }
    if (outcome === "accepted_by_provider") { return { tone: "ok", message: result.message }; }
    if (outcome === "partially_applied") { return { tone: "warn", message: result.message }; }
    return { tone: "warn", message: (result && result.message) || "That did not take effect." };
  }

  function failureOf(response) {
    var error = (response.payload && response.payload.error) || {};
    return {
      code: error.code || null,
      message: error.message || "That was refused.",
      detail: error.detail || null
    };
  }

  function send(key, path, options) {
    if (!beginPending(key)) { return Promise.resolve(null); }
    return deps.api(path, options).then(function (response) {
      endPending();
      if (!response.ok) {
        actionError = failureOf(response);
        actionNote = null;
        /* Refused actions still re-read: the commonest reason for a refusal is
           that the picture on the phone is out of date. */
        return load(true).then(function () { return null; });
      }
      actionError = null;
      actionNote = describeOutcome(response.payload);
      var payload = response.payload;
      /* The action response already carries a fresh snapshot the server took
         after acting, so this adopts it rather than making a second call
         against a rate-limited account. */
      if (payload && payload.playback) {
        snapshot = Object.assign({}, payload.playback, { authorization: authorization() });
        loadError = null;
      }
      render();
      reschedule();
      return payload;
    }).catch(function (error) {
      endPending();
      if (error && error.message === "unauthorized") { return null; }
      actionError = { message: "Cofferdam could not reach the workstation.", detail: null };
      render();
      return null;
    });
  }

  function transport(operation) {
    return send(operation, "/api/spotify/player/" + encodeURIComponent(operation), { body: {} });
  }

  function playPause() {
    return transport(snapshot && snapshot.is_playing === true ? "pause" : "resume");
  }

  function setVolume(value) {
    return send("volume", "/api/spotify/player/volume", {
      method: "PUT",
      body: { volume_percent: value }
    }).then(function (payload) { draftVolume = null; render(); return payload; });
  }

  function toggleMute() {
    var muted = snapshot && snapshot.muted_by_cofferdam === true;
    return send("mute", "/api/spotify/player/mute", { method: "PUT", body: { muted: !muted } });
  }

  function transfer() {
    var target = selectedDevice || (snapshot && snapshot.active_device_resource_id);
    if (!target) {
      actionError = { message: "Pick a Spotify device first.", detail: null };
      render();
      return Promise.resolve(null);
    }
    /* `play: false` is passed explicitly rather than left to a default: the
       documented semantics differ, and "move where Spotify plays" should not
       silently start something that was paused. */
    return send("transfer", "/api/spotify/player/device", {
      method: "PUT",
      body: { device_resource_id: target, play: false }
    });
  }

  /* ------------------------------------------------------- authorization flow */

  function startAuthorization() {
    if (!beginPending("authorize")) { return Promise.resolve(null); }
    return deps.api("/api/spotify/authorize", { body: {} }).then(function (response) {
      endPending();
      if (!response.ok) {
        actionError = failureOf(response);
        return load(true);
      }
      actionError = null;
      actionNote = {
        tone: "ok",
        message: (response.payload && response.payload.message) ||
          "Continue authorization in Opera on the workstation."
      };
      return load(true);
    }).catch(function (error) {
      endPending();
      if (error && error.message === "unauthorized") { return null; }
      actionError = { message: "Cofferdam could not reach the workstation.", detail: null };
      render();
      return null;
    });
  }

  function cancelAuthorization() {
    if (!beginPending("cancel-authorize")) { return Promise.resolve(null); }
    return deps.api("/api/spotify/authorize", { method: "DELETE" }).then(function () {
      endPending();
      actionError = null;
      actionNote = { tone: "warn", message: "Authorization cancelled. Nothing was changed." };
      return load(true);
    }).catch(function (error) {
      endPending();
      if (error && error.message === "unauthorized") { return null; }
      actionError = { message: "Cofferdam could not reach the workstation.", detail: null };
      render();
      return null;
    });
  }

  function disconnect() {
    return send("disconnect", "/api/spotify/disconnect", { body: {} }).then(function (payload) {
      /* Deliberately not "revoked". The API publishes no revocation endpoint for
         this flow, and the server's message says where the other half is done. */
      return load(true).then(function () { return payload; });
    });
  }

  /* ------------------------------------------------- search-result playback */

  /* Called by app.js when a Spotify *track* card's button is tapped. The client
     sends a search id and a result id and nothing else — no URI, no track id, no
     device id — and the server rebuilds the Spotify URI from the verified search
     session it privately remembers. */

  function resultAction(searchId, resultId, verb) {
    if (pending !== null) {
      /* Said plainly rather than reported as a failure: nothing was refused,
         the panel is simply still finishing the last thing it was asked to do. */
      return Promise.resolve({
        ok: false,
        code: null,
        outcome: null,
        message: "Cofferdam is still finishing the last Spotify action."
      });
    }
    var path = "/api/media/searches/" + encodeURIComponent(searchId) +
      "/results/" + encodeURIComponent(resultId) + "/spotify/" + verb;
    return send("result-" + verb, path, { body: {} }).then(function (payload) {
      if (payload) {
        return { ok: true, code: null, message: payload.message, outcome: payload.outcome };
      }
      return {
        ok: false,
        code: actionError ? actionError.code : null,
        message: (actionError && actionError.message) || "That did not work.",
        outcome: null
      };
    });
  }

  function playResult(searchId, resultId) {
    return resultAction(searchId, resultId, "play");
  }

  function queueResult(searchId, resultId) {
    return resultAction(searchId, resultId, "queue");
  }

  /* ---------------------------------------------------------------- polling */

  function visible() {
    /* A hidden tab is a phone in a pocket. Polling it spends an account's rate
       limit on nobody. */
    var doc = global.document;
    return !doc || doc.hidden !== true;
  }

  function wanted() {
    if (stopped) { return null; }
    return authorizing() ? AUTH_POLL_MS : POLL_MS;
  }

  function reschedule() {
    var interval = wanted();
    if (interval === timerInterval && timer) { return; }
    if (timer) { global.clearInterval(timer); timer = null; }
    timerInterval = interval;
    if (interval === null) { return; }
    timer = global.setInterval(function () {
      /* Never poll over an action in flight: a snapshot landing mid-request
         would redraw the panel underneath the user's finger. */
      if (pending === null && visible()) { load(false); }
    }, interval);
  }

  /* ----------------------------------------------------------------- wiring */

  function mount(dependencies) {
    deps = dependencies;
    stopped = false;

    var root = deps.el("spotifyPanel");
    if (root) {
      root.addEventListener("click", function (event) {
        var target = event.target;
        if (!target) { return; }
        switch (target.id) {
          case "spotifyRefresh": load(true); return;
          case "spotifyAuthorize": startAuthorization(); return;
          case "spotifyCancelAuth": cancelAuthorization(); return;
          case "spotifyDisconnect": disconnect(); return;
          case "spotifyPlayPause": playPause(); return;
          case "spotifyNext": transport("next"); return;
          case "spotifyPrevious": transport("previous"); return;
          case "spotifyMute": toggleMute(); return;
          case "spotifyTransfer": transfer(); return;
          default: return;
        }
      });

      /* `input` tracks the finger, `change` commits. Separating them keeps a
         drag from firing a request per pixel, and keeps the committed value
         distinct from the one under the thumb. */
      root.addEventListener("input", function (event) {
        if (event.target && event.target.id === "spotifyVolume") {
          var parsed = parseInt(event.target.value, 10);
          draftVolume = isNaN(parsed) ? null : Math.max(0, Math.min(100, parsed));
          var readout = root.querySelector ? root.querySelector(".sp-volume-value") : null;
          if (readout && draftVolume !== null) { readout.textContent = draftVolume + "%"; }
        }
      });

      root.addEventListener("change", function (event) {
        if (!event.target) { return; }
        if (event.target.id === "spotifyVolume") {
          var parsed = parseInt(event.target.value, 10);
          if (isNaN(parsed)) { draftVolume = null; render(); return; }
          setVolume(Math.max(0, Math.min(100, parsed)));
          return;
        }
        if (event.target.id === "spotifyDevice") {
          selectedDevice = event.target.value || null;
        }
      });
    }

    reschedule();
    return load(false);
  }

  function stop() {
    stopped = true;
    if (timer) { global.clearInterval(timer); timer = null; }
    timerInterval = null;
    endPending();
    /* What is playing, which account it is, and which speakers someone owns all
       go with the token. A signed-out device keeps none of it and makes no
       further requests. */
    snapshot = null;
    loadError = null;
    actionError = null;
    actionNote = null;
    draftVolume = null;
    selectedDevice = null;
    render();
  }

  global.CofferdamSpotify = {
    mount: mount,
    refresh: load,
    stop: stop,
    connected: isConnected,
    playResult: playResult,
    queueResult: queueResult
  };
})(window);
