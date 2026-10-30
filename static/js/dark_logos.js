// Dark theme: operator logos are drawn for a white page, and many are dark
// wordmarks that vanish on a dark one. Rather than a white plate, their dark ink
// is lifted in lightness, hue and saturation kept, so a brand keeps its colours:
// black and greys become the page's light text grey, navy a readable blue of the
// same hue, and anything already at mid lightness (a red, a sky blue) is left as
// it is. The stats charts use the same lightening for their axis logos.
(function () {
    function rgbToHsl(r, g, b) {
        r /= 255; g /= 255; b /= 255;
        const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
        if (max === min) return [0, 0, l];
        const d = max - min;
        const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
        const h = max === r ? (g - b) / d + (g < b ? 6 : 0)
            : max === g ? (b - r) / d + 2
            : (r - g) / d + 4;
        return [h / 6, s, l];
    }

    function hslToRgb(h, s, l) {
        if (!s) return [l * 255, l * 255, l * 255];
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
        const channel = t => {
            t = (t + 1) % 1;
            if (t < 1 / 6) return p + (q - p) * 6 * t;
            if (t < 1 / 2) return q;
            if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
            return p;
        };
        return [channel(h + 1 / 3) * 255, channel(h) * 255, channel(h - 1 / 3) * 255];
    }

    // OKLCH: perceptual lightness, chroma and hue. Lifting a dark colour's
    // lightness here keeps it the same colour to the eye; in HSL a dark green
    // drifted to mint and navy to electric blue.
    function srgbToLinear(c) {
        c /= 255;
        return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    }

    function linearToSrgb(c) {
        c = Math.min(1, Math.max(0, c));
        return 255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055);
    }

    function rgbToOklch(r, g, b) {
        r = srgbToLinear(r); g = srgbToLinear(g); b = srgbToLinear(b);
        const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
        const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
        const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
        const L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s;
        const A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s;
        const B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s;
        return [L, Math.hypot(A, B), Math.atan2(B, A)];
    }

    // Chroma is pulled back until the colour fits sRGB; lightness and hue are kept
    function oklchToRgb(L, C, H) {
        let rgb;
        for (let i = 0; i < 24; i++) {
            const A = C * Math.cos(H), B = C * Math.sin(H);
            const l = Math.pow(L + 0.3963377774 * A + 0.2158037573 * B, 3);
            const m = Math.pow(L - 0.1055613458 * A - 0.0638541728 * B, 3);
            const s = Math.pow(L - 0.0894841775 * A - 1.2914855480 * B, 3);
            rgb = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
                   -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
                   -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s];
            if (rgb.every(c => c >= -1e-4 && c <= 1 + 1e-4)) break;
            C *= 0.9;
        }
        return rgb.map(linearToSrgb);
    }

    // Tuned against a few hundred operator logos on the dark page. Only truly dark
    // colours are lifted (OK lightness under 0.45, fully under 0.33): mid tones such
    // as DSB's red or Trenitalia's teal already read on the page and stay as drawn.
    // Greys are lifted from higher up (0.62), a mid grey reading poorly on the page.
    // Greys go to the light text grey, colours to a lighter shade of themselves.
    // Only dark ink over the transparent ground is lifted, as it would sit on the
    // page: dark parts on the logo's own coloured shape (BVG's lettering in its
    // yellow heart) already have something to stand on.
    // White details that lifted ink surrounds (Skyss's dark shield) are inverted, or
    // they would have nothing to stand on; they land on the page's own lightness.
    const INK = {
        dark: 0.45, fade: 0.12, greyDark: 0.62, greyFade: 0.15, neutralChroma: 0.035,
        greyTarget: 0.86, colourTarget: 0.64, keptChroma: 0.95,
        invertHigh: 0.88, invertLow: 0.28
    };

    function lightenDarkInk(ctx, width, height) {
        let image;
        try {
            image = ctx.getImageData(0, 0, width, height);
        } catch (e) {
            return;
        }
        const d = image.data, n = width * height;
        // A logo has few distinct colours, so each is converted once
        const lch = new Array(n), colours = new Map();
        for (let p = 0; p < n; p++) {
            const i = p * 4, key = d[i] << 16 | d[i + 1] << 8 | d[i + 2];
            let c = colours.get(key);
            if (!c) colours.set(key, c = rgbToOklch(d[i], d[i + 1], d[i + 2]));
            lch[p] = c;
        }

        const isGrey = p => lch[p][1] < INK.neutralChroma;
        const darkLimit = p => isGrey(p) ? INK.greyDark : INK.dark;

        // White areas are the logo's paper. One is inverted to the page's colour only
        // when black ink borders most of it: a detail inside a black shape (Skyss's
        // shield, Corsica Ferries' head, a black disc's lettering). Any other white
        // is part of the artwork (P&O's flag, Regio Bahn's lettering on red, a
        // logo's own white plate) and stays, with whatever is drawn on it.
        // A grey bordering a white counts as black ink when its own grey area
        // reaches true black: the anti-aliased edge of a black shape, not the light
        // shading drawn on a white (P&O's flag folds).
        const isWhite = p => d[p * 4 + 3] && isGrey(p) && lch[p][0] >= 0.9;
        const isGreyish = p => d[p * 4 + 3] >= 128 && isGrey(p) && lch[p][0] < 0.9;
        const greyBlack = new Uint8Array(n), greySeen = new Uint8Array(n);
        for (let start = 0; start < n; start++) {
            if (greySeen[start] || !isGreyish(start)) continue;
            const comp = [start];
            greySeen[start] = 1;
            let darkest = lch[start][0];
            for (let k = 0; k < comp.length; k++) {
                const p = comp[k], x = p % width;
                darkest = Math.min(darkest, lch[p][0]);
                const next = [x > 0 ? p - 1 : -1, x < width - 1 ? p + 1 : -1, p - width, p + width];
                for (const q of next) {
                    if (q >= 0 && q < n && !greySeen[q] && isGreyish(q)) {
                        greySeen[q] = 1;
                        comp.push(q);
                    }
                }
            }
            if (darkest < 0.35) comp.forEach(p => { greyBlack[p] = 1; });
        }
        const invertWhite = new Uint8Array(n), seen = new Uint8Array(n);
        for (let start = 0; start < n; start++) {
            if (seen[start] || !isWhite(start)) continue;
            const comp = [start];
            let borderBlack = 0, borderOther = 0;
            seen[start] = 1;
            for (let k = 0; k < comp.length; k++) {
                const p = comp[k], x = p % width;
                const next = [x > 0 ? p - 1 : -1, x < width - 1 ? p + 1 : -1, p - width, p + width];
                for (const q of next) {
                    if (q < 0 || q >= n) {
                        borderOther++;
                    } else if (isWhite(q)) {
                        if (!seen[q]) {
                            seen[q] = 1;
                            comp.push(q);
                        }
                    } else if (greyBlack[q]) {
                        borderBlack++;
                    } else {
                        borderOther++;
                    }
                }
            }
            if (borderBlack > borderOther) comp.forEach(p => { invertWhite[p] = 1; });
        }
        // The anti-aliased grey edge of an inverted white goes with it: left as is,
        // it rings the ink in light grey and holds it dark as a backdrop (MBTA's T)
        const edge = [];
        for (let p = 0; p < n; p++) {
            if (invertWhite[p] || isWhite(p) || !d[p * 4 + 3] || !isGrey(p) || lch[p][0] < INK.greyDark) continue;
            const x = p % width;
            const next = [x > 0 ? p - 1 : -1, x < width - 1 ? p + 1 : -1, p - width, p + width];
            if (next.some(q => q >= 0 && q < n && invertWhite[q])) edge.push(p);
        }
        edge.forEach(p => { invertWhite[p] = 1; });

        // Each dark pixel takes the backdrop nearest to it outside the ink: lifted
        // where that is the transparent ground or a white turned to the page, kept
        // where it is the logo's own colour or white paper. Only something near-opaque
        // is a backdrop: a part-transparent fringe is the anti-aliased edge of the ink.
        const dark = new Uint8Array(n);
        for (let p = 0; p < n; p++) dark[p] = d[p * 4 + 3] && lch[p][0] < darkLimit(p) ? 1 : 0;
        const isDark = p => dark[p] === 1;
        const touchesDark = p => {
            const x = p % width;
            return (x > 0 && dark[p - 1]) || (x < width - 1 && dark[p + 1])
                || (p >= width && dark[p - width]) || (p + width < n && dark[p + width]);
        };
        const LIFT = 1, KEEP = 2;
        const label = new Uint8Array(n), backdropL = new Float64Array(n), queue = [];
        for (let p = 0; p < n; p++) {
            if (isDark(p)) continue;
            // An opaque light grey is no backdrop of its own: it takes what lies
            // beyond it. It is the solid anti-aliased rim of black ink (Skyss's
            // letters) or the shading on a white (P&O's flag)
            if (d[p * 4 + 3] >= 200 && isGrey(p) && lch[p][0] < 0.9 && !invertWhite[p]) continue;
            label[p] = d[p * 4 + 3] >= 200 && !invertWhite[p] ? KEEP : LIFT;
            backdropL[p] = lch[p][0];
            // The backdrop's lightness is the lightest solid colour within two
            // pixels: right against the ink sits its anti-aliased blend with it.
            // Only read where it reaches ink.
            if (label[p] === KEEP && touchesDark(p)) {
                const x0 = p % width, y0 = (p - x0) / width;
                for (let y = Math.max(0, y0 - 2); y < Math.min(height, y0 + 3); y++) {
                    for (let x = Math.max(0, x0 - 2); x < Math.min(width, x0 + 3); x++) {
                        const q = y * width + x;
                        if (d[q * 4 + 3] >= 200 && !isDark(q)) backdropL[p] = Math.max(backdropL[p], lch[q][0]);
                    }
                }
            }
            queue.push(p);
        }
        for (let head = 0; head < queue.length; head++) {
            const p = queue[head], x = p % width;
            const next = [x > 0 ? p - 1 : -1, x < width - 1 ? p + 1 : -1, p - width, p + width];
            for (const q of next) {
                if (q >= 0 && q < n && !label[q]) {
                    label[q] = label[p];
                    backdropL[q] = backdropL[p];
                    queue.push(q);
                }
            }
        }
        // Each connected piece of dark ink (a letter) goes by its majority, so no
        // letter is cut in two where it crosses the edge of a shape (HLB's roof)
        const exposed = new Uint8Array(n), done = new Uint8Array(n);
        for (let start = 0; start < n; start++) {
            if (done[start] || !isDark(start)) continue;
            const comp = [start];
            done[start] = 1;
            for (let k = 0; k < comp.length; k++) {
                const p = comp[k], x = p % width;
                const next = [x > 0 ? p - 1 : -1, x < width - 1 ? p + 1 : -1, p - width, p + width];
                for (const q of next) {
                    if (q >= 0 && q < n && !done[q] && isDark(q)) {
                        done[q] = 1;
                        comp.push(q);
                    }
                }
            }
            // Lifted when most of it is over the transparent ground, or a good share
            // is and the colour it otherwise sits on is not light: light ink still
            // reads on HLB's red, not on Scoot's or BVG's yellow
            const kept = comp.filter(p => label[p] === KEEP);
            const liftedShare = 1 - kept.length / comp.length;
            const onLight = kept.length > 0
                && kept.reduce((sum, p) => sum + backdropL[p], 0) / kept.length >= 0.75;
            if (liftedShare >= 0.5 || (liftedShare >= 0.25 && !onLight)) comp.forEach(p => { exposed[p] = 1; });
        }

        for (let p = 0; p < n; p++) {
            const i = p * 4;
            if (!d[i + 3]) continue;
            const [L, C, H] = lch[p];
            if (L < darkLimit(p) && !exposed[p]) continue;
            const grey = isGrey(p);
            let rgb;
            if (invertWhite[p]) {
                rgb = oklchToRgb(INK.invertHigh - (INK.invertHigh - INK.invertLow) * L, C, H);
            } else {
                const t = Math.min(1, Math.max(0, grey ? (INK.greyDark - L) / INK.greyFade
                                                       : (INK.dark - L) / INK.fade));
                if (!t) continue;
                const target = INK.greyTarget + (INK.colourTarget - INK.greyTarget) * Math.min(1, C / 0.08);
                rgb = oklchToRgb(L + (target - L) * t, C * (1 + (INK.keptChroma - 1) * t), H);
                // A faint grey (a watermark plane) is drawn for a white page, where
                // little opacity shows; on a dark one it needs more. Not the soft
                // edge of a solid shape, whose neighbours are opaque.
                if (grey && d[i + 3] < 128) {
                    const x = p % width;
                    const next = [x > 0 ? p - 1 : -1, x < width - 1 ? p + 1 : -1, p - width, p + width];
                    if (next.every(q => q < 0 || q >= n || d[q * 4 + 3] < 200)) {
                        d[i + 3] = Math.min(153, Math.round(d[i + 3] * 2));
                    }
                }
            }
            d[i] = rgb[0]; d[i + 1] = rgb[1]; d[i + 2] = rgb[2];
        }
        ctx.putImageData(image, 0, 0);
    }

    // Admin-made dark versions by logo file, and by name for the resized copies,
    // whose URL keeps only the file name
    const darkByPath = window.trainlogDarkLogos || {};
    const darkByName = {};
    for (const [path, dark] of Object.entries(darkByPath)) darkByName[path.split('/').pop()] = dark;

    function darkVersion(src) {
        const url = new URL(src, location.href);
        const path = decodeURIComponent(url.pathname);
        if (path.startsWith('/resize_image/')) {
            // The server reads %26 as &, which stats.html's URLs rely on
            const dark = darkByPath[(url.searchParams.get('image_path') || '').replace('%26', '&')];
            return dark && path + '?image_path=' + dark;
        }
        if (path.startsWith('/static/images/resized/')) {
            const [size, name] = path.split('/').slice(4);
            const dark = darkByName[name];
            return dark && `/resize_image/${size.replace('x', '/')}?image_path=${dark}`;
        }
        const dark = path.startsWith('/static/') && darkByPath[path.slice('/static/'.length)];
        return dark && '/static/' + dark;
    }

    // src -> promise of the lightened data URL, or null to keep the original
    const lightened = new Map();

    function lighten(src) {
        if (!lightened.has(src)) {
            lightened.set(src, new Promise(resolve => {
                const img = new Image();
                img.onload = () => {
                    if (!img.naturalWidth) return resolve(null);
                    // Worked at no more than the largest size a logo is shown at
                    // (the admin preview, at double density): the time grows with
                    // the pixel count, and some files are thousands of pixels wide
                    const scale = Math.min(1, 720 / img.naturalWidth, 180 / img.naturalHeight);
                    const canvas = document.createElement('canvas');
                    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
                    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                    lightenDarkInk(ctx, canvas.width, canvas.height);
                    try {
                        resolve(canvas.toDataURL());
                    } catch (e) {
                        resolve(null);
                    }
                };
                img.onerror = () => resolve(null);
                img.src = src;
            }));
        }
        return lightened.get(src);
    }

    window.TrainlogDarkInk = { rgbToHsl, hslToRgb, lightenDarkInk, lighten, darkVersion };

    if (document.documentElement.getAttribute('data-bs-theme') !== 'dark') return;

    // Every operator logo is served from operator_logos/ or its resized/ copies, so
    // matching the path catches them whatever their class; the classes stay for logos
    // that come as data or from elsewhere. .tl-logo marks any other image to treat.
    const LOGOS = 'img[src*="/operator_logos/"], img[src*="/images/resized/"],'
        + ' .tl-logo, .itineraryOperatorLogo, .operatorLogo, .operatorGridLogo, .operatorListLogo, .op-pill img,'
        + ' .op-suggestion-logo, .ticket-logo img, .ticket-table-logo img, .ticket-row-logo,'
        + ' .ticket-operator img, .ticket-pill-logo, .ticket-picker-logo img';

    function process(img) {
        const src = img.getAttribute('src');
        // Trip-type pictograms share .operatorLogo; theme.css inverts those. The stats
        // tooltip shows the logo on a light box of its own, kept as drawn, as are the
        // admin page's light previews (.logo-as-drawn)
        if (!src || src.startsWith('data:') || src.includes('/trip_logos/')
            || src.includes('/operator_logos/dark/') || img.closest('.statsTipMedia, .logo-as-drawn')) return;
        const dark = darkVersion(src);
        if (dark) {
            img.src = dark;
            return;
        }
        lighten(src).then(url => {
            if (url && img.getAttribute('src') === src) img.src = url;
        });
    }

    function scan(root) {
        if (root.matches && root.matches(LOGOS)) process(root);
        if (root.querySelectorAll) root.querySelectorAll(LOGOS).forEach(process);
    }

    function start() {
        scan(document.body);
        // The trips table, pickers and autocompletes add their rows later
        new MutationObserver(mutations => mutations.forEach(m => {
            if (m.type === 'attributes') scan(m.target);
            else m.addedNodes.forEach(node => node.nodeType === 1 && scan(node));
        })).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
