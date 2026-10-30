/* iOS Safari (and Android Chrome 108+) leave position:fixed on the layout viewport behind the keyboard, and
   Quasar's iOS scroll lock undoes Safari's pan, so dialogs are pinned to the visual viewport via --wiz-vv-*. */
(function () {
  if (window.__wizDialogKeyboard) return;
  window.__wizDialogKeyboard = true;

  var vv = window.visualViewport;
  if (!vv) return;

  // A keyboard (or its accessory bar) takes well over this; toolbars sliding in and out take less.
  var KEYBOARD_MIN = 120;
  var root = document.documentElement;
  var probe = null;
  var pending = false;
  var open = false;
  var standalone = window.matchMedia && window.matchMedia('(display-mode: standalone)').matches;
  // A toolbar-sized shortfall; anything bigger is a real resize, not the bug.
  var STANDALONE_GAP_MAX = 160;
  var tallest = 0;
  var tallestWidth = 0;
  var gap = 0;

  // innerHeight and clientHeight each disagree with the fixed-layout box on iOS, depending on toolbar state.
  function layoutHeight() {
    if (!probe) {
      probe = document.createElement('div');
      probe.setAttribute('aria-hidden', 'true');
      probe.style.cssText =
        'position:fixed;top:0;bottom:0;left:0;width:0;visibility:hidden;pointer-events:none';
      document.body.appendChild(probe);
    }
    return probe.getBoundingClientRect().height;
  }

  function editable(el) {
    if (!el || !el.matches) return false;
    return el.matches('input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]), textarea, [contenteditable=""], [contenteditable=true]');
  }

  function dialogOpen() {
    return !!document.querySelector('.q-dialog .q-dialog__inner');
  }

  function revealFocused() {
    var el = document.activeElement;
    if (!editable(el) || !el.closest('.q-dialog')) return;
    var field = el.closest('.q-field') || el;
    field.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function apply() {
    pending = false;
    var full = layoutHeight();
    var kb = full - vv.height;
    var up = vv.scale < 1.01 && kb > KEYBOARD_MIN;
    if (standalone) pinStandalone(full, up);
    var next = dialogOpen() && up;
    if (next) {
      var top = Math.max(0, vv.offsetTop);
      var bottom = Math.max(0, full - vv.offsetTop - vv.height);
      root.style.setProperty('--wiz-vv-top', top + 'px');
      root.style.setProperty('--wiz-vv-bottom', bottom + 'px');
      root.style.setProperty('--wiz-vv-height', vv.height + 'px');
    }
    if (next !== open) {
      open = next;
      root.classList.toggle('wiz-kb-open', open);
      if (!open) {
        root.style.removeProperty('--wiz-vv-top');
        root.style.removeProperty('--wiz-vv-bottom');
        root.style.removeProperty('--wiz-vv-height');
      }
    }
    if (open) requestAnimationFrame(revealFocused);
  }

  function pinStandalone(full, up) {
    if (window.innerWidth !== tallestWidth) {
      tallestWidth = window.innerWidth;
      tallest = 0;
    }
    tallest = Math.max(tallest, full);
    var short = tallest - full;
    var next = !up && short > 0 && short <= STANDALONE_GAP_MAX ? short : 0;
    if (next === gap) return;
    gap = next;
    if (gap) root.style.setProperty('--wiz-standalone-gap', gap + 'px');
    else root.style.removeProperty('--wiz-standalone-gap');
  }

  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(apply);
  }

  vv.addEventListener('resize', schedule);
  vv.addEventListener('scroll', schedule);
  if (standalone) window.addEventListener('scroll', schedule, { passive: true });
  // Focus can move between fields with the keyboard up, and a dialog can close with it up; neither resizes the viewport.
  document.addEventListener('focusin', schedule);
  document.addEventListener('focusout', function () { setTimeout(schedule, 0); });
  new MutationObserver(function () { if (open || dialogOpen()) schedule(); })
    .observe(document.body, { childList: true });
})();
