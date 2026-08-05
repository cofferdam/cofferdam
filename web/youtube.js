/* Cofferdam — the "YouTube player" area (M2E YouTube dedicated player).
 *
 * Its own file, beside audio.js and spotify.js, and the separation is the whole
 * point of these three panels existing separately at all:
 *
 *   audio.js    changes *this computer's* speaker — PipeWire, one system volume,
 *               one physical output.
 *   spotify.js  changes *a Spotify account's own player* — a Connect device that
 *               might be a phone in another room.
 *   this file   changes *one browser tab on the workstation* — the YouTube
 *               player's own volume, which is a property of a video element and
 *               has nothing to do with either of the other two.
 *
 * Three sliders all labelled "volume" is a trap, so the product keeps them in
 * three panels with three headings and the code keeps them in three files.
 *
 * The rules this panel is written to, all inherited and all earned:
 *
 *   1. **Nothing is claimed until the player has been observed.** Every write on
 *      the backend acts, re-reads what the player *reports*, and answers with
 *      that. This file renders that report and never upgrades it. A play button
 *      that flipped to "playing" because it was tapped would be describing the
 *      tap, not the video.
 *   2. **One action at a time, bounded.** Controls disable while a request is in
 *      flight and a timer gives the panel back if the request never answers.
 *   3. **An older answer never wins.** Every request that can produce state
 *      carries a monotonic generation, and a response older than the newest one
 *      already applied is dropped. This is the protection the Spotify milestone
 *      earned in real validation — a poll issued *before* a write resolving
 *      *after* it, painting the old value back over the newly verified one — and
 *      it is inherited here rather than re-learned.
 *   4. **Nothing is logged.** There is no `console` call in this file. What
 *      somebody is watching is a fact about their evening, and a browser console
 *      is a surface neither of us controls.
 *   5. **The client names nothing.** The only things sent are handles the server
 *      issued: a search id, a result id, a queue item id. There is no URL, no
 *      video id and no player command anywhere in this file, because the service
 *      accepts none of them.
 */
(function (global) {
  "use strict";

  var deps = null;

  /* Conservative. Player state lives in the daemon's memory, so a poll is cheap
     — but it is still a request from a phone over a tailnet, and playback
     position is not something a phone on a desk needs at animation rates. The
     progress readout is therefore honest rather than smooth: it shows the
     position at the last observation. Refresh covers "now". */
  var POLL_MS = 10000;

  /* An action that has not answered by this point has failed as far as the user
     is concerned. Without this bound a dropped connection leaves the panel
     permanently disabled — the failure mode that makes people reload and press
     the button twice. */
  var ACTION_TIMEOUT_MS = 15000;

  /* Opening the player is the slow one: launch Opera, wait for the tab, wait for
     the official API script, load the video, confirm it. The server's own
     registration window is 24s, so this has to outlast it or the phone would
     give up on an operation that was about to succeed. */
  var OPEN_TIMEOUT_MS = 60000;

  /* While a write is in flight the panel polls the activity route instead of the
     state route. It costs the server one in-memory read and touches neither the
     player nor the network, so watching a slow launch cannot slow it down. */
  var ACTIVITY_POLL_MS = 700;

  var snapshot = null;
  var loadError = null;
  var timer = null;
  var timerInterval = null;
  var pending = null;       /* which control is busy: a string key, or null */
  var pendingTimer = null;
  var actionError = null;   /* {message, detail, code} from the server, verbatim */
  var actionNote = null;    /* the observed outcome of the last action */
  var draftVolume = null;   /* slider position while dragging; never truth */
  var queueOpen = false;
  var stopped = false;

  /* Response ordering. `refreshGeneration` is stamped on a request when it is
     issued; `appliedGeneration` is the newest one whose payload has been
     adopted. A response arriving with an older stamp is discarded — it is a
     description of a world that has since moved. */
  var refreshGeneration = 0;
  var appliedGeneration = 0;
  var inflightRefresh = null;

  var activity = null;
  var activityTimer = null;

  function esc(value) { return deps.escapeHtml(value); }

  /* ------------------------------------------------------------ reading state */

  function connection() { return (snapshot && snapshot.connection) || {}; }
  function current() { return (snapshot && snapshot.current) || {}; }
  function volumeState() { return (snapshot && snapshot.volume) || {}; }
  function queue() { return (snapshot && snapshot.queue) || {}; }
  function queueItems() { return queue().items || []; }
  function capabilities() { return (snapshot && snapshot.capabilities) || {}; }

  function isConnected() { return connection().connected === true; }

  function playbackState() { return current().playback_state || null; }

  function isPlaying() { return playbackState() === "playing"; }

  function isBlocked() { return playbackState() === "autoplay_blocked"; }

  function isUnavailable() { return connection().state === "unavailable"; }

  function isOpening() {
    var state = connection().state;
    return state === "launching" || state === "waiting_for_player";
  }

  /* --------------------------------------------------------------- rendering */

  function clock(seconds) {
    if (typeof seconds !== "number" || seconds < 0) { return null; }
    var minutes = Math.floor(seconds / 60);
    var rest = seconds % 60;
    if (minutes < 60) { return minutes + ":" + (rest < 10 ? "0" : "") + rest; }
    var hours = Math.floor(minutes / 60);
    var mins = minutes % 60;
    return hours + ":" + (mins < 10 ? "0" : "") + mins + ":" + (rest < 10 ? "0" : "") + rest;
  }

  function badge(text, tone) {
    return '<span class="badge' + (tone ? " " + tone : "") + '">' + esc(text) + "</span>";
  }

  function connectionLine() {
    var state = connection().state;
    if (state === "ready") {
      return badge("player open", "ok");
    }
    if (state === "launching") { return badge("opening…", "warn"); }
    if (state === "waiting_for_player") { return badge("waiting for player…", "warn"); }
    if (state === "unavailable") { return badge("unavailable on this host", "warn"); }
    return badge("player closed", "warn");
  }

  function busy(key) { return pending === key; }

  function locked() { return pending !== null; }

  function nowPlayingSection() {
    var video = current().video;
    if (!isConnected()) {
      return '<p class="muted">' +
        (isUnavailable()
          ? "This host has no browser Cofferdam can open a player in."
          : "No Cofferdam YouTube player is open on the workstation.") +
        "</p>";
    }
    if (!video) {
      return '<p class="muted">The player is open and nothing is loaded. ' +
        "Pick a YouTube result below and press <strong>Play now</strong>.</p>";
    }

    var meta = [];
    if (video.channel) { meta.push(video.channel); }
    if (video.published) { meta.push(video.published); }

    var position = clock(current().current_time_seconds);
    var duration = clock(current().duration_seconds);
    var timeline = position && duration
      ? position + " / " + duration
      : (position || duration || null);

    var stateBadge = "";
    if (isBlocked()) { stateBadge = badge("needs one click on the workstation", "warn"); }
    else if (playbackState() === "buffering") { stateBadge = badge("buffering", "warn"); }
    else if (playbackState() === "ended") { stateBadge = badge("ended"); }
    else if (playbackState() === "paused") { stateBadge = badge("paused"); }
    else if (isPlaying()) { stateBadge = badge("playing", "ok"); }

    return '<div class="yt-now">' +
      '<div class="yt-title"><strong>' + esc(video.title || "Untitled") + "</strong>" +
      stateBadge + "</div>" +
      (meta.length ? '<div class="yt-meta">' + esc(meta.join(" · ")) + "</div>" : "") +
      (timeline
        ? '<div class="yt-meta yt-time">' + esc(timeline) +
          '<span class="muted"> at last check</span></div>'
        : "") +
      "</div>";
  }

  function autoplayNotice() {
    if (!isBlocked()) { return ""; }
    /* Truthful and actionable. The browser refused; the video is loaded and one
       click on the workstation resolves it for the rest of the session. */
    return '<p class="media-note warn" id="youtubeBlockedNote">' +
      "<strong>Your browser will not start sound until the player window is clicked once.</strong> " +
      "The video you chose is loaded and waiting. Click <em>Enable playback</em> in the " +
      "Cofferdam player window on the workstation, then press play here again. " +
      "This is a browser rule, not a Cofferdam setting.</p>";
  }

  function transportSection() {
    var off = !isConnected() || locked();
    var disabled = off ? " disabled" : "";
    return '<div class="yt-transport">' +
      '<button id="youtubePrevious"' + disabled + ' aria-label="Previous in queue">‹ Prev</button>' +
      '<button id="youtubePlayPause" class="primary"' + disabled + ">" +
      (busy("transport") ? "…" : (isPlaying() ? "Pause" : "Play")) + "</button>" +
      '<button id="youtubeNext"' + disabled + ' aria-label="Next in queue">Next ›</button>' +
      "</div>";
  }

  function volumeSection() {
    var state = volumeState();
    var off = !isConnected() || locked();
    var level = draftVolume !== null
      ? draftVolume
      : (typeof state.volume_percent === "number" ? state.volume_percent : null);
    var muted = state.muted === true;

    return '<div class="yt-volume">' +
      '<label class="field">' +
      '<span class="field-label">YouTube player volume</span>' +
      '<input type="range" id="youtubeVolume" min="0" max="100" step="1" ' +
      'value="' + (level === null ? 0 : level) + '"' + (off ? " disabled" : "") +
      ' aria-label="YouTube player volume">' +
      "</label>" +
      '<span class="yt-volume-value">' +
      (level === null ? "—" : esc(String(level)) + "%") + "</span>" +
      '<button id="youtubeMute"' + (off ? " disabled" : "") + ">" +
      (busy("mute") ? "…" : (muted ? "Unmute" : "Mute")) + "</button>" +
      /* Said in the panel, not only in the docs. This is the control most likely
         to be mistaken for the machine's own volume. */
      '<p class="muted hint">This is the video player\'s own volume. ' +
      "It does not change this computer's speaker — that is the <strong>Audio</strong> " +
      "panel — and it does not change Spotify.</p>" +
      "</div>";
  }

  function queueSection() {
    var info = queue();
    var items = queueItems();
    var count = typeof info.length === "number" ? info.length : items.length;
    var off = locked();

    var head = '<div class="yt-queue-head">' +
      '<button id="youtubeQueueToggle" class="ghost" aria-expanded="' +
      (queueOpen ? "true" : "false") + '">' +
      "Queue (" + esc(String(count)) +
      (typeof info.max_length === "number" ? " of " + esc(String(info.max_length)) : "") +
      ") " + (queueOpen ? "▲" : "▼") + "</button>" +
      (count
        ? '<button id="youtubeClearQueue" class="ghost"' + (off ? " disabled" : "") + ">" +
          (busy("clear") ? "…" : "Clear") + "</button>"
        : "") +
      "</div>";

    if (!queueOpen) { return '<div class="yt-queue">' + head + "</div>"; }

    if (!count) {
      return '<div class="yt-queue">' + head +
        '<p class="muted">Nothing queued. <strong>Next</strong> only ever plays something ' +
        "you queued — Cofferdam never picks a YouTube suggestion for you.</p></div>";
    }

    var currentIndex = info.index;
    var list = items.map(function (item, index) {
      var isCurrent = index === currentIndex;
      var meta = item.channel ? esc(item.channel) : "";
      return '<li class="yt-queue-item' + (isCurrent ? " current" : "") + '">' +
        '<div class="yt-queue-text">' +
        '<span class="yt-queue-title">' + esc(item.title || "Untitled") + "</span>" +
        (isCurrent ? badge("playing now", "ok") : "") +
        (meta ? '<span class="yt-meta">' + meta + "</span>" : "") +
        "</div>" +
        '<button class="ghost yt-queue-remove" data-remove-queue-item="' +
        esc(item.queue_item_id) + '"' + (off ? " disabled" : "") +
        ' aria-label="Remove from queue">Remove</button>' +
        "</li>";
    }).join("");

    return '<div class="yt-queue">' + head + '<ul class="yt-queue-list">' + list + "</ul></div>";
  }

  function activityLine() {
    if (!activity || !activity.label) { return ""; }
    return '<p class="media-note" id="youtubeActivity">' + esc(activity.label) + "</p>";
  }

  function messages() {
    var html = "";
    if (actionError) {
      html += '<p class="media-note err">' + esc(actionError.message) +
        (actionError.detail ? " " + esc(actionError.detail) : "") + "</p>";
    }
    if (actionNote && actionNote.message) {
      html += '<p class="media-note' + (actionNote.tone === "warn" ? " warn" : "") + '">' +
        esc(actionNote.message) + "</p>";
    }
    if (loadError) {
      html += '<p class="media-note err">' + esc(loadError) + "</p>";
    }
    return html;
  }

  function openButton() {
    if (isUnavailable()) { return ""; }
    if (isConnected()) {
      return '<button id="youtubeOpenPlayer" class="ghost"' + (locked() ? " disabled" : "") +
        ">Player is open</button>";
    }
    return '<button id="youtubeOpenPlayer" class="primary"' + (locked() ? " disabled" : "") +
      ">" + (busy("open") ? "Opening…" : "Open player on workstation") + "</button>";
  }

  function render() {
    if (!deps) { return; }
    var host = deps.el("youtubeSections");
    if (!host) { return; }

    if (snapshot === null && !loadError) {
      host.innerHTML = '<p class="muted">Loading…</p>';
      return;
    }

    host.innerHTML =
      '<div class="yt-status">' + connectionLine() + openButton() + "</div>" +
      activityLine() +
      messages() +
      autoplayNotice() +
      nowPlayingSection() +
      transportSection() +
      volumeSection() +
      queueSection();

    var observed = deps.el("youtubeObserved");
    if (observed) {
      observed.textContent = snapshot && snapshot.observed_at
        ? "checked " + new Date(snapshot.observed_at).toLocaleTimeString()
        : "";
    }
  }

  /* ------------------------------------------------------- response ordering */

  function nextGeneration() {
    refreshGeneration += 1;
    return refreshGeneration;
  }

  /* Adopt a snapshot, unless something newer has already been applied. */
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
    /* Optional: the generation guard is what actually makes ordering correct,
       and aborting is the optimisation that stops a doomed response being
       downloaded at all. Where AbortController does not exist the guard alone
       still holds. */
    if (typeof global.AbortController !== "function") { return null; }
    try { return new global.AbortController(); } catch (error) { return null; }
  }

  function load() {
    var generation = nextGeneration();
    abortInflightRefresh();
    var controller = newAbortController();
    inflightRefresh = controller;
    var options = controller ? { signal: controller.signal } : undefined;

    return deps.api("/api/youtube/player", options)
      .then(function (response) {
        if (inflightRefresh === controller) { inflightRefresh = null; }
        /* Dropped in silence: a stale read is not an error, it is simply no
           longer news, and reporting it would be noise on a working panel. */
        if (generation < appliedGeneration) { return; }
        if (!response.ok) {
          appliedGeneration = generation;
          loadError = "Cofferdam could not read the YouTube player state.";
          snapshot = null;
        } else {
          adopt(response.payload, generation);
        }
        render();
        reschedule();
      })
      .catch(function (error) {
        if (inflightRefresh === controller) { inflightRefresh = null; }
        if (error && error.message === "unauthorized") { return; }
        /* An aborted request is one this code cancelled on purpose. It is not a
           reachability problem and must not be rendered as one. */
        if (error && (error.name === "AbortError" || error.aborted)) { return; }
        if (generation < appliedGeneration) { return; }
        loadError = "Cofferdam could not reach the workstation to read the YouTube player.";
        snapshot = null;
        render();
      });
  }

  /* ---------------------------------------------------------------- actions */

  function beginPending(key, timeoutMs) {
    if (pending !== null) { return false; }   /* rule 2: one action at a time */
    pending = key;
    actionError = null;
    actionNote = null;
    activity = null;
    /* Periodic state polling stops for the duration of the write. A snapshot
       landing mid-confirmation would redraw the panel underneath the user's
       finger and — before the generation guard existed — could overwrite the
       verified result the write is about to return. Both are now prevented, and
       stopping is still right: the poll would be describing a picture the action
       is about to supersede. */
    abortInflightRefresh();
    stopPolling();
    startActivityWatch();
    if (pendingTimer) { global.clearTimeout(pendingTimer); }
    pendingTimer = global.setTimeout(function () {
      if (pending !== null) {
        endPending();
        actionError = {
          message: "That did not finish in time, so Cofferdam cannot say whether it worked.",
          detail: "Refresh to see what the player is actually doing before trying again."
        };
        render();
      }
    }, timeoutMs || ACTION_TIMEOUT_MS);
    render();
    return true;
  }

  function endPending() {
    pending = null;
    activity = null;
    if (pendingTimer) { global.clearTimeout(pendingTimer); pendingTimer = null; }
    stopActivityWatch();
    reschedule();
  }

  function startActivityWatch() {
    stopActivityWatch();
    activityTimer = global.setInterval(function () {
      if (pending === null) { stopActivityWatch(); return; }
      deps.api("/api/youtube/activity").then(function (response) {
        if (pending === null || !response.ok) { return; }
        var payload = response.payload || {};
        if (payload.active === false && !payload.label) { return; }
        activity = payload;
        render();
      }).catch(function () { /* the phase is a nicety; never a failure */ });
    }, ACTIVITY_POLL_MS);
  }

  function stopActivityWatch() {
    if (activityTimer) { global.clearInterval(activityTimer); activityTimer = null; }
  }

  function describeOutcome(result) {
    /* The server compared what it asked for against what it then observed. This
       only picks a tone; it never promotes an outcome. */
    var outcome = result && result.outcome;
    if (outcome === "applied" || outcome === "queued") {
      return { tone: "ok", message: result.note };
    }
    if (outcome === "autoplay_blocked") { return { tone: "warn", message: result.note }; }
    if (outcome === "partially_applied") { return { tone: "warn", message: result.note }; }
    return { tone: "warn", message: (result && result.note) || "That did not take effect." };
  }

  function failureOf(response) {
    var error = (response.payload && response.payload.error) || {};
    return {
      code: error.code || null,
      message: error.message || "That was refused.",
      detail: error.detail || null
    };
  }

  function send(key, path, options, timeoutMs) {
    if (!beginPending(key, timeoutMs)) { return Promise.resolve(null); }
    /* Stamped now, after any earlier poll was issued and aborted, so this
       write's verified result is newer than anything already in the air. */
    var generation = nextGeneration();
    return deps.api(path, options).then(function (response) {
      endPending();
      if (!response.ok) {
        actionError = failureOf(response);
        actionNote = null;
        /* Refused actions still re-read: the commonest reason for a refusal is
           that the picture on the phone is out of date. */
        return load().then(function () { return null; });
      }
      actionError = null;
      actionNote = describeOutcome(response.payload);
      var payload = response.payload;
      /* The server acted, re-read the player on a bounded schedule, and returned
         what it *observed*. That snapshot is the freshest verified state that
         exists, so it is adopted directly — and under the newest generation, so
         a poll issued earlier can no longer paint the old value back over it. */
      if (payload && payload.player) { adopt(payload.player, generation); }
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

  function openPlayer() {
    return send("open", "/api/youtube/player/open", { body: {} }, OPEN_TIMEOUT_MS);
  }

  function transport(operation) {
    return send(
      "transport",
      "/api/youtube/player/" + encodeURIComponent(operation),
      { body: {} }
    );
  }

  function playPause() {
    return transport(isPlaying() ? "pause" : "resume");
  }

  function setVolume(value) {
    return send("volume", "/api/youtube/player/volume", {
      method: "PUT",
      body: { volume_percent: value }
    }).then(function (payload) { draftVolume = null; render(); return payload; });
  }

  function toggleMute() {
    var muted = volumeState().muted === true;
    return send("mute", "/api/youtube/player/mute", {
      method: "PUT",
      body: { muted: !muted }
    });
  }

  function clearQueue() {
    return send("clear", "/api/youtube/player/queue", { method: "DELETE" });
  }

  function removeQueueItem(handle) {
    if (!handle) { return Promise.resolve(null); }
    return send(
      "queue-" + handle,
      "/api/youtube/player/queue/" + encodeURIComponent(handle),
      { method: "DELETE" }
    );
  }

  /* Play now / Add to queue, called by the result cards in app.js.
   *
   * The only things that travel are the two handles the server issued. There is
   * no video id and no URL in this function, because there is no field for one
   * at the other end. Play now gets the long timeout: with no player open it
   * launches one, waits for it, and continues the same request. */
  function playResult(searchId, resultId) {
    if (!searchId || !resultId) { return Promise.resolve(null); }
    return send(
      "play-" + resultId,
      "/api/media/searches/" + encodeURIComponent(searchId) +
        "/results/" + encodeURIComponent(resultId) + "/youtube/play",
      { body: {} },
      OPEN_TIMEOUT_MS
    );
  }

  function queueResult(searchId, resultId) {
    if (!searchId || !resultId) { return Promise.resolve(null); }
    return send(
      "queue-" + resultId,
      "/api/media/searches/" + encodeURIComponent(searchId) +
        "/results/" + encodeURIComponent(resultId) + "/youtube/queue",
      { body: {} }
    );
  }

  /* ---------------------------------------------------------------- polling */

  function visible() {
    /* A hidden tab is a phone in a pocket. Polling it spends the workstation's
       time describing a screen nobody is looking at. */
    var doc = global.document;
    return !doc || doc.visibilityState !== "hidden";
  }

  function wanted() {
    /* No periodic polling while a write is being confirmed: the activity watch
       covers that window and costs the player nothing. */
    if (stopped || pending !== null) { return null; }
    return POLL_MS;
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
      /* Belt and braces with `wanted()`: an action can begin between ticks. */
      if (pending === null && visible()) { load(); }
    }, interval);
  }

  /* ----------------------------------------------------------------- wiring */

  function mount(dependencies) {
    deps = dependencies;
    stopped = false;

    var root = deps.el("youtubePanel");
    if (root) {
      root.addEventListener("click", function (event) {
        var target = event.target;
        if (!target) { return; }

        var remove = target.closest
          ? target.closest("[data-remove-queue-item]")
          : null;
        if (remove) {
          removeQueueItem(remove.getAttribute("data-remove-queue-item"));
          return;
        }

        switch (target.id) {
          case "youtubeRefresh": load(); return;
          case "youtubeOpenPlayer": openPlayer(); return;
          case "youtubePlayPause": playPause(); return;
          case "youtubeNext": transport("next"); return;
          case "youtubePrevious": transport("previous"); return;
          case "youtubeMute": toggleMute(); return;
          case "youtubeClearQueue": clearQueue(); return;
          case "youtubeQueueToggle": queueOpen = !queueOpen; render(); return;
          default: return;
        }
      });

      /* `input` tracks the finger, `change` commits. Separating them keeps a
         drag from firing a request per pixel, and keeps the committed value
         distinct from the one under the thumb. */
      root.addEventListener("input", function (event) {
        if (event.target && event.target.id === "youtubeVolume") {
          var parsed = parseInt(event.target.value, 10);
          draftVolume = isNaN(parsed) ? null : Math.max(0, Math.min(100, parsed));
          var readout = root.querySelector ? root.querySelector(".yt-volume-value") : null;
          if (readout && draftVolume !== null) { readout.textContent = draftVolume + "%"; }
        }
      });

      root.addEventListener("change", function (event) {
        if (!event.target || event.target.id !== "youtubeVolume") { return; }
        var parsed = parseInt(event.target.value, 10);
        if (isNaN(parsed)) { draftVolume = null; render(); return; }
        setVolume(Math.max(0, Math.min(100, parsed)));
      });
    }

    reschedule();
    return load();
  }

  function stop() {
    stopped = true;
    stopPolling();
    stopActivityWatch();
    abortInflightRefresh();
    pending = null;
    if (pendingTimer) { global.clearTimeout(pendingTimer); pendingTimer = null; }
    activity = null;
    /* What somebody is watching and what they lined up next go with the token.
       A signed-out device keeps none of it and makes no further requests. */
    snapshot = null;
    loadError = null;
    actionError = null;
    actionNote = null;
    draftVolume = null;
    queueOpen = false;
    render();
  }

  global.CofferdamYouTube = {
    mount: mount,
    refresh: load,
    stop: stop,
    available: function () { return !!snapshot && !isUnavailable(); },
    connected: isConnected,
    playResult: playResult,
    queueResult: queueResult
  };
})(window);
