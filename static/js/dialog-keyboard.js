/*
 * Keep an open dialog above the on-screen keyboard.
 *
 * iOS Safari (and Chrome on Android since 108) don't shrink the layout
 * viewport when the keyboard opens; only the visual viewport shrinks, and
 * position:fixed elements stay anchored to the layout viewport behind the
 * keyboard. Safari normally pans the page to reveal the focused field, but
 * Quasar's iOS scroll lock (preventScroll's onAppleResize) scrolls the page
 * back while a dialog is open, so a bottom sheet's fields stayed under the
 * keyboard.
 *
 * While the keyboard is up this pins each dialog to the visual viewport via
 * --wiz-vv-top / --wiz-vv-bottom / --wiz-vv-height on <html> (the
 * html.wiz-kb-open rules in styles.css read them), then scrolls the focused
 * field into view inside the dialog card.
 *
 * It also nudges the page when the keyboard goes away, dialog or not. iOS,
 * the home screen app above all, can leave bottom-fixed bars (the tab bar)
 * parked where the keyboard's top edge was until the next scroll; a one-pixel
 * scroll and back makes WebKit lay them out against the real bottom again.
 */
(function () {
  if (window.__wizDialogKeyboard) return;
  window.__wizDialogKeyboard = true;

  var vv = window.visualViewport;
  if (!vv) return;

  // A keyboard (or its accessory bar) takes well over this; browser toolbars
  // sliding in and out take less.
  var KEYBOARD_MIN = 120;
  var root = document.documentElement;
  var probe = null;
  var pending = false;
  var open = false;
  var keyboardUp = false;

  // The box a position:fixed element is laid out against. innerHeight and
  // clientHeight each disagree with it on iOS depending on toolbar state.
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
    if (keyboardUp && !up) {
      var y = window.scrollY;
      window.scrollTo(window.scrollX, y + 1);
      window.scrollTo(window.scrollX, y);
    }
    keyboardUp = up;
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

  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(apply);
  }

  vv.addEventListener('resize', schedule);
  vv.addEventListener('scroll', schedule);
  // The keyboard can already be up when focus moves between fields, and a
  // dialog can close with it still up; neither resizes the viewport.
  document.addEventListener('focusin', schedule);
  document.addEventListener('focusout', function () { setTimeout(schedule, 0); });
  new MutationObserver(function () { if (open || dialogOpen()) schedule(); })
    .observe(document.body, { childList: true });
})();
