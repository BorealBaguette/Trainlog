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

    // The lift fades in between lightness 0.42 and 0.27, so anti-aliased edges
    // follow smoothly; greys go furthest, strong colours stop short of pastel.
    // A logo drawn mainly in black and grey (Skyss, whose dark shield carries white
    // details) has its greys inverted instead, white turning to a dark grey near the
    // page's, or lifting the black would leave the white parts nothing to stand on.
    // Logos without dark grey ink keep their whites: white on a coloured flag stays.
    function lightenDarkInk(ctx, width, height) {
        let image;
        try {
            image = ctx.getImageData(0, 0, width, height);
        } catch (e) {
            return;
        }
        const d = image.data;
        let opaque = 0, darkNeutral = 0;
        for (let i = 0; i < d.length; i += 4) {
            if (d[i + 3] < 128) continue;
            opaque++;
            const [, sat, l] = rgbToHsl(d[i], d[i + 1], d[i + 2]);
            if (sat < 0.2 && l < 0.35) darkNeutral++;
        }
        const invertNeutrals = opaque && darkNeutral / opaque >= 0.1;
        for (let i = 0; i < d.length; i += 4) {
            if (!d[i + 3]) continue;
            const [h, sat, l] = rgbToHsl(d[i], d[i + 1], d[i + 2]);
            let rgb;
            if (invertNeutrals && sat < 0.2) {
                rgb = hslToRgb(h, sat, 0.84 - 0.7 * l);
            } else {
                const t = Math.min(1, Math.max(0, (0.42 - l) / 0.15));
                if (!t) continue;
                const target = 0.84 - 0.22 * sat;
                rgb = hslToRgb(h, sat, l + (target - l) * t);
            }
            d[i] = rgb[0]; d[i + 1] = rgb[1]; d[i + 2] = rgb[2];
        }
        ctx.putImageData(image, 0, 0);
    }

    window.TrainlogDarkInk = { rgbToHsl, hslToRgb, lightenDarkInk };

    if (document.documentElement.getAttribute('data-bs-theme') !== 'dark') return;

    // Every operator logo is served from operator_logos/ or its resized/ copies, so
    // matching the path catches them whatever their class; the classes stay for logos
    // that come as data or from elsewhere. .tl-logo marks any other image to treat.
    const LOGOS = 'img[src*="/operator_logos/"], img[src*="/images/resized/"],'
        + ' .tl-logo, .itineraryOperatorLogo, .operatorLogo, .operatorGridLogo, .operatorListLogo, .op-pill img,'
        + ' .op-suggestion-logo, .ticket-logo img, .ticket-table-logo img, .ticket-row-logo,'
        + ' .ticket-operator img, .ticket-pill-logo, .ticket-picker-logo img';

    // src -> promise of the lightened data URL, or null to keep the original
    const lightened = new Map();

    function lighten(src) {
        if (!lightened.has(src)) {
            lightened.set(src, new Promise(resolve => {
                const img = new Image();
                img.onload = () => {
                    if (!img.naturalWidth) return resolve(null);
                    const canvas = document.createElement('canvas');
                    canvas.width = img.naturalWidth;
                    canvas.height = img.naturalHeight;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0);
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

    function process(img) {
        const src = img.getAttribute('src');
        // Trip-type pictograms share .operatorLogo; theme.css inverts those. The stats
        // tooltip shows the logo on a light box of its own, kept as drawn
        if (!src || src.startsWith('data:') || src.includes('/trip_logos/')
            || img.closest('.statsTipMedia')) return;
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
