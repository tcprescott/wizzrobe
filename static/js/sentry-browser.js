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

  // Without Vue (or Quasar) the page is dead: the load guard reloads and reports it, so every later error is noise, told apart by text.
  var GUARD_REPORT = 'framework scripts failed to load';

  function missingFramework(event) {
    if (window.Vue && window.Quasar) return false;
    return !messagesOf(event).some(function (text) { return text.indexOf(GUARD_REPORT) !== -1; });
  }

  // Without import maps (Safari before 16.4) NiceGUI's Vue module never loads; the load guard asks the person to update.
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
    // Errors whose stack is all someone else's script (extension, ad, translation overlay) aren't ours.
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
      if (missingFramework(event)) return null;
      return event;
    },
  });
  if (cfg.user) Sentry.setUser(cfg.user);
  Sentry.setTags(cfg.tags || {});

  function replay() {
    return typeof Sentry.getReplay === 'function' ? Sentry.getReplay() : null;
  }

  // The server stamps this id on its own events from this tab, linking a failed tap's server error to this recording.
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

  // NiceGUI logs, rather than throws, errors raised while applying a server message, so they'd go unseen.
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
