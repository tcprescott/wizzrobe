/* Drawn in-app: a Home Screen web app on an iPhone opens a PDF full screen with no Back button. */
(function () {
  if (window.__wizGuideViewer) return;
  window.__wizGuideViewer = true;

  var SAFE_LINK = /^(https?:|mailto:|tel:)/i;

  function fail(box) {
    box.querySelector('.wiz-guide-status').textContent =
      "The guide didn't load here. Use Download PDF above to open it.";
  }

  async function render(box, pdf, width) {
    var pages = box.querySelector('.wiz-guide-page-list');
    pages.textContent = '';
    var scaleFor = null;
    for (var n = 1; n <= pdf.numPages; n++) {
      var page = await pdf.getPage(n);
      var base = page.getViewport({ scale: 1 });
      var viewport = page.getViewport({ scale: width / base.width });
      var ratio = Math.min(window.devicePixelRatio || 1, 2);

      var frame = document.createElement('div');
      frame.className = 'wiz-guide-page';
      frame.style.width = viewport.width + 'px';
      frame.style.height = viewport.height + 'px';
      frame.setAttribute('role', 'img');
      frame.setAttribute('aria-label', 'Page ' + n + ' of ' + pdf.numPages);

      var canvas = document.createElement('canvas');
      canvas.width = Math.floor(viewport.width * ratio);
      canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = viewport.width + 'px';
      canvas.style.height = viewport.height + 'px';
      frame.appendChild(canvas);
      pages.appendChild(frame);

      await page.render({
        canvasContext: canvas.getContext('2d'),
        viewport: viewport,
        transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null,
      }).promise;

      var annotations = await page.getAnnotations();
      annotations.forEach(function (a) {
        if (a.subtype !== 'Link' || !a.url || !SAFE_LINK.test(a.url)) return;
        var r = viewport.convertToViewportRectangle(a.rect);
        var link = document.createElement('a');
        link.className = 'wiz-guide-link';
        link.href = a.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.setAttribute('aria-label', a.url);
        link.style.left = Math.min(r[0], r[2]) + 'px';
        link.style.top = Math.min(r[1], r[3]) + 'px';
        link.style.width = Math.abs(r[2] - r[0]) + 'px';
        link.style.height = Math.abs(r[3] - r[1]) + 'px';
        frame.appendChild(link);
      });
      scaleFor = width;
    }
    return scaleFor;
  }

  async function start(box) {
    box.dataset.started = '1';
    var status = box.querySelector('.wiz-guide-status');
    try {
      var pdfjs = await import(box.dataset.lib);
      pdfjs.GlobalWorkerOptions.workerSrc = box.dataset.worker;
      var pdf = await pdfjs.getDocument({ url: box.dataset.src, isEvalSupported: false }).promise;
      var drawnAt = await render(box, pdf, box.clientWidth);
      status.remove();

      var timer = null;
      window.addEventListener('resize', function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          if (!document.body.contains(box)) return;
          var width = box.clientWidth;
          if (Math.abs(width - drawnAt) < 24) return;
          render(box, pdf, width).then(function (w) { drawnAt = w; });
        }, 250);
      });
    } catch (e) {
      console.warn('Event guide viewer:', e);
      fail(box);
    }
  }

  function scan() {
    document.querySelectorAll('.wiz-guide-pages:not([data-started])').forEach(start);
  }

  new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
  scan();
})();
