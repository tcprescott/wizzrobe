/*
 * Browser-side Sentry: errors from people's browsers, each with a masked replay
 * of the minute before it. Loaded after static/vendor/sentry/bundle.replay.min.js
 * when theme/browser_monitoring.py hands over window.__wizSentryConfig.
 *
 * The UI runs over a websocket, so most browser errors never reach
 * window.onerror on their own: Vue hands render errors to app.config.errorHandler
 * and NiceGUI catches errors while applying a server update and only logs them.
 * Both are routed to Sentry here.
 *
 * Nothing is sent that a rule ignores (the built-in list plus super-admin rules
 * from /platform), and the server can switch sending off, change the share sent,
 * or replace the rules under an open tab through window.__wizSentry.apply().
 */
(function () {
  if (window.__wizSentry || !window.Sentry || !window.__wizSentryConfig) return;

  var cfg = window.__wizSentryConfig;
  var live = { enabled: cfg.enabled, replay: cfg.replay, sampleRate: cfg.sampleRate, rules: cfg.rules || [] };
  var REPLAY_EVENT = 'wiz_sentry_replay';
  var originalConsoleError = console.error.bind(console);
  var reportedReplayId = null;

  function lower(text) {
    return String(text || '').toLowerCase();
  }

  function messagesOf(event) {
    var out = [];
    if (event.message) out.push(typeof event.message === 'string' ? event.message : event.message.formatted);
    if (event.logentry && event.logentry.message) out.push(event.logentry.message);
    ((event.exception && event.exception.values) || []).forEach(function (value) {
      out.push((value.type || '') + ': ' + (value.value || ''));
    });
    return out.map(lower);
  }

  function urlsOf(event) {
    var out = [];
    ((event.exception && event.exception.values) || []).forEach(function (value) {
      ((value.stacktrace && value.stacktrace.frames) || []).forEach(function (frame) {
        if (frame.filename) out.push(lower(frame.filename));
      });
    });
    return out;
  }

  // Without Vue the page is dead, and everything that runs after says so in its
  // own words: Quasar reading window.Vue (Safari's "Cannot destructure property
  // 'h' from null or undefined value" names nothing of ours, SGLMAN-1R), the
  // language pack, NiceGUI's page script. The framework load guard reloads the
  // page and reports a failure that outlives the reload, so everything else is
  // follow-on noise. The SDK attaches a stack to that report too, so it is told
  // apart by its text.
  var GUARD_REPORT = 'framework scripts failed to load';

  function missingVue(event) {
    if (window.Vue) return false;
    return !messagesOf(event).some(function (text) { return text.indexOf(GUARD_REPORT) !== -1; });
  }

  // Without import maps (Safari before 16.4) NiceGUI's Vue module never loads
  // and its Tailwind build won't parse (SGLMAN-21). The load guard tells the
  // person to update; nothing from such a browser is ours to fix.
  var unsupportedBrowser = !(window.HTMLScriptElement && HTMLScriptElement.supports &&
    HTMLScriptElement.supports('importmap'));

  function ignored(event) {
    var messages = messagesOf(event);
    var urls = urlsOf(event);
    return cfg.builtin.concat(live.rules).some(function (rule) {
      var needle = lower(rule.pattern);
      var haystack = rule.match === 'url' ? urls : messages;
      return haystack.some(function (text) { return text.indexOf(needle) !== -1; });
    });
  }

  Sentry.init({
    dsn: cfg.dsn,
    environment: cfg.environment,
    release: cfg.release || undefined,
    sendDefaultPii: false,
    // Errors whose stack is all someone else's script (an extension, an ad or
    // translation overlay) are not ours to fix.
    allowUrls: [location.origin],
    integrations: [
      Sentry.replayIntegration({ maskAllText: true, maskAllInputs: true, blockAllMedia: true }),
    ],
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: cfg.replay ? 1.0 : 0,
    beforeSend: function (event) {
      if (!live.enabled || unsupportedBrowser) return null;
      if (live.sampleRate < 1 && Math.random() >= live.sampleRate) return null;
      if (ignored(event)) return null;
      if (missingVue(event)) return null;
      return event;
    },
  });
  if (cfg.user) Sentry.setUser(cfg.user);
  Sentry.setTags(cfg.tags || {});

  function replay() {
    return typeof Sentry.getReplay === 'function' ? Sentry.getReplay() : null;
  }

  // The server stamps this id on its own events from this tab, which is what
  // links a failed tap's server error to this tab's recording.
  function reportReplayId() {
    var r = replay();
    var id = r && r.getReplayId && r.getReplayId();
    if (!id || id === reportedReplayId || typeof emitEvent !== 'function' || !window.did_handshake) return;
    try {
      emitEvent(REPLAY_EVENT, { id: id });
      reportedReplayId = id;
    } catch (e) { /* monitoring never breaks the page */ }
  }
  setInterval(reportReplayId, 2000);

  function capture(err, context) {
    if (!(err instanceof Error)) return;
    if (err.__wizSentryCaptured) return;
    try { err.__wizSentryCaptured = true; } catch (e) { /* frozen error */ }
    Sentry.captureException(err, { tags: context });
  }

  // NiceGUI logs, rather than throws, an error raised while it applies a
  // server message (nicegui.js's socket queue), so it would go unseen.
  console.error = function () {
    for (var i = 0; i < arguments.length; i++) {
      if (arguments[i] instanceof Error) {
        capture(arguments[i], { caught_by: 'console' });
        break;
      }
    }
    return originalConsoleError.apply(null, arguments);
  };

  // Vue logs render and slot-template errors to the console and carries on.
  // `app` is NiceGUI's global Vue app, created by the page's module script.
  document.addEventListener('DOMContentLoaded', function () {
    if (typeof app === 'undefined' || !app || !app.config) return;
    app.config.errorHandler = function (err, instance, info) {
      capture(err, { caught_by: 'vue', vue_info: String(info || '') });
      originalConsoleError(err);
    };
  });

  window.__wizSentry = {
    apply: function (next) {
      live.enabled = !!next.enabled;
      live.sampleRate = Number(next.sampleRate);
      live.rules = next.rules || [];
      var r = replay();
      if (!r) return;
      var wantReplay = live.enabled && !!next.replay;
      if (!wantReplay && live.replay) {
        r.stop();
      } else if (wantReplay && !live.replay) {
        r.startBuffering();
      }
      live.replay = wantReplay;
    },
    // A tap failed on the server: upload this tab's buffered recording so the
    // server's issue, which already names this replay id, has it to show.
    flushReplay: function () {
      var r = replay();
      if (!r || !live.enabled || !live.replay) return;
      r.flush({ continueRecording: false })
        .then(function () { return r.startBuffering(); })
        .then(reportReplayId)
        .catch(function () { /* monitoring never breaks the page */ });
    },
  };
})();
