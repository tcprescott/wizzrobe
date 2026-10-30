/* Drag a survey question to a new place in the builder.
 *
 * The ⠿ handle (.wiz-survey-drag) inside a question row (.wiz-survey-q with
 * data-qid) starts the drag: at once with a mouse, after a press-and-hold
 * with a finger so a swipe still scrolls the page. The row floats under the
 * pointer and a dashed slot shows where it lands, in its own section or
 * another (.wiz-survey-qlist with data-section). Dropping fires a
 * `survey-drop` DOM event on the builder ({question, section, index}); the
 * server decides and redraws. Escape puts it back.
 *
 * Vue owns the rows, so nothing here moves them: the row only gets a
 * transform, and the slot is a foreign node removed before the event fires.
 * Move up / Move down in the row's ⋮ menu stay the keyboard and no-drag path
 * (WCAG 2.2 SC 2.5.7).
 */
(function () {
  'use strict';
  var HOLD_MS = 400, SLOP = 8, EDGE = 48;
  var press = null, drag = null;

  function rows(list) {
    return Array.prototype.filter.call(list.querySelectorAll('.wiz-survey-q[data-qid]'), function (r) {
      return !drag || r !== drag.row;
    });
  }

  function start() {
    var row = press.row;
    var rect = row.getBoundingClientRect();
    drag = { row: row, startY: press.y + window.scrollY, height: rect.height, list: null, index: -1 };
    drag.slot = document.createElement('div');
    drag.slot.className = 'wiz-survey-slot';
    drag.slot.style.height = rect.height + 'px';
    row.classList.add('is-lifted');
    document.body.classList.add('wiz-survey-dragging');
    if (navigator.vibrate && press.touch) navigator.vibrate(15);
    move(press.x, press.y);
  }

  function target(y) {
    var lists = document.querySelectorAll('.wiz-survey-qlist[data-section]');
    var best = null;
    for (var i = 0; i < lists.length; i++) {
      var r = lists[i].getBoundingClientRect();
      var d = y < r.top ? r.top - y : (y > r.bottom ? y - r.bottom : 0);
      if (!best || d < best.d) best = { list: lists[i], d: d };
    }
    if (!best) return null;
    var others = rows(best.list), index = others.length;
    for (var j = 0; j < others.length; j++) {
      var b = others[j].getBoundingClientRect();
      if (y < b.top + b.height / 2) { index = j; break; }
    }
    return { list: best.list, index: index, before: others[index] || null, after: others[others.length - 1] || null };
  }

  function move(x, y) {
    drag.row.style.transform = 'translateY(' + (y + window.scrollY - drag.startY) + 'px)';
    var t = target(y);
    if (!t) return;
    if (t.list !== drag.list || t.index !== drag.index) {
      drag.list = t.list;
      drag.index = t.index;
      if (t.before) t.before.parentNode.insertBefore(drag.slot, t.before);
      else if (t.after) t.after.parentNode.insertBefore(drag.slot, t.after.nextSibling);
      else t.list.appendChild(drag.slot);
    }
    drag.x = x;
    drag.y = y;
    var step = y < EDGE ? -12 : (y > window.innerHeight - EDGE ? 12 : 0);
    if (step && !drag.scroller) {
      // Keep scrolling while the pointer rests near an edge, not only while it moves.
      drag.scroller = setInterval(function () {
        if (!drag) return;
        var s = drag.y < EDGE ? -12 : (drag.y > window.innerHeight - EDGE ? 12 : 0);
        if (!s) { clearInterval(drag.scroller); drag.scroller = null; return; }
        window.scrollBy(0, s);
        move(drag.x, drag.y);
      }, 16);
    }
  }

  function finish(cancelled) {
    var d = drag;
    drag = null;
    press = null;
    if (!d) return;
    clearInterval(d.scroller);
    if (d.slot.parentNode) d.slot.parentNode.removeChild(d.slot);
    d.row.classList.remove('is-lifted');
    d.row.style.transform = '';
    document.body.classList.remove('wiz-survey-dragging');
    if (cancelled || !d.list) return;
    var from = d.row.closest('.wiz-survey-qlist');
    var was = from ? rows(from).indexOf(d.row) : -1;
    if (from === d.list && was === d.index) return;
    var builder = d.list.closest('.wiz-survey-builder');
    if (!builder) return;
    builder.dispatchEvent(new CustomEvent('survey-drop', {
      bubbles: false,
      detail: { question: +d.row.dataset.qid, section: +d.list.dataset.section, index: d.index },
    }));
  }

  document.addEventListener('pointerdown', function (e) {
    var handle = e.target.closest && e.target.closest('.wiz-survey-drag');
    if (!handle || e.button > 0) return;
    var row = handle.closest('.wiz-survey-q[data-qid]');
    if (!row) return;
    var touch = e.pointerType === 'touch';
    press = { row: row, x: e.clientX, y: e.clientY, touch: touch, id: e.pointerId };
    if (touch) {
      press.timer = setTimeout(function () { if (press) start(); }, HOLD_MS);
    }
    e.preventDefault();
  });

  document.addEventListener('pointermove', function (e) {
    if (!press || e.pointerId !== press.id) return;
    if (drag) { e.preventDefault(); move(e.clientX, e.clientY); return; }
    var far = Math.abs(e.clientY - press.y) > SLOP || Math.abs(e.clientX - press.x) > SLOP;
    if (!far) return;
    if (press.touch) { clearTimeout(press.timer); press = null; return; }
    start();
  }, { passive: false });

  function release(e) {
    if (!press || e.pointerId !== press.id) return;
    clearTimeout(press.timer);
    finish(e.type === 'pointercancel');
    press = null;
  }
  document.addEventListener('pointerup', release);
  document.addEventListener('pointercancel', release);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && drag) { e.stopPropagation(); finish(true); }
  }, true);
})();
