// Admin → Stream Layouts: drag, resize and nudge the boxes on a layout's art.
//
// The canvas carries its layout's size in data-w / data-h, and each box its
// position in that space, so a move is reported in the art's own pixels
// whatever size the canvas is drawn at. The server owns the boxes: a finished
// drag emits `sl_box_changed`, a plain tap `sl_box_selected`, and the page
// repaints from what it stored. Arrow keys move a focused box (Shift for 10).
(() => {
  if (window.__wizStreamLayoutEditor) return;
  window.__wizStreamLayoutEditor = true;
  const SNAP = 4;
  const MIN = 4;
  let drag = null;

  const num = (el, key) => Number(el.dataset[key]);
  const snap = v => Math.round(v / SNAP) * SNAP;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  function place(box, cv, x, y, w, h) {
    const W = num(cv, 'w'), H = num(cv, 'h');
    Object.assign(box.style, {
      left: (x / W * 100) + '%', top: (y / H * 100) + '%',
      width: (w / W * 100) + '%', height: (h / H * 100) + '%',
    });
    Object.assign(box.dataset, {x, y, w, h});
  }

  function report(box) {
    emitEvent('sl_box_changed', {
      id: box.dataset.id, x: num(box, 'x'), y: num(box, 'y'), w: num(box, 'w'), h: num(box, 'h'),
    });
  }

  document.addEventListener('pointerdown', e => {
    const box = e.target.closest('.sl-box');
    const cv = box && box.closest('.sl-canvas');
    if (!cv) return;
    const rect = cv.getBoundingClientRect();
    drag = {
      box, cv, id: e.pointerId, moved: false,
      resize: !!e.target.closest('.sl-handle'),
      sx: e.clientX, sy: e.clientY, k: num(cv, 'w') / rect.width,
      x: num(box, 'x'), y: num(box, 'y'), w: num(box, 'w'), h: num(box, 'h'),
    };
    box.setPointerCapture(e.pointerId);
    e.preventDefault();
  });

  document.addEventListener('pointermove', e => {
    if (!drag || e.pointerId !== drag.id) return;
    const dx = (e.clientX - drag.sx) * drag.k, dy = (e.clientY - drag.sy) * drag.k;
    if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 3 * drag.k) return;
    drag.moved = true;
    const W = num(drag.cv, 'w'), H = num(drag.cv, 'h');
    if (drag.resize) {
      const w = clamp(snap(drag.w + dx), MIN, W - drag.x);
      const h = clamp(snap(drag.h + dy), MIN, H - drag.y);
      place(drag.box, drag.cv, drag.x, drag.y, w, h);
    } else {
      const x = clamp(snap(drag.x + dx), 0, W - drag.w);
      const y = clamp(snap(drag.y + dy), 0, H - drag.h);
      place(drag.box, drag.cv, x, y, drag.w, drag.h);
    }
  });

  function end(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const {box, moved} = drag;
    drag = null;
    if (moved) report(box);
    else emitEvent('sl_box_selected', {id: box.dataset.id});
  }
  document.addEventListener('pointerup', end);
  document.addEventListener('pointercancel', end);

  document.addEventListener('keydown', e => {
    const box = e.target.closest && e.target.closest('.sl-box');
    const cv = box && box.closest('.sl-canvas');
    if (!cv) return;
    if (e.key === 'Enter' || e.key === ' ') {
      emitEvent('sl_box_selected', {id: box.dataset.id});
      e.preventDefault();
      return;
    }
    const step = e.shiftKey ? 10 : 1;
    const d = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]}[e.key];
    if (!d) return;
    e.preventDefault();
    const W = num(cv, 'w'), H = num(cv, 'h'), w = num(box, 'w'), h = num(box, 'h');
    place(box, cv, clamp(num(box, 'x') + d[0], 0, W - w), clamp(num(box, 'y') + d[1], 0, H - h), w, h);
    clearTimeout(box._slTimer);
    box._slTimer = setTimeout(() => report(box), 250);
  });
})();
