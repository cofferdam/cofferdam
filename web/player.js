/* Cofferdam — the dedicated YouTube player page (M2E).
 *
 * Runs in Opera on the workstation, served by the loopback-only listener in
 * cofferdam/workstation/youtubeplayer/endpoint.py. It is the *controlled* side
 * of the product: the phone talks to the authenticated Cofferdam API, the API
 * puts a typed command on the loopback channel, and this file executes it
 * against one official YouTube IFrame player.
 *
 * The rules this file is written to:
 *
 *   1. **One player, for the whole session.** The iframe is built once, on the
 *      first video, and every video after that is `loadVideoById` on the same
 *      player. Nothing here creates a tab, navigates, or replaces the frame.
 *   2. **A closed command vocabulary.** `run()` is a switch over five names.
 *      There is no eval, no Function, no dynamic dispatch, no property lookup
 *      from a message field, and no path by which a channel message can name a
 *      function to call. A message this file does not recognise is ignored.
 *   3. **Report, never assert.** The state posted back is read from the player
 *      on every tick — `getPlayerState`, `getVolume`, `isMuted`,
 *      `getCurrentTime`, `getDuration`. This file never posts what it was asked
 *      for. The backend's "did it work" answer depends on that being true.
 *   4. **Nothing is logged.** There is no `console` call in this file. What is
 *      playing is a fact about somebody's evening, and a browser console is a
 *      surface neither of us controls.
 *   5. **Nothing is stored.** No localStorage, no sessionStorage, no cookies,
 *      no token. Closing the tab leaves nothing behind.
 *
 * Autoplay, honestly
 * ------------------
 * The official API documents an `onAutoplayBlocked` event, and Chromium's
 * policy is that muted autoplay is always allowed while unmuted playback needs
 * user activation. Media autoplay requires *sticky* activation, which is never
 * consumed and lasts the lifetime of the document — which is why one click on
 * the gate below is enough for the rest of the session rather than once per
 * video. The iframe carries `allow="autoplay"` so that activation is delegated
 * to the cross-origin frame; without that attribute the click would apply to
 * this page and not to the player inside it.
 */
(function () {
  "use strict";

  /* Fixed, and matching endpoint.py. Same-origin relative paths: this page has
     no idea what host or port it is on and never needs one. */
  var PATH_REGISTER = "/channel/register";
  var PATH_COMMANDS = "/channel/commands";
  var PATH_STATE = "/channel/state";
  var PATH_ACK = "/channel/ack";
  var PATH_RELEASE = "/channel/release";

  var IFRAME_API = "https://www.youtube.com/iframe_api";
  var EMBED_ORIGIN = "https://www.youtube.com";

  /* The five commands the backend may send. A frozen list rather than a
     convention: "what can the backend make this page do" is answerable by
     reading it. */
  var COMMAND_LOAD = "load_video";
  var COMMAND_PLAY = "play";
  var COMMAND_PAUSE = "pause";
  var COMMAND_SET_VOLUME = "set_volume";
  var COMMAND_SET_MUTED = "set_muted";

  var heartbeatMs = 2000;
  var instanceId = null;
  var player = null;
  var playerReady = false;
  var lastSequence = 0;
  var currentVideoId = null;
  var autoplayBlocked = false;
  var lastErrorCode = null;
  var stopped = false;
  var polling = false;
  var apiReady = false;
  var pendingVideoId = null;
  /* The embedding origin, supplied by the server at registration. Never derived
     here, and never accepted from anywhere else. */
  var playerOrigin = null;

  var dot = document.getElementById("dot");
  var statusText = document.getElementById("status");
  var stage = document.getElementById("stage");
  var idle = document.getElementById("idle");
  var gate = document.getElementById("gate");
  var enableButton = document.getElementById("enable");

  function setStatus(text, state) {
    statusText.textContent = text;
    dot.className = "dot" + (state ? " " + state : "");
  }

  /* ------------------------------------------------------------- transport */

  /* Every channel call is a POST carrying application/json. That content type
     is what forces a cross-origin caller into a preflight the listener never
     answers, so it is not decoration and is never dropped for a "simple"
     request. */
  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      cache: "no-store",
      credentials: "omit"
      /* No `referrerPolicy` override here either. These are same-origin POSTs
         to this page's own loopback channel, so the document policy applies and
         nothing leaves the machine. Pinning `no-referrer` here would be
         harmless in itself, but this file must contain no `no-referrer` at all:
         it is the exact string that caused error 153, and leaving one in place
         invites the next reader to copy it back onto the iframe. */
    });
  }

  /* ------------------------------------------------------------ the player */

  function videoIdIsWellFormed(value) {
    /* The same eleven-character shape the server validates. Checked again here
       because this value is about to become part of a URL: a page that trusted
       whatever arrived would be the one place in the chain that did not. */
    return typeof value === "string" && /^[A-Za-z0-9_-]{11}$/.test(value);
  }

  function embedUrl(videoId) {
    /* Built from constants, one validated id, and the origin the *server* gave
       us at registration.

       `enablejsapi` is what makes the player controllable. `origin` is the
       documented security parameter and must be the complete embedding origin
       including the port — `http://127.0.0.1:40187`, not `http://127.0.0.1` —
       because a different port is a different origin to a browser.

       It comes from the register response rather than from
       `window.location.origin` so the value has exactly one author. The two
       agree, and a mismatch means something is wrong enough not to guess
       through: `createPlayer` refuses rather than falling back. */
    return EMBED_ORIGIN + "/embed/" + videoId +
      "?enablejsapi=1" +
      "&origin=" + encodeURIComponent(playerOrigin) +
      "&playsinline=1" +
      "&rel=0" +          /* keep the end screen to this channel's videos */
      "&autoplay=1";
  }

  function createPlayer(videoId, onCreated) {
    if (!playerOrigin) {
      /* No server-supplied origin means no truthful `origin` parameter, and an
         embed that lies about its origin is refused by YouTube anyway. Saying
         so is better than building a player that cannot work. */
      setStatus("Cofferdam did not supply the player origin", "off");
      return;
    }
    var frame = document.createElement("iframe");
    frame.id = "cofferdam-youtube-frame";
    frame.src = embedUrl(videoId);
    frame.title = "Cofferdam YouTube player";
    /* The load-bearing attribute. Autoplay permission is delegated to a
       cross-origin frame only through this allowlist; without it, a click on
       this page would grant activation to this page alone and the player inside
       would still refuse to start. */
    frame.setAttribute("allow", "autoplay; encrypted-media; picture-in-picture; fullscreen");
    frame.setAttribute("allowfullscreen", "");
    /* The second half of the error-153 fix, and the half that is easy to miss.
       A per-iframe `referrerpolicy` **overrides** the document's policy in both
       directions — measured, not assumed — so a `no-referrer` here would strip
       the Referer even though the response header now permits it, and YouTube
       would go on refusing the embed. Set explicitly rather than inherited, so
       the value is visible at the one place it matters. */
    frame.setAttribute("referrerpolicy", "strict-origin-when-cross-origin");
    stage.appendChild(frame);
    idle.hidden = true;

    player = new YT.Player(frame, {
      events: {
        onReady: function () {
          playerReady = true;
          if (onCreated) { onCreated(); }
        },
        onStateChange: function (event) {
          /* A state change to playing is the browser telling us the block is
             over. Clearing it here rather than on the click means the gate
             disappears when playback actually starts, not when it was asked
             for. */
          if (event && event.data === 1) {
            autoplayBlocked = false;
            gate.hidden = true;
          }
        },
        onError: function (event) {
          lastErrorCode = event && typeof event.data === "number" ? event.data : null;
        },
        onAutoplayBlocked: function () {
          /* The documented event. This is the browser refusing, not a failure
             of the command, and it is reported as its own state. */
          autoplayBlocked = true;
          gate.hidden = false;
        }
      }
    });
  }

  /* ------------------------------------------------------------- commands */

  /* The complete set of things the backend can make this page do. A switch, so
     no message field is ever used to look up a function. */
  function run(command) {
    var name = command && command.command;

    if (name === COMMAND_LOAD) {
      var videoId = command.video_id;
      if (!videoIdIsWellFormed(videoId)) { return; }
      currentVideoId = videoId;
      lastErrorCode = null;
      if (!player) {
        /* First video of the session: build the one iframe. If the official
           API script has not finished loading yet, remember the video and build
           it in onYouTubeIframeAPIReady — the command is not lost and no second
           player is created when the script does arrive. */
        if (!apiReady) { pendingVideoId = videoId; return; }
        createPlayer(videoId, null);
        return;
      }
      if (!playerReady) { return; }
      /* Every subsequent video: the same player, the same tab, the same frame.
         This single call is the whole difference from the old behaviour. */
      if (command.autoplay === false) {
        player.cueVideoById({ videoId: videoId });
      } else {
        player.loadVideoById({ videoId: videoId });
      }
      return;
    }

    if (!player || !playerReady) { return; }

    if (name === COMMAND_PLAY) {
      player.playVideo();
      return;
    }
    if (name === COMMAND_PAUSE) {
      player.pauseVideo();
      return;
    }
    if (name === COMMAND_SET_VOLUME) {
      var level = command.volume_percent;
      /* Re-checked at the last possible moment. The server validated it, and
         this page still refuses anything outside the documented 0–100 range
         rather than passing an unchecked number to the player API. */
      if (typeof level !== "number" || !isFinite(level) || level < 0 || level > 100) { return; }
      player.setVolume(level);
      /* Setting a level while muted would otherwise look like it did nothing:
         the player remembers the volume and stays silent. Unmuting here matches
         what "set the volume to 40" means to a person. */
      if (level > 0 && player.isMuted && player.isMuted()) { player.unMute(); }
      return;
    }
    if (name === COMMAND_SET_MUTED) {
      if (command.muted === true) { player.mute(); }
      else if (command.muted === false) { player.unMute(); }
      return;
    }
    /* Anything else: ignored. There is no default branch that does work. */
  }

  /* ---------------------------------------------------------------- state */

  function readState() {
    /* Read from the player every time. Nothing here is remembered from a
       command, which is what makes the backend's confirmation meaningful. */
    var state = { autoplay_blocked: autoplayBlocked, error_code: lastErrorCode };
    if (player && playerReady) {
      try {
        state.player_state = player.getPlayerState();
        state.current_time = player.getCurrentTime();
        state.duration = player.getDuration();
        state.volume = player.getVolume();
        state.muted = player.isMuted();
        state.video_id = currentVideoId;
      } catch (error) {
        /* A player mid-navigation can throw. Reporting nothing is honest;
           reporting the last known values would be inventing an observation. */
        state = { autoplay_blocked: autoplayBlocked, error_code: lastErrorCode };
      }
    }
    return state;
  }

  function beat() {
    if (stopped || !instanceId) { return; }
    post(PATH_STATE, { instance_id: instanceId, state: readState() })
      .then(function (response) {
        if (response.status === 409) { supersede(); return; }
        if (response.ok) { setStatus("connected to Cofferdam", "on"); }
      })
      .catch(function () {
        setStatus("Cofferdam is not answering", "off");
      });
  }

  function supersede() {
    /* Another player registered — usually this tab was reloaded, or the page
       was opened twice. The older one stops rather than competing. */
    stopped = true;
    setStatus("replaced by a newer player window", "off");
    document.getElementById("note").textContent =
      "This window was replaced by a newer Cofferdam player. You can close it.";
  }

  /* ----------------------------------------------------------- the channel */

  function pollOnce() {
    if (stopped || polling || !instanceId) { return; }
    polling = true;
    post(PATH_COMMANDS, { instance_id: instanceId, after: lastSequence })
      .then(function (response) {
        if (response.status === 409) { supersede(); return null; }
        if (!response.ok) { return null; }
        return response.json();
      })
      .then(function (payload) {
        var commands = (payload && payload.commands) || [];
        for (var index = 0; index < commands.length; index += 1) {
          var command = commands[index];
          if (typeof command.sequence !== "number" || command.sequence <= lastSequence) {
            continue;
          }
          lastSequence = command.sequence;
          run(command);
          /* Acknowledged after running, so an ack means "executed" rather than
             "received". The backend distinguishes a wedged tab from a video
             YouTube refused on exactly this. */
          post(PATH_ACK, { instance_id: instanceId, sequence: command.sequence })
            .catch(function () {});
        }
      })
      .catch(function () {})
      .then(function () {
        polling = false;
        /* One poll at a time, re-armed only after the previous one finished.
           There is no interval driving this, so a slow or failed poll can never
           produce overlapping requests. */
        if (!stopped) { window.setTimeout(pollOnce, 0); }
      });
  }

  /* -------------------------------------------------------------- start-up */

  function register() {
    post(PATH_REGISTER, {})
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (payload) {
        if (!payload || typeof payload.instance_id !== "string") {
          setStatus("Cofferdam did not accept this player", "off");
          return;
        }
        instanceId = payload.instance_id;
        /* Taken from the server, then checked against what this document
           actually is. They must agree; if they do not, something has gone
           wrong that guessing would only hide, so the page says so and builds
           no player. */
        if (typeof payload.player_origin === "string" &&
            payload.player_origin === window.location.origin) {
          playerOrigin = payload.player_origin;
        } else {
          setStatus("Cofferdam and this page disagree about the player origin", "off");
        }
        if (typeof payload.heartbeat_seconds === "number" && payload.heartbeat_seconds > 0) {
          heartbeatMs = payload.heartbeat_seconds * 1000;
        }
        setStatus("connected to Cofferdam", "on");
        window.setInterval(beat, heartbeatMs);
        beat();
        pollOnce();
      })
      .catch(function () {
        setStatus("Cofferdam is not answering", "off");
      });
  }

  enableButton.addEventListener("click", function () {
    /* Inside the click handler on purpose: this is the user activation, and it
       has to be the thing that calls play. */
    gate.hidden = true;
    autoplayBlocked = false;
    if (player && playerReady) {
      player.unMute();
      player.playVideo();
    }
    beat();
  });

  window.addEventListener("pagehide", function () {
    /* Best effort, and the heartbeat is the real mechanism: if this never
       arrives, the player goes stale within a few seconds anyway. `keepalive`
       so the request survives the page going away. */
    if (!instanceId) { return; }
    try {
      fetch(PATH_RELEASE, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instance_id: instanceId }),
        keepalive: true
      });
    } catch (error) { /* the tab is closing; there is nothing to recover */ }
  });

  /* The official loader contract: define the global, then load the script. */
  window.onYouTubeIframeAPIReady = function () {
    apiReady = true;
    setStatus("player ready", "on");
    /* A video that arrived while the script was still downloading. Built once,
       here, so an early Play now is honoured rather than dropped. */
    if (pendingVideoId && !player) {
      var videoId = pendingVideoId;
      pendingVideoId = null;
      createPlayer(videoId, null);
    }
  };

  var api = document.createElement("script");
  api.src = IFRAME_API;
  api.async = true;
  document.head.appendChild(api);

  register();
})();
