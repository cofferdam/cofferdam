/* Cofferdam — the "Audio" area (M2C audio control foundation).
 *
 * Its own file for the same reason live.js is: a separate vocabulary. app.js
 * renders configuration, live.js renders discovered runtime resources, and this
 * file renders the one surface that *changes the physical machine*. The volume
 * in the room moves when a control here is used, which sets the rules below.
 *
 *   1. **Nothing is claimed until the server has been observed.** No control
 *      here updates its own display from what the user asked for. Every value
 *      shown comes from the server's `observed` state or from a fresh snapshot.
 *      A slider that snapped to 25% because the user dragged it there would be
 *      telling them the speaker is at 25% when nothing has confirmed that.
 *   2. **One action at a time.** Controls are disabled while a request is in
 *      flight, and the pending state is bounded by a timer, so a request that
 *      never returns re-enables the panel with an error instead of freezing it.
 *   3. **`unavailable` is not `empty`.** A host that cannot enumerate streams
 *      says so; it never renders as "nothing is playing".
 *   4. **The graph is not the product.** The full PipeWire node list belongs in
 *      a diagnostic, not in a panel someone opens to turn the volume down.
 *      Outputs and streams are collapsed behind summaries; the default view is
 *      the current output, the volume, and a mute button.
 *
 * Volume semantics worth stating once: the number here is the *system output*
 * level for one device — the same thing the laptop's own volume key changes.
 * A future Spotify or YouTube player volume is a different control on a
 * different object and does not belong in this panel.
 */
(function (global) {
  "use strict";

  var deps = null;

  /* Slower than the live inventory: audio state changes when someone changes
     it, and a dump of the audio graph on a timer is a poor trade for a phone
     left open on a desk. The refresh button covers "now". */
  var POLL_MS = 20000;

  /* An action that has not answered by this point has failed as far as the user
     is concerned. Without this bound a dropped connection leaves the panel
     permanently disabled, which is the failure mode that makes people reload
     the page and press the button twice. */
  var ACTION_TIMEOUT_MS = 12000;

  /* Device categories the backend may report, and what to call them. A category
     absent from this table renders under its own name rather than being forced
     into a wrong bucket. */
  var DEVICE_LABELS = {
    builtin_speaker: "Built-in",
    hdmi: "HDMI / DisplayPort",
    usb: "USB",
    bluetooth: "Bluetooth",
    unknown: "Unrecognised"
  };

  var snapshot = null;
  var loadError = null;
  var timer = null;
  var pending = null;      /* which control is busy: a string key, or null */
  var pendingTimer = null;
  var actionError = null;  /* {message, detail} from the server, verbatim */
  var actionNote = null;   /* the observed outcome of the last action */
  var draftVolume = null;  /* slider position while dragging; never displayed as truth */

  function esc(value) { return deps.escapeHtml(value); }

  function outputsCollection() {
    return snapshot && snapshot.collections ? snapshot.collections.outputs : null;
  }

  function streamsCollection() {
    return snapshot && snapshot.collections ? snapshot.collections.streams : null;
  }

  function outputItems() {
    var collection = outputsCollection();
    return collection && collection.items ? collection.items : [];
  }

  function currentOutput() {
    var wanted = snapshot ? snapshot.default_output_resource_id : null;
    if (!wanted) { return null; }
    var items = outputItems();
    for (var i = 0; i < items.length; i += 1) {
      if (items[i].resource_id === wanted) { return items[i]; }
    }
    return null;
  }

  function deviceLabel(item) {
    var key = item && item.device_type;
    if (!key) { return ""; }
    return DEVICE_LABELS[key] || String(key).replace(/_/g, " ");
  }

  /* ------------------------------------------------------------- rendering */

  function renderUnavailable(collection, what) {
    /* The backend's own words. Rewriting them here would lose the part that
       tells the user what to do about it. */
    var reason = collection && collection.reason ? collection.reason : null;
    return '<p class="audio-unavailable">' +
      esc("Cofferdam cannot read " + what + " on this machine right now.") +
      (reason ? ' <span class="muted">' + esc(reason) + "</span>" : "") + "</p>";
  }

  function renderSummary() {
    var collection = outputsCollection();
    if (!collection) { return '<p class="muted">Loading…</p>'; }
    if (collection.status === "unavailable" || collection.status === "error") {
      return renderUnavailable(collection, "its audio outputs");
    }

    var output = currentOutput();
    if (!output) {
      var items = outputItems();
      if (!items.length) {
        return '<p class="audio-unavailable">' +
          esc("This machine has no audio outputs available right now.") + "</p>";
      }
      return '<p class="audio-unavailable">' +
        esc("This machine has outputs but no default one, so Cofferdam will not " +
            "guess which to control. Choose one below.") + "</p>";
    }

    var busy = pending !== null;
    var volume = typeof output.volume_percent === "number" ? output.volume_percent : null;
    var muted = output.muted === true;
    var shown = draftVolume === null ? volume : draftVolume;

    var rows = [];
    rows.push(
      '<div class="audio-current">' +
        '<div class="audio-current-label">Current output</div>' +
        '<div class="audio-current-name">' + esc(output.display_name || output.node_name || "—") +
        "</div>" +
        '<div class="audio-current-sub muted">' + esc(deviceLabel(output)) +
          (output.route ? esc(" · " + output.route) : "") + "</div>" +
      "</div>"
    );

    if (volume === null) {
      /* A volume the host would not report is left absent. Rendering 0% or 50%
         here would be inventing a number someone might act on. */
      rows.push('<p class="audio-unavailable">' +
        esc("This output did not report a volume level, so it cannot be changed from here.") +
        "</p>");
    } else {
      rows.push(
        '<div class="audio-volume">' +
          '<label class="audio-volume-label" for="audioVolume">Volume</label>' +
          '<input type="range" id="audioVolume" min="0" max="100" step="1" ' +
            'value="' + esc(String(shown)) + '" ' +
            'aria-label="output volume percent" ' +
            'aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + esc(String(shown)) + '" ' +
            (busy ? "disabled " : "") + ">" +
          '<output class="audio-volume-value" for="audioVolume">' +
            esc(String(shown)) + "%</output>" +
        "</div>"
      );
      if (draftVolume !== null && draftVolume !== volume) {
        /* The pending target is shown as a target, never as the state. */
        rows.push('<p class="audio-pending muted">' +
          esc("Release to set " + draftVolume + "% — currently " + volume + "%.") + "</p>");
      }
    }

    rows.push(
      '<div class="audio-actions">' +
        '<button id="audioMute" class="' + (muted ? "primary" : "") + '" ' +
          (busy ? "disabled" : "") + ">" +
          esc(muted ? "Unmute" : "Mute") +
        "</button>" +
        (muted ? '<span class="audio-badge">Muted</span>' : "") +
      "</div>"
    );
    return rows.join("");
  }

  function renderOutputs() {
    var collection = outputsCollection();
    if (!collection) { return ""; }
    if (collection.status === "unavailable" || collection.status === "error") { return ""; }

    var items = outputItems();
    var current = snapshot ? snapshot.default_output_resource_id : null;
    var busy = pending !== null;

    var cards = items.map(function (item) {
      var isCurrent = item.resource_id === current;
      var parts = [];
      parts.push('<div class="audio-output-name">' +
        esc(item.display_name || item.node_name || "—") +
        (isCurrent ? ' <span class="audio-badge">Current</span>' : "") + "</div>");
      var sub = [deviceLabel(item)];
      if (item.route) { sub.push(item.route); }
      if (typeof item.volume_percent === "number") { sub.push(item.volume_percent + "%"); }
      if (item.muted === true) { sub.push("muted"); }
      parts.push('<div class="audio-output-sub muted">' + esc(sub.join(" · ")) + "</div>");
      if (!isCurrent) {
        parts.push('<button class="audio-select ghost" data-output="' +
          esc(item.resource_id) + '"' + (busy ? " disabled" : "") + ">Set as output</button>");
      }
      return '<div class="audio-output">' + parts.join("") + "</div>";
    });

    if (!cards.length) {
      cards.push('<p class="muted">' + esc("No outputs are available right now.") + "</p>");
    }

    /* The backend's warnings are where "your HDMI card is off because nothing is
       plugged into it" lives. Dropping them would leave a user staring at a list
       that is missing the monitor they are looking at. */
    var warnings = (snapshot && snapshot.warnings) || [];
    var notes = warnings.length
      ? '<ul class="audio-notes">' + warnings.map(function (text) {
          return "<li>" + esc(text) + "</li>";
        }).join("") + "</ul>"
      : "";

    return '<details class="audio-fold" id="audioOutputsFold">' +
      "<summary>Outputs <span class=\"count\">" + esc(String(items.length)) + "</span></summary>" +
      '<p class="muted audio-switch-note">' +
        esc("Choosing an output changes where new sound goes. Audio that is already " +
            "playing may stay where it is — Cofferdam will tell you which happened.") +
      "</p>" +
      '<div class="audio-output-list">' + cards.join("") + "</div>" +
      notes +
      "</details>";
  }

  function renderStreams() {
    var collection = streamsCollection();
    if (!collection) { return ""; }

    if (collection.status === "unavailable" || collection.status === "error") {
      /* The panel stays fully usable without this section: volume, mute and
         output selection are all above and none of them depend on it. */
      return '<details class="audio-fold">' +
        "<summary>Active audio streams</summary>" +
        renderUnavailable(collection, "what is currently playing") +
        "</details>";
    }

    var items = collection.items || [];
    var rows = items.map(function (item) {
      var association = item.association || {};
      var identified = association.status === "identified";
      /* An unidentified stream is shown as unidentified. Falling back to the
         name the application declared about itself would turn a guess into a
         label, which is the exact thing the backend refused to do. */
      var title = identified
        ? association.application
        : (item.declared_application_name
            ? item.declared_application_name + " (unverified)"
            : "Unidentified");
      var sub = [];
      if (item.media_role) { sub.push(item.media_role); }
      if (item.state) { sub.push(item.state); }
      if (!identified && association.reason) { sub.push(association.reason); }
      return '<div class="audio-stream">' +
        '<div class="audio-stream-name">' + esc(title) + "</div>" +
        '<div class="audio-stream-sub muted">' + esc(sub.join(" · ")) + "</div>" +
        "</div>";
    });

    if (!rows.length) {
      rows.push('<p class="muted">' + esc("Nothing is playing right now.") + "</p>");
    }
    return '<details class="audio-fold">' +
      "<summary>Active audio streams <span class=\"count\">" +
        esc(String(items.length)) + "</span></summary>" +
      '<p class="muted">' +
        esc("What Cofferdam can see producing sound. Titles of what is playing are " +
            "never read or shown.") + "</p>" +
      '<div class="audio-stream-list">' + rows.join("") + "</div>" +
      "</details>";
  }

  function renderFeedback() {
    var blocks = [];
    if (actionError) {
      blocks.push('<p class="audio-error">' + esc(actionError.message) +
        (actionError.detail ? ' <span class="muted">' + esc(actionError.detail) + "</span>" : "") +
        "</p>");
    }
    if (actionNote) {
      blocks.push('<p class="audio-note ' + esc(actionNote.tone) + '">' +
        esc(actionNote.message) + "</p>");
    }
    return blocks.join("");
  }

  function render() {
    var root = deps.el("audioSections");
    if (!root) { return; }
    if (loadError) {
      root.innerHTML = '<p class="audio-unavailable">' + esc(loadError) + "</p>";
      return;
    }
    if (!snapshot) {
      root.innerHTML = '<p class="muted">Loading…</p>';
      return;
    }
    root.innerHTML =
      renderSummary() + renderFeedback() + renderOutputs() + renderStreams();

    var stamp = deps.el("audioObserved");
    if (stamp) {
      stamp.textContent = snapshot.observed_at
        ? "observed " + new Date(snapshot.observed_at).toLocaleTimeString() : "";
    }
    var refresh = deps.el("audioRefresh");
    if (refresh) { refresh.disabled = pending !== null; }
  }

  /* ---------------------------------------------------------------- loading */

  function load(force) {
    return deps.api("/api/audio" + (force ? "?refresh=true" : "")).then(function (response) {
      if (!response.ok) {
        loadError = "Cofferdam could not read this machine's audio state.";
        snapshot = null;
      } else {
        loadError = null;
        snapshot = response.payload;
      }
      render();
    }).catch(function () {
      loadError = "Cofferdam could not reach the workstation to read its audio state.";
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
      /* The bound. Whatever happened to the request, the panel comes back. */
      if (pending !== null) {
        pending = null;
        pendingTimer = null;
        actionError = {
          message: "That did not finish in time, so Cofferdam cannot say whether it worked.",
          detail: "Refresh to see the current state before trying again."
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
    /* The server already computed this by comparing observed state against the
       request. This function only chooses a tone; it never upgrades an outcome. */
    var outcome = result && result.outcome;
    if (outcome === "applied") { return { tone: "ok", message: result.message }; }
    if (outcome === "partially_applied") { return { tone: "warn", message: result.message }; }
    return { tone: "warn", message: (result && result.message) || "That did not take effect." };
  }

  function send(key, path, body) {
    if (!beginPending(key)) { return Promise.resolve(); }
    var settings = { method: "PUT", body: body || {} };
    return deps.api(path, settings).then(function (response) {
      endPending();
      if (!response.ok) {
        var error = (response.payload && response.payload.error) || {};
        /* The server's validation message is shown as-is. It is written for a
           person and it is the only account of what was wrong. */
        actionError = {
          message: error.message || "That was refused.",
          detail: error.detail || null
        };
        actionNote = null;
        /* Refused actions still refresh: the most common reason for a refusal
           is that the picture on the phone is out of date. */
        return load(true);
      }
      actionError = null;
      actionNote = describeOutcome(response.payload);
      /* Re-read rather than trusting the response envelope alone, so the whole
         panel — including any other output whose state moved — is server-truth. */
      return load(true);
    }).catch(function () {
      endPending();
      actionError = { message: "Cofferdam could not reach the workstation.", detail: null };
      render();
    });
  }

  function setVolume(value) {
    var output = currentOutput();
    if (!output) { return Promise.resolve(); }
    return send(
      "volume",
      "/api/audio/outputs/" + encodeURIComponent(output.resource_id) + "/volume",
      { volume_percent: value }
    ).then(function () { draftVolume = null; });
  }

  function toggleMute() {
    var output = currentOutput();
    if (!output) { return Promise.resolve(); }
    return send(
      "mute",
      "/api/audio/outputs/" + encodeURIComponent(output.resource_id) + "/mute",
      { muted: output.muted !== true }
    );
  }

  function selectOutput(id) {
    return send("default", "/api/audio/outputs/" + encodeURIComponent(id) + "/default", {});
  }

  /* ---------------------------------------------------------------- wiring */

  function schedule() {
    if (timer) { global.clearInterval(timer); }
    timer = global.setInterval(function () {
      /* Never poll over an action in flight: a snapshot landing mid-request
         would redraw the panel underneath the user's finger. */
      if (pending === null) { load(false); }
    }, POLL_MS);
  }

  function mount(dependencies) {
    deps = dependencies;

    var root = deps.el("audioPanel");
    if (root) {
      root.addEventListener("click", function (event) {
        var target = event.target;
        if (!target) { return; }
        if (target.id === "audioMute") { toggleMute(); return; }
        if (target.id === "audioRefresh") { load(true); return; }
        var chosen = target.getAttribute && target.getAttribute("data-output");
        if (chosen) { selectOutput(chosen); }
      });

      /* `input` tracks the finger, `change` commits. Separating them is what
         keeps a drag from firing a request per pixel, and what keeps the
         committed value distinct from the one under the thumb. */
      root.addEventListener("input", function (event) {
        if (event.target && event.target.id === "audioVolume") {
          var parsed = parseInt(event.target.value, 10);
          draftVolume = isNaN(parsed) ? null : Math.max(0, Math.min(100, parsed));
          var readout = root.querySelector ? root.querySelector(".audio-volume-value") : null;
          if (readout && draftVolume !== null) { readout.textContent = draftVolume + "%"; }
        }
      });

      root.addEventListener("change", function (event) {
        if (event.target && event.target.id === "audioVolume") {
          var parsed = parseInt(event.target.value, 10);
          if (isNaN(parsed)) { draftVolume = null; render(); return; }
          setVolume(Math.max(0, Math.min(100, parsed)));
        }
      });
    }

    schedule();
    return load(false);
  }

  function stop() {
    if (timer) { global.clearInterval(timer); timer = null; }
    endPending();
    snapshot = null;
    loadError = null;
    actionError = null;
    actionNote = null;
    draftVolume = null;
    render();
  }

  global.CofferdamAudio = { mount: mount, refresh: load, stop: stop };
})(window);
