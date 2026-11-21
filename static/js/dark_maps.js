// "Darken map backgrounds": map backgrounds made for a white page, turned dark. Leaflet's raster
// base layer gets this as a CSS filter (theme.css), but MapLibre draws the
// background and the trips on one canvas, where a filter would invert the trips
// too. So for MapLibre the same filter is applied to the style itself: raster
// layers through their paint properties, vector layers by recolouring each colour
// in their paint. Overlays added after the base style (OpenRailwayMap, the trips)
// keep their colours, as they do on Leaflet.
//
// On when the user turned "darken map backgrounds" on (data-dark-maps, set by
// theme_head), whether the rest of the page is light or dark.
(function () {
    // Same chain as theme.css's Leaflet rule:
    // invert(1) hue-rotate(180deg) brightness(0.95) contrast(0.85) saturate(0.6)
    const HUE_180 = [
        [-0.574, 1.430, 0.144],
        [0.426, 0.430, 0.144],
        [0.426, 1.430, -0.856],
    ];
    const SATURATE = (s => [
        [0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s],
        [0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s],
        [0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s],
    ])(0.6);

    const clamp = c => Math.min(1, Math.max(0, c));
    const apply = (m, c) => m.map(row => clamp(row[0] * c[0] + row[1] * c[1] + row[2] * c[2]));

    function filterRgb(rgb) {
        let c = rgb.map(v => 1 - v);
        c = apply(HUE_180, c);
        c = c.map(v => clamp(v * 0.95));
        c = c.map(v => clamp((v - 0.5) * 0.85 + 0.5));
        return apply(SATURATE, c);
    }

    // The raster equivalent. MapLibre runs saturation, contrast and hue first and
    // brightness last; min above max is what inverts.
    const RASTER_PAINT = {
        "raster-brightness-min": 0.95,
        "raster-brightness-max": 0,
        "raster-hue-rotate": 180,
        "raster-contrast": -0.15,
        "raster-saturation": -0.4,
    };

    const NAMED = { white: [1, 1, 1, 1], black: [0, 0, 0, 1] };

    function hslToRgb(h, s, l) {
        h = ((h % 360) + 360) % 360 / 360;
        if (!s) return [l, l, l];
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
        const channel = t => {
            t = (t + 1) % 1;
            if (t < 1 / 6) return p + (q - p) * 6 * t;
            if (t < 1 / 2) return q;
            if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
            return p;
        };
        return [channel(h + 1 / 3), channel(h), channel(h - 1 / 3)];
    }

    // [r, g, b, a] in 0..1, or null for anything that is not a colour
    function parseColor(str) {
        const s = str.trim().toLowerCase();
        if (NAMED[s]) return NAMED[s];
        let m = s.match(/^#([0-9a-f]{3,8})$/);
        if (m) {
            let hex = m[1];
            if (hex.length === 3 || hex.length === 4) hex = hex.replace(/./g, "$&$&");
            if (hex.length !== 6 && hex.length !== 8) return null;
            const n = [0, 2, 4, 6].map(i => i < hex.length ? parseInt(hex.slice(i, i + 2), 16) / 255 : 1);
            return n;
        }
        m = s.match(/^(rgba?|hsla?)\(([^)]*)\)$/);
        if (!m) return null;
        const parts = m[2].split(/[\s,\/]+/).filter(Boolean);
        if (parts.length < 3) return null;
        const num = (p, scale) => p.endsWith("%") ? parseFloat(p) / 100 : parseFloat(p) / scale;
        const a = parts.length > 3 ? num(parts[3], 1) : 1;
        let rgb;
        if (m[1].startsWith("rgb")) {
            rgb = parts.slice(0, 3).map(p => num(p, 255));
        } else {
            rgb = hslToRgb(parseFloat(parts[0]), num(parts[1], 100), num(parts[2], 100));
        }
        if (rgb.some(isNaN) || isNaN(a)) return null;
        return [...rgb, a];
    }

    function darkenColor(str) {
        const c = parseColor(str);
        if (!c) return str;
        const [r, g, b] = filterRgb(c.slice(0, 3)).map(v => Math.round(v * 255));
        return `rgba(${r}, ${g}, ${b}, ${+c[3].toFixed(3)})`;
    }

    // A colour property's value: a plain colour, a legacy {stops: [[z, colour]]}
    // function, or an expression, whose colour literals can sit anywhere in it
    function darkenValue(v) {
        if (typeof v === "string") return darkenColor(v);
        if (Array.isArray(v)) return v.map(darkenValue);
        if (v && typeof v === "object") {
            const out = {};
            for (const [k, x] of Object.entries(v)) out[k] = darkenValue(x);
            return out;
        }
        return v;
    }

    function darkMapsOn() {
        return document.documentElement.hasAttribute("data-dark-maps");
    }

    // Recolours a MapLibre style object in place and returns it
    function darkenMapStyle(style) {
        for (const layer of style.layers || []) {
            if (layer.type === "raster") {
                layer.paint = { ...(layer.paint || {}), ...RASTER_PAINT };
                continue;
            }
            const paint = layer.paint || {};
            for (const key of Object.keys(paint)) {
                if (key.endsWith("-color")) paint[key] = darkenValue(paint[key]);
            }
        }
        return style;
    }

    window.trainlogDarkMapsOn = darkMapsOn;
    window.trainlogDarkenMapStyle = darkenMapStyle;
})();
