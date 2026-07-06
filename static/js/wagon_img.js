/* Shared wagon-image helpers, used by the trainset builder, the builder modal,
 * the read-only trainset display, and the admin wagons page. Loading this file more
 * than once on a page is harmless — it only (re)defines two idempotent window functions.
 *
 * wagonImgSrc(base, unit, side)
 *   Build the image URL for a wagon, honouring both its side layout (image_type) and
 *   its file format (image_ext, 'gif' | 'png', defaulting to 'gif'). `unit` is any object
 *   with { image, image_type, image_ext }. `side` ('L' | 'R') overrides unit._side.
 *
 * normalizeStrip(container, opts)
 *   Size a row of wagon images. An image carrying a `data-ppm` (pixels-per-metre)
 *   attribute is drawn to TRUE scale — its real height (naturalHeight / ppm) times a
 *   shared on-screen scale (target / refMeters), so every calibrated wagon everywhere
 *   sits on one absolute metres-per-pixel scale. Images without data-ppm fall back to
 *   the strip's OWN median height, so a source's arbitrary pixels-per-metre cancels out:
 *       displayed = target * (naturalHeight / median) ^ gamma
 *   Both paths are clamped to [min, max]. gamma in [0,1] controls how much real height
 *   matters for the fallback: 1 = fully proportional, 0 = every unit identical,
 *   ~0.4 = double-deckers still read as taller but outliers are compressed.
 *   Dimensions are read at runtime (naturalHeight) and it re-runs as images load.
 *   opts: { target=30, gamma=0.4, min=0, max=Infinity, refMeters=4, selector='img',
 *           skipClass='wagon-placeholder-img', onApply } — refMeters is the real height
 *   (metres) a target-sized wagon represents, tying data-ppm scale to the median scale;
 *   onApply fires after each (re)size, e.g. to re-fit a container whose height changed.
 *   Placeholders (skipClass) are excluded from the median and left at their own size.
 */
(function () {
  function wagonImgSrc(base, unit, side) {
    if (!unit || !unit.image) return '';
    var ext = unit.image_ext || 'gif';
    var s = side || unit._side || 'L';
    var t = unit.image_type;
    if (t === 'sides')   return base + '/' + unit.image + (s === 'R' ? '_R' : '_L') + '.' + ext;
    if (t === 'sides_L') return base + '/' + unit.image + '_L.' + ext;
    if (t === 'sides_R') return base + '/' + unit.image + '_R.' + ext;
    return base + '/' + unit.image + '.' + ext;
  }

  function normalizeStrip(container, opts) {
    opts = opts || {};
    if (!container) return;
    var selector = opts.selector || 'img';
    var skip     = opts.skipClass || 'wagon-placeholder-img';
    var target   = opts.target    != null ? opts.target    : 30;
    var gamma    = opts.gamma     != null ? opts.gamma     : 0.4;
    var minH     = opts.min       != null ? opts.min       : 0;
    var maxH     = opts.max       != null ? opts.max       : Infinity;
    var refM     = opts.refMeters != null ? opts.refMeters : 4;
    var onApply  = opts.onApply;
    var screenScale = target / refM;   // on-screen px per real metre, for calibrated imgs

    var imgs = Array.prototype.slice.call(container.querySelectorAll(selector));
    if (!imgs.length) return;

    function apply() {
      // Re-filter each pass: an image that failed to load may have been turned into
      // a placeholder (skip class) since the last run.
      var active = imgs.filter(function (img) {
        return !img.classList.contains(skip) && img.naturalHeight > 0;
      });
      if (!active.length) return;

      var heights = active.map(function (img) { return img.naturalHeight; })
                          .sort(function (a, b) { return a - b; });
      var mid = Math.floor(heights.length / 2);
      var median = heights.length % 2 ? heights[mid]
                                      : (heights[mid - 1] + heights[mid]) / 2;
      if (!median) return;

      active.forEach(function (img) {
        var ppm = parseFloat(img.getAttribute('data-ppm'));
        var h;
        if (ppm > 0) {
          h = (img.naturalHeight / ppm) * screenScale;   // true real-world scale
        } else {
          h = target * Math.pow(img.naturalHeight / median, gamma);
        }
        h = Math.max(minH, Math.min(maxH, h));
        img.style.height    = Math.round(h) + 'px';
        img.style.width     = 'auto';
        img.style.maxHeight = 'none';
        img.style.maxWidth  = 'none';
      });
      if (onApply) onApply();
    }

    imgs.forEach(function (img) {
      if (img.complete && img.naturalHeight) return;
      img.addEventListener('load', apply);
    });
    apply();
  }

  window.wagonImgSrc = wagonImgSrc;
  window.normalizeStrip = normalizeStrip;
})();
