/* Records the *name* of what was used, never what was typed or selected. */
(function () {
  if (window.__wizUsage) return;
  window.__wizUsage = true;

  var MAX_TEXT = 80;
  var FLUSH_MS = 5000;
  var BATCH = 50;
  var buffer = [];
  var openDialogs = new Map();
  var lastMenuOpener = null;

  var CONTROL = [
    'button', 'a[href]', '[role=button]', '[role=menuitem]', '[role=tab]',
    '[role=option]', '[role=switch]', '[role=checkbox]', '[role=radio]',
    '.q-btn', '.q-item--clickable', '.q-tab', '.q-toggle', '.q-checkbox',
    '.q-radio', '.q-chip--clickable', '.q-select',
  ].join(',');

  function clean(text) {
    text = (text || '').replace(/\s+/g, ' ').trim();
    return text.length > MAX_TEXT ? text.slice(0, MAX_TEXT) : text;
  }

  function device() {
    var w = window.innerWidth;
    return w < 600 ? 'phone' : w < 1024 ? 'tablet' : 'desktop';
  }

  function path() {
    return location.pathname.replace(/^\/t\/[^/]+/, '').replace(/\/\d+(?=\/|$)/g, '/{id}') || '/';
  }

  function fieldLabel(el) {
    var field = el.closest('.q-field');
    var label = field && field.querySelector('.q-field__label');
    var input = field ? field.querySelector('input, textarea') : el;
    return clean(
      (label && label.textContent.replace(/\s*\*\s*$/, '')) ||
      (input && (input.getAttribute('aria-label') || input.getAttribute('placeholder'))) ||
      el.getAttribute('aria-label') || ''
    );
  }

  function kindOf(el) {
    if (el.matches('.q-select')) return 'select';
    if (el.matches('.q-toggle, [role=switch]')) return 'toggle';
    if (el.matches('.q-checkbox, [role=checkbox]')) return 'checkbox';
    if (el.matches('.q-radio, [role=radio]')) return 'radio';
    if (el.matches('.q-tab, [role=tab]')) return 'tab';
    if (el.closest('.q-menu')) return el.matches('[role=option]') ? 'option' : 'menu-item';
    if (el.matches('.q-chip--clickable')) return 'chip';
    if (el.matches('[role=option]')) return 'option';
    if (el.matches('.q-item--clickable')) return 'item';
    if (el.matches('a[href]')) return 'link';
    return 'button';
  }

  function nameOf(el, kind) {
    if (kind === 'select') return fieldLabel(el);
    var aria = el.getAttribute('aria-label');
    if (aria) return clean(aria);
    if (el.matches('.q-btn')) {
      // A labelled button's text includes its icon ligature; the label span alone is the name.
      var label = el.querySelector('.q-btn__content .block');
      if (label) return clean(label.textContent);
    }
    if (el.matches('.q-item--clickable, [role=menuitem], [role=option]')) {
      var main = el.querySelector('.q-item__label');
      if (main) return clean(main.textContent);
    }
    if (el.matches('.q-toggle, .q-checkbox, .q-radio')) {
      var inner = el.querySelector('.q-toggle__label, .q-checkbox__label, .q-radio__label');
      if (inner) return clean(inner.textContent);
    }
    return clean(el.textContent || el.getAttribute('title') || '');
  }

  function dialogTitle(dialog) {
    var title = dialog.querySelector('h1, h2, h3, .dialog-header .text-h6, .text-h6, .section-title');
    var labelledBy = dialog.getAttribute('aria-labelledby');
    var labelEl = labelledBy && document.getElementById(labelledBy);
    return clean(
      (title && title.textContent) || dialog.getAttribute('aria-label') ||
      (labelEl && labelEl.textContent) || ''
    );
  }

  function isPicker(dialog) {
    return !!dialog.querySelector('.q-select__dialog');
  }

  function pickerName(dialog, opener) {
    var label = dialog.querySelector('.q-select__dialog .q-field__label');
    return clean((label && label.textContent.replace(/\s*\*\s*$/, '')) || '') || opener || 'Dropdown';
  }

  function push(event) {
    if (!event.target) return;
    event.path = path();
    event.device = device();
    event.width = window.innerWidth;
    buffer.push(event);
    if (buffer.length >= BATCH) flush();
  }

  function flush() {
    if (!buffer.length || typeof emitEvent !== 'function') return;
    var events = buffer.splice(0, BATCH);
    try { emitEvent('wiz_usage', { events: events }); } catch (e) { /* telemetry never breaks the page */ }
  }

  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest && e.target.closest(CONTROL);
    if (!el) return;
    var kind = kindOf(el);
    var name = nameOf(el, kind);
    var table = el.closest('[data-wiz-table-key]');
    // Drop the row from the name so every row's ⋮ counts as one control.
    if (table) name = name.replace(/ for .*$/, '');
    if (!name) return;
    var event = { type: 'ui.click', kind: kind, target: name };
    var dialog = el.closest('[role=dialog]');
    var info = dialog && openDialogs.get(dialog);
    var inPicker = !!dialog && (info ? info.picker : isPicker(dialog));
    if (inPicker) {
      event.menu = info ? info.title : pickerName(dialog, lastMenuOpener);
    } else if (dialog) {
      event.dialog = info ? info.title : dialogTitle(dialog);
      if (info) info.lastClick = name;
    }
    if (el.closest('.q-menu')) {
      if (lastMenuOpener) event.menu = lastMenuOpener;
    } else if (!inPicker) {
      lastMenuOpener = name;
    }
    if (table) event.table = table.getAttribute('data-wiz-table-key');
    push(event);
  }, true);

  document.addEventListener('change', function (e) {
    var el = e.target;
    if (!el || !el.matches || !el.matches('input, textarea, select')) return;
    if (el.matches('[type=checkbox], [type=radio]')) return;
    var name = fieldLabel(el);
    if (!name) return;
    var event = { type: 'ui.field_used', kind: el.getAttribute('type') || el.tagName.toLowerCase(), target: name };
    var dialog = el.closest('[role=dialog]');
    if (dialog && !isPicker(dialog)) event.dialog = (openDialogs.get(dialog) || {}).title || dialogTitle(dialog);
    push(event);
  }, true);

  function opened(dialog) {
    if (openDialogs.has(dialog)) return;
    var info = { title: '', lastClick: null, picker: false };
    var opener = lastMenuOpener;
    openDialogs.set(dialog, info);
    // Content is rendered a tick after the dialog shell.
    setTimeout(function () {
      if (isPicker(dialog)) {
        info.picker = true;
        info.title = pickerName(dialog, opener);
        return;
      }
      info.title = dialogTitle(dialog) || '(untitled dialog)';
      push({ type: 'ui.dialog_opened', target: info.title });
    }, 50);
  }

  function closed(dialog) {
    var info = openDialogs.get(dialog);
    if (!info) return;
    openDialogs.delete(dialog);
    if (info.picker || isPicker(dialog)) return;
    push({ type: 'ui.dialog_closed', target: info.title || '(untitled dialog)', via: info.lastClick || 'dismissed' });
  }

  new MutationObserver(function (records) {
    records.forEach(function (r) {
      r.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        var d = n.matches('[role=dialog]') ? n : n.querySelector('[role=dialog]');
        if (d) opened(d);
      });
      r.removedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        openDialogs.forEach(function (_, d) {
          if (d === n || n.contains(d)) closed(d);
        });
      });
    });
  }).observe(document.body, { childList: true, subtree: true });

  setInterval(flush, FLUSH_MS);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') flush();
  });
  window.addEventListener('pagehide', flush);
})();
