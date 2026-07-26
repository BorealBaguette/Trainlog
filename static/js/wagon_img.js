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
 *   sits on one absolute metres-per-pixel scale. Images without data-ppm are treated as
 *   `fallbackPpm` px/m (default 10, measured from real stock) so they ALSO get one absolute
 *   scale — the same wagon is the same size in every strip, regardless of its stripmates.
 *   Set fallbackPpm:0 to restore
 *   the legacy per-strip median normalization (needed only for mixed-source px/m):
 *       displayed = target * (naturalHeight / median) ^ gamma
 *   Both paths are clamped to [min, max]. gamma defaults to 1 = ONE uniform scale factor
 *   for the whole strip (target/median). Keep it there: a uniform factor bottom-aligns every
 *   car's body while taller artwork (e.g. a raised pantograph) simply protrudes above. gamma<1
 *   compresses the spread but, because it scales each image by its total bounding box, it also
 *   shrinks the BODY of any image with roof equipment out of line with its neighbours. The
 *   median reference already normalizes absolute scale BETWEEN strips.
 *   Dimensions are read at runtime (naturalHeight) and it re-runs as images load.
 *   opts: { target=30, gamma=1, min=0, max=Infinity, refMeters=4, fallbackPpm=10,
 *           selector='img', skipClass='wagon-placeholder-img', onApply } — refMeters is the
 *   real height (metres) a target-sized wagon represents; fallbackPpm is the assumed px/m for
 *   uncalibrated images (0 = use median instead); placeholderPx (default 44) is the art-height
 *   the "not found" SVGs represent, sized at the same absolute scale as real cars (their own
 *   naturalHeight is unreliable). onApply fires after each (re)size, e.g. to re-fit a container.
 *   In legacy median mode (fallbackPpm:0), placeholders keep their CSS size as before.
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
    var gamma    = opts.gamma     != null ? opts.gamma     : 1;
    var minH     = opts.min       != null ? opts.min       : 0;
    var maxH     = opts.max       != null ? opts.max       : Infinity;
    var refM     = opts.refMeters != null ? opts.refMeters : 4;
    // Assumed px-per-metre for images WITHOUT a data-ppm attribute. The MLG wagon
    // collection is drawn at a consistent scale, so treating uncalibrated images at that
    // scale draws every wagon at ONE absolute size — the same wagon is the same size in
    // every strip. 10 px/m is measured from real stock: SNCB MW41 248px/24.80m = 10.0,
    // BLS RABe 528.1 42px/4.26m = 9.86. Set fallbackPpm:0 to restore per-strip median.
    var fbPpm    = opts.fallbackPpm != null ? opts.fallbackPpm : 10;
    // The "not found" placeholder SVGs are authored with a viewBox height of 44 (≈ a
    // 4.4 m car body at fbPpm). Their <img> naturalHeight is unreliable (viewBox but a
    // width attr and no height), so size them from this fixed art-height instead, at the
    // same absolute scale as real cars — so a placeholder sits right next to real wagons.
    var phPx     = opts.placeholderPx != null ? opts.placeholderPx : 44;
    var onApply  = opts.onApply;
    var screenScale = target / refM;   // on-screen px per real metre, for calibrated imgs

    var imgs = Array.prototype.slice.call(container.querySelectorAll(selector));
    if (!imgs.length) return;

    function sizeImg(img, h) {
      h = Math.max(minH, Math.min(maxH, h));
      img.style.height    = Math.round(h) + 'px';
      img.style.width     = 'auto';
      img.style.maxHeight = 'none';
      img.style.maxWidth  = 'none';
    }

    function apply() {
      // Re-filter each pass: an image that failed to load may have been turned into
      // a placeholder (skip class) since the last run.
      var active = imgs.filter(function (img) {
        return !img.classList.contains(skip) && img.naturalHeight > 0;
      });

      // Placeholders → generic wagon at the same absolute scale (skipped in legacy
      // median mode, where they keep their CSS size).
      if (fbPpm > 0) {
        var phH = (phPx / fbPpm) * screenScale;
        imgs.forEach(function (img) {
          if (img.classList.contains(skip)) sizeImg(img, phH);
        });
      }

      if (active.length) {
        var heights = active.map(function (img) { return img.naturalHeight; })
                            .sort(function (a, b) { return a - b; });
        var mid = Math.floor(heights.length / 2);
        var median = heights.length % 2 ? heights[mid]
                                        : (heights[mid - 1] + heights[mid]) / 2;
        if (median) {
          active.forEach(function (img) {
            var ppm = parseFloat(img.getAttribute('data-ppm'));
            if (!(ppm > 0) && fbPpm > 0) ppm = fbPpm;   // assume a default scale when uncalibrated
            var h = ppm > 0 ? (img.naturalHeight / ppm) * screenScale        // true real-world scale
                            : target * Math.pow(img.naturalHeight / median, gamma); // legacy median
            sizeImg(img, h);
          });
        }
      }
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
