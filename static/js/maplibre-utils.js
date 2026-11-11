// maplibre-utils.js - Reusable MapLibre utilities for TrainLog with Vector Tiles

// Global configuration
const MapConfig = {
    transportTypes: [
        { id: 'train', icon: 'fa-train', color: '#52b0fe' },
        { id: 'tram', icon: 'fa-train-tram', color: '#a2d7ff' },
        { id: 'metro', icon: 'fa-train-subway', color: '#004595' },
        { id: 'bus', icon: 'fa-bus', color: '#9f4bbb' },
        { id: 'car', icon: 'fa-car-side', color: '#a68fcd' },
        { id: 'cycle', icon: 'fa-bicycle', color: '#6e211a' },
        { id: 'scooter', icon: 'bi-scooter', iconClass: 'bi', color: '#00d084' },
        { id: 'funicular', icon: 'fa-mountain', color: '#6495ed' },
        { id: 'rail', icon: 'fa-dumbbell', color: '#7ec8ff' },
        { id: 'ski', icon: 'fa-person-skiing', color: '#b8e6f0' },
        { id: 'walk', icon: 'fa-person-hiking', color: '#e88c00' },
        { id: 'air', icon: 'fa-plane', color: '#40b91f' },
        { id: 'ferry', icon: 'fa-ship', color: '#1e1e7c' },
        { id: 'aerialway', icon: 'fa-cable-car', color: '#afcf3b' },
        { id: 'other', icon: 'fa-circle-question', color: '#000000' }
    ],
    // Vertical exaggeration for 3D flight altitude. Real cruise (~11 km) over a
    // ~1000 km route is ~1% of the route length and barely visible, so the stored
    // (true-metre) altitude is multiplied by this at render time only.
    altitudeExaggeration: 1,
    jawgAllowedLangs: ["de", "en", "es", "fr", "it", "ja", "ko", "nl", "ru", "zh"],
    vectorStylePaths: {
        "jawg-streets-v2":'/getVectorStyle/{language}/jawg-streets.json',
        "jawg-lagoon-v2":'/getVectorStyle/{language}/jawg-lagoon.json',
        "trainlog-lagoon-v2":'/getVectorStyle/{language}/trainlog-lagoon.json',
        "dark-train":       '/getVectorStyle/{language}/trainlog-dark.json',
        "trainlog-rail":    '/getVectorStyle/{language}/trainlog-rail.json',
        "trainlog-light":   '/getVectorStyle/{language}/trainlog-light.json',
        "ofm-liberty": 'https://tiles.openfreemap.org/styles/liberty',
        "ofm-bright": 'https://tiles.openfreemap.org/styles/bright',
        "ofm-positron": 'https://tiles.openfreemap.org/styles/positron'
    }
};

// Resolves a tileserver value (incl. orm-vector-<type>.<base>) into a MapLibre style
// Map labels default to the user's map language setting (window.MAP_LANG, set by the layouts)
async function resolveBackground(tileserver, { styleUrl = null, userLanguage = window.MAP_LANG || 'en', useGlobe = false } = {}) {
    let mapStyle;

    // Parse the OpenRailwayMap overlay. It is always the vector one, over raster and vector
    // bases alike; openrailwaymap-<type>.<base> is the old raster overlay, still saved in
    // some users' settings, with the same type names
    let ormVectorBase = null;
    let ormVectorType = 'standard';
    let effectiveTileserver = tileserver;
    const ormVectorMatch = tileserver && tileserver.match(/^(?:orm-vector-|openrailwaymap-)([^.]+)\.(.+)$/);
    if (ormVectorMatch) {
        ormVectorType = ormVectorMatch[1];
        ormVectorBase = ormVectorMatch[2];
        effectiveTileserver = ormVectorBase;
    }

    // Handle different tile server types
    if (effectiveTileserver === 'none') {
        mapStyle = { version: 8, sources: {}, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#f8f9fa' } }] };
        if (useGlobe) mapStyle.projection = { type: 'globe' };
    } else if (isVectorTileServer(effectiveTileserver) || styleUrl) {
        // Load vector style
        const url = styleUrl || getVectorStyleUrl(effectiveTileserver, userLanguage);
        try {
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`Failed to load style: ${response.status}`);
            }
            mapStyle = await response.json();
            absolutizeSprite(mapStyle);

            if (OFM_SOURCED_STYLES.includes(effectiveTileserver)) {
                applyStyleLanguage(mapStyle, userLanguage);
            }

            // Add globe projection if requested
            if (useGlobe) {
                mapStyle.projection = { type: 'globe' };
            }
        } catch (error) {
            console.error('Failed to load vector style, falling back to raster:', error);
            // Fallback to raster tiles
            mapStyle = createRasterStyle('osm', userLanguage, useGlobe);
        }
    } else {
        // Use raster tiles
        mapStyle = createRasterStyle(effectiveTileserver, userLanguage, useGlobe);
    }

    return { mapStyle, ormVectorBase, ormVectorType };
}

// Map initialization function with vector tile support
async function initializeMapLibre(options = {}) {
    const {
        container = 'map',
        useGlobe = false,
        userLanguage = window.MAP_LANG || 'en',
        center = [10, 50],
        zoom = 5,
        styleUrl = null,
        preserveDrawingBuffer = false,
        pixelRatio = null
    } = options;

    const tileserver = options.tileserver || 'osm';
    const { mapStyle, ormVectorBase, ormVectorType } =
        await resolveBackground(tileserver, { styleUrl, userLanguage, useGlobe });

    // Create map
    // preserveDrawingBuffer/pixelRatio are only needed to export the canvas as an
    // image (e.g. the print poster) — left off by default since they cost extra
    // GPU memory/bandwidth on the normal interactive map.
    const map = new maplibregl.Map({
        container: container,
        style: mapStyle,
        center: center,
        zoom: zoom,
        doubleClickZoom: false,
        preserveDrawingBuffer: preserveDrawingBuffer,
        ...(pixelRatio ? { pixelRatio } : {})
    });

    map._tlOptions = { styleUrl, userLanguage, useGlobe, tileserver };
    map._currentTileserver = tileserver;
    map._baseIds = { sources: Object.keys(mapStyle.sources || {}), layers: mapStyle.layers.map(l => l.id) };

    // Remember page-added images (pins etc.): a style swap drops them
    map._userImages = new Set();
    const addImage = map.addImage.bind(map);
    map.addImage = (id, ...rest) => { map._userImages.add(id); return addImage(id, ...rest); };

    // Page lines (trips) go under the base's 3D buildings, which then hide them as they hide the ground
    const addLayer = map.addLayer.bind(map);
    map.addLayer = (layer, beforeId) => addLayer(layer, beforeId ?? (layer.type === 'line' ? baseBuildingsId(map) : undefined));

    // Add a sentinel layer that page layers will sit above
    map.once('load', () => {
        map.addLayer({ id: 'orm-sentinel', type: 'background', paint: { 'background-opacity': 0 } });
    });

    // Add OpenRailwayMap vector overlay after style loads
    if (ormVectorBase) {
        map.once('load', () => addOrmOverlay(map, ormVectorType));
    }

    // Add navigation controls
    map.addControl(new maplibregl.NavigationControl(), 'top-right');

    // Zoom readout, for the owner only (TRAINLOG_OWNER, set by the layouts)
    if (window.TRAINLOG_OWNER) {
        const zoomBox = document.createElement('div');
        zoomBox.style.cssText = 'position:absolute;left:50%;top:8px;transform:translateX(-50%);z-index:5;padding:2px 6px;'
            + 'background:rgba(0,0,0,.6);color:#fff;font:12px monospace;border-radius:4px;pointer-events:none';
        map.getContainer().appendChild(zoomBox);
        const showZoom = () => { zoomBox.textContent = `z${map.getZoom().toFixed(2)}`; };
        map.on('zoom', showZoom);
        showZoom();
    }

    return map;
}

function baseBuildingsId(map) {
    const base = new Set(map._baseIds.layers);
    return map.getStyle().layers.find(l => l.type === 'fill-extrusion' && base.has(l.id))?.id;
}

// Where the overlay goes: under the base's first label, under its 3D buildings (drawn after them
// the lines showed through), and under anything the page added. First label, not first symbol:
// oneway arrow layers sit down among the tunnels and would bury it under the roads
function ormAnchorId(map) {
    const base = new Set(map._baseIds.layers);
    return map.getStyle().layers.find(l => !base.has(l.id) || l.type === 'fill-extrusion'
        || (l.type === 'symbol' && l.layout?.['text-field']))?.id;
}

// Adds the OpenRailwayMap vector overlay under the base map's labels. What it adds is
// remembered on map._ormIds so a background change can tell it apart from trip layers.
async function addOrmOverlay(map, ormVectorType) {
    const ormTypeMap = {
        standard:    'standard',
        maxspeed:    'speed',
        signals:     'signals',
        electrified: 'electrification',
        gauge:       'track',
    };
    const ormStyleName = ormTypeMap[ormVectorType] || 'standard';

    const ormIds = map._ormIds = { sources: [], layers: [], type: ormStyleName };

    // "org-<style>" types: openrailwaymap.org's own raster tiles, offered as an alternative to
    // ours now that it serves Trainlog again. It may block us again, hence kept separate
    if (ormVectorType.startsWith('org-')) {
        const id = 'orm-overlay_org';
        map.addSource(id, {
            type: 'raster',
            tiles: [`https://tiles.openrailwaymap.org/${ormVectorType.slice(4)}/{z}/{x}/{y}.png`],
            tileSize: 256,
            maxzoom: 19,
            attribution: '© <a href="https://www.openrailwaymap.org/" target="_blank">OpenRailwayMap</a>'
        });
        map.addLayer({ id, type: 'raster', source: id }, ormAnchorId(map));
        ormIds.sources.push(id);
        ormIds.layers.push(id);
        return;
    }

    setupOrmPopup(map);
    try {
        const ormStyleResp = await fetch(`/getORMStyle/${ormStyleName}.json`);
        const ormStyle = await ormStyleResp.json();

        const skipSources = new Set(['dem', 'search', 'route', 'route_stops', 'openhistoricalmap']);
        const addedSources = new Set();

        // Fix sources: convert relative url/tiles to absolute tiles array
        for (const [name, source] of Object.entries(ormStyle.sources)) {
            if (skipSources.has(name)) continue;
            try {
                const fixedSource = { ...source };
                if (fixedSource.url) {
                    const path = fixedSource.url.startsWith('/') ? fixedSource.url : `/${fixedSource.url}`;
                    fixedSource.tiles = [`https://openrailwaymap.app${path}/{z}/{x}/{y}`];
                    delete fixedSource.url;
                } else if (fixedSource.tiles) {
                    fixedSource.tiles = fixedSource.tiles.map(t =>
                        t.startsWith('/') ? `https://openrailwaymap.app${t}/{z}/{x}/{y}` : t
                    );
                } else {
                    continue; // no tiles, skip
                }
                // Not orm_: Trainlog Rail has its own orm_high/orm_low sources
                map.addSource(`orm-overlay_${name}`, fixedSource);
                ormIds.sources.push(`orm-overlay_${name}`);
                addedSources.add(name);
            } catch (e) {
                console.warn(`[ORM] skipping source ${name}:`, e.message);
            }
        }

        // Add all ORM line layers below text labels but above base map geometry
        // Skip symbol/background layers but the line labels — they cause gritty artifacts and CORS issues
        const skipLayers = new Set(['hillshade', 'route', 'route_text', 'route_stops', 'search']);
        const skipTypes = new Set(['symbol', 'background', 'raster']);
        const anchorId = ormAnchorId(map);
        // Labels along the lines ("25kV 50Hz", speeds, refs) need a font from the map's one glyph
        // source: raster bases have none, so they get OpenFreeMap's
        if (!map.getStyle().glyphs) map.setGlyphs(OFM_GLYPHS);
        const labelFont = map.getStyle().glyphs.includes('jawg') ? ['Noto Bold'] : ['Noto Sans Bold'];
        for (const layer of ormStyle.layers) {
            if (skipLayers.has(layer.id) || !layer.source) continue;
            if (skipSources.has(layer.source) || !addedSources.has(layer.source)) continue;
            const lineLabel = layer.type === 'symbol' && layer.id.endsWith('_text')
                && layer.layout?.['symbol-placement'] === 'line';
            // orm-raster: the overlay below zoom 6, as images (build_style)
            if (skipTypes.has(layer.type) && !lineLabel && layer.id !== 'orm-raster') continue;
            if (layer.id.includes('_cover')) continue;
            // Dash lengths follow line-width, so a wider dashed casing drifts out of step with its fill
            if (layer.id.includes('_casing') && layer.paint?.['line-dasharray']) continue;
            try {
                const newLayer = { ...layer, source: `orm-overlay_${layer.source}` };
                if (lineLabel) newLayer.layout = { ...layer.layout, 'text-font': labelFont };
                // Strip layout.visibility expressions — unsupported by MapLibre, default to visible
                if (newLayer.layout?.visibility && typeof newLayer.layout.visibility !== 'string') {
                    newLayer.layout = { ...newLayer.layout, visibility: 'visible' };
                }
                map.addLayer(newLayer, anchorId);
                ormIds.layers.push(newLayer.id);
            } catch (e) {
                console.warn(`[ORM] skipping layer ${layer.id}:`, e.message);
            }
        }
    } catch (e) {
        console.error('[ORM] failed to load style:', e);
    }
}

// What an overlay line's colour stands for in each mode, from the tile properties behind it
function describeOrmLine(p, type) {
    const list = (...values) => values.filter(v => v != null && v !== '');
    switch (type) {
        case 'speed':
            return p.maxspeed != null ? `${p.maxspeed} km/h` : null;
        case 'electrification':
            if (!p.voltage) return p.electrification_state || null;
            return `${p.voltage >= 1000 ? `${p.voltage / 1000} kV` : `${p.voltage} V`} ${p.frequency ? `${p.frequency} Hz` : 'DC'}`;
        case 'signals':
            return list(p.train_protection0, p.train_protection1, p.train_protection2).join(', ').toUpperCase() || null;
        case 'track':
            return String(p.gauges || p.gauge0 || '').split(/[;,]\s*/).filter(Boolean)
                .map(g => /^\d+$/.test(g) ? `${g} mm` : g).join(' / ') || null;
        default:
            return list(p.usage, p.highspeed ? 'HSL' : null).join(' · ') || null;
    }
}

// The overlay line under a point and what to tell about it, or null. Anything the page draws
// (trips, stops) has its own click and cursor, so a hit on one of those wins
function ormLineAt(map, point) {
    const orm = map._ormIds;
    if (!orm?.layers.length) return null;
    // Lines are a few px wide: search around the point rather than under it
    const box = [[point.x - 6, point.y - 6], [point.x + 6, point.y + 6]];
    const skip = new Set([...map._baseIds.layers, ...orm.layers, 'orm-sentinel']);
    const pageLayers = map.getLayersOrder().filter(id => !skip.has(id));
    if (pageLayers.length && map.queryRenderedFeatures(box, { layers: pageLayers }).length) return null;
    for (const f of map.queryRenderedFeatures(box, { layers: orm.layers })) {
        if (!f.sourceLayer?.includes('railway_line')) continue;
        const p = f.properties;
        // Lines not in service carry no speed, voltage…: their state is what sets them apart
        const meaning = describeOrmLine(p, orm.type) ?? (p.state !== 'present' ? p.state : null);
        const title = [p.ref, p.name].filter(Boolean).join(' ');
        const text = [title, meaning].filter(Boolean).join('\n');
        if (text) return text;
    }
    return null;
}

// Clicking an overlay line tells what its colour means; hovering one shows the pointer
function setupOrmPopup(map) {
    if (map._ormPopup) return;
    map._ormPopup = true;
    map.on('click', (e) => {
        if (e.originalEvent.target !== map.getCanvas()) return;
        const text = ormLineAt(map, e.point);
        if (!text) return;
        const el = document.createElement('div');
        el.style.whiteSpace = 'pre-line';
        el.textContent = text;
        new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
    });
    // Once per frame at most, and the cursor is only reset when set here: trip layers set theirs
    let pending = null, pointing = false;
    map.on('mousemove', (e) => {
        if (pending) { pending = e.point; return; }
        pending = e.point;
        requestAnimationFrame(() => {
            const over = !!ormLineAt(map, pending);
            pending = null;
            if (over === pointing) return;
            pointing = over;
            map.getCanvas().style.cursor = over ? 'pointer' : '';
        });
    });
}

// Swaps the background of a live map, keeping everything the page added on top
// (trip sources/layers, the sentinel, images). Not persisted anywhere.
async function changeBackground(map, tileserver, { useGlobe } = {}) {
    const o = map._tlOptions;
    map._currentTileserver = tileserver;
    // The globe can be switched with the background (map background modal); otherwise keep the current one
    if (useGlobe !== undefined) map._currentGlobe = useGlobe;
    const globe = map._currentGlobe ?? o.useGlobe;
    const { mapStyle, ormVectorBase, ormVectorType } = await resolveBackground(tileserver, { ...o, useGlobe: globe });
    const orm = map._ormIds || { sources: [], layers: [] };
    const baseSources = new Set(map._baseIds.sources);
    const baseLayers = new Set(map._baseIds.layers);
    const ormSources = new Set(orm.sources);
    const ormLayers = new Set(orm.layers);

    const prev = map.getStyle();
    const keepSources = Object.entries(prev.sources).filter(([id]) => !baseSources.has(id) && !ormSources.has(id));
    const keepLayers = prev.layers.filter(l => !baseLayers.has(l.id) && !ormLayers.has(l.id));
    const shadow = tripShadowColor(map);
    for (const l of keepLayers) if (l.id.endsWith('-shadow')) l.paint[`${l.type}-color`] = shadow;
    const images = [...map._userImages].filter(id => map.hasImage(id)).map(id => [id, map.getImage(id)]);

    map._baseIds = { sources: Object.keys(mapStyle.sources || {}), layers: mapStyle.layers.map(l => l.id) };
    map._ormIds = null;
    // Page lines go under the new base's 3D buildings, as addLayer puts them; the rest on top
    for (const [id, src] of keepSources) mapStyle.sources[id] = src;
    const buildings = mapStyle.layers.findIndex(l => l.type === 'fill-extrusion');
    const lines = buildings < 0 ? [] : keepLayers.filter(l => l.type === 'line');
    mapStyle.layers.splice(buildings, 0, ...lines);
    mapStyle.layers.push(...keepLayers.filter(l => !lines.includes(l)));
    map.setStyle(mapStyle, { diff: false });

    map.once('style.load', () => {
        for (const [id, img] of images) {
            if (!map.hasImage(id)) map.addImage(id, img.data, { pixelRatio: img.pixelRatio, sdf: img.sdf });
        }
        if (ormVectorBase) addOrmOverlay(map, ormVectorType);
    });
}

// Trip shadows are black, but on a dark map the darkest trip colours would vanish in one:
// those get a light grey edge instead. Stop dots carry their trip's type as tripType
const DARK_BACKGROUNDS = ['dark-train', 'trainlog-rail', 'jawg-dark'];
const DARK_TRIP_TYPES = ['metro', 'cycle', 'ferry', 'other'];
function tripShadowColor(map) {
    if (!DARK_BACKGROUNDS.includes(map._currentTileserver?.split('.').pop())) return '#000000';
    return ['match', ['coalesce', ['get', 'tripType'], ['get', 'type']], DARK_TRIP_TYPES, '#b0b0b0', '#000000'];
}

// Check if tileserver is a vector tile server
function isVectorTileServer(tileserver) {
    const vectorServers = [
        'jawg-streets-v2',
        'jawg-lagoon-v2',
        'trainlog-lagoon-v2',
        'dark-train',
        'trainlog-rail',
        'trainlog-light',
        'ofm-liberty',
        'ofm-bright',
        'ofm-positron'
    ];
    return vectorServers.includes(tileserver);
}

// Styles served from OpenFreeMap tiles, whose per-language name fields let the
// labels be localised client-side (Jawg instead gets its language server-side).
const OFM_GLYPHS = 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf';

const OFM_SOURCED_STYLES = ['dark-train', 'trainlog-rail', 'trainlog-light', 'ofm-liberty', 'ofm-bright', 'ofm-positron'];

// "native" labels places as written locally (北京); "local" the same with the
// Latin spelling below a non-Latin name (北京 / Beijing); "int" in Latin letters
// only (Beijing); a language code in that language.
function applyStyleLanguage(style, userLanguage) {
    const code = userLanguage === 'gsw' ? 'de' : userLanguage.split('-')[0];
    const textField = code === 'local'
        ? ['case', ['has', 'name:nonlatin'],
            ['concat', ['get', 'name:nonlatin'], '\n', ['get', 'name:latin']],
            ['coalesce', ['get', 'name'], ['get', 'name:latin']]]
        : code === 'native'
        ? ['coalesce', ['get', 'name'], ['get', 'name:latin']]
        : code === 'int'
        ? ['coalesce', ['get', 'name:latin'], ['get', 'name']]
        : ['coalesce', ['get', `name:${code}`], ['get', 'name:latin'], ['get', 'name']];
    for (const layer of style.layers) {
        if (layer.type !== 'symbol' || !layer.layout || !layer.layout['text-field']) continue;
        if (!JSON.stringify(layer.layout['text-field']).includes('name')) continue;
        layer.layout['text-field'] = textField;
    }
}

// Get vector style URL based on tileserver
// /getVectorStyle serves its sprite paths relative (the server can't tell http
// from https behind the proxy); MapLibre wants them absolute, on the page's origin
function absolutizeSprite(style) {
    const abs = url => new URL(url, window.location.href).href;
    if (typeof style.sprite === 'string') {
        style.sprite = abs(style.sprite);
    } else if (Array.isArray(style.sprite)) {
        style.sprite = style.sprite.map(s => ({ ...s, url: abs(s.url) }));
    }
}

function getVectorStyleUrl(tileserver, userLanguage) {
    // Validate language for Jawg
    if (!MapConfig.jawgAllowedLangs.includes(userLanguage)) {
        userLanguage = "int";
    }
    return MapConfig.vectorStylePaths[tileserver].replace("{language}", userLanguage);
}
function createRasterStyle(serverType, userLanguage, useGlobe) {
    const tileConfig = getTileServerConfig(serverType, userLanguage);
    const projection = useGlobe ? { projection: { type: 'globe' } } : {};

    return {
        version: 8,
        ...projection,
        sources: {
            'osm-tiles': {
                type: 'raster',
                tiles: Array.isArray(tileConfig.url) ? tileConfig.url : [tileConfig.url],
                tileSize: 256,
                attribution: tileConfig.attribution
            }
        },
        layers: [
            {
                id: 'osm-tiles',
                type: 'raster',
                source: 'osm-tiles',
                minzoom: 0,
                maxzoom: 22
            }
        ]
    };
}

// Get tile server configuration (for raster fallback)
function getTileServerConfig(serverType, userLanguage) {
    let tileUrl = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
    let attribution = '© OpenStreetMap contributors';

    // Validate language for Jawg
    if (!MapConfig.jawgAllowedLangs.includes(userLanguage)) {
        userLanguage = "int";
    }

    switch (serverType) {
        case 'de':
            tileUrl = 'https://tile.openstreetmap.de/{z}/{x}/{y}.png';
            break;
        case 'fr':
            tileUrl = [
                'https://a.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png',
                'https://b.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png',
                'https://c.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png'
            ];
            attribution = '© OpenStreetMap France | © OpenStreetMap contributors';
            break;
        default:
            if (serverType && serverType.startsWith('jawg-')) {
                tileUrl = `https://tiles.trainlog.me/tile/${serverType}/{x}/{y}/{z}/${userLanguage}`;
                attribution = '<a href="https://jawg.io">© Jawg</a> © OpenStreetMap contributors';
            } else if (serverType === 'thunderforest-transport') {
                tileUrl = `https://tiles.trainlog.me/tile/${serverType}/{x}/{y}/{z}`;
                attribution = '© Thunderforest, © OpenStreetMap contributors';
            } else if (serverType && /^(orm-vector-|openrailwaymap-)/.test(serverType)) {
                // The ORM overlay is added by resolveBackground; here just the base raster
                const m = serverType.match(/^(?:orm-vector-|openrailwaymap-)([^.]+)\.(.+)$/);
                serverType = (m ? m[2] : null) || "osm";
                return getTileServerConfig(serverType, userLanguage);
            }
    }

    return { url: tileUrl, attribution: attribution };
}

// Normalize longitude for geodesic lines
function normalizeLng(lng, prevLng) {
    let delta = lng - prevLng;
    if (delta > 180) lng -= 360;
    else if (delta < -180) lng += 360;
    return lng;
}

function normalizePathCoords(coords) {
    let result = [];
    let prevLng = coords[0][0];
    for (let [lng, lat] of coords) {
        lng = normalizeLng(lng, prevLng);
        result.push([lng, lat]);
        prevLng = lng;
    }
    return result;
}

// How far the flown track's first fix has to be from the departure airport before the gap
// is worth drawing. FR24's public track starts at the first radar contact, which is
// usually on or beside the runway but is sometimes tens of km into the climb-out. Bridging
// unconditionally therefore drew a straight stub alongside tracks that already reached the
// airport; below this threshold the gap is normal coverage jitter, not missing data.
const LIVE_ORIGIN_BRIDGE_MIN_KM = 5;

// Great-circle distance in km between two [lat, lng] points.
function _haversineKm(a, b) {
    const toRad = d => d * Math.PI / 180;
    const dLat = toRad(b[0] - a[0]), dLng = toRad(b[1] - a[1]);
    const h = Math.sin(dLat / 2) ** 2
            + Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dLng / 2) ** 2;
    return 6371 * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
}

// The departure airport, but only when the live track genuinely fails to reach it — i.e.
// only when there is missing data to stand in for. Both arguments are [lat, lng]; returns
// null when no bridge should be drawn, so callers can just test the result.
function liveOriginBridge(origin, firstFix) {
    if (!origin || !firstFix) return null;
    return _haversineKm(origin, firstFix) > LIVE_ORIGIN_BRIDGE_MIN_KM ? origin : null;
}

// Create geodesic line between two points
function createGeodesicLine(start, end, numPoints = 100, splitSegments = false) {
    const toRad = deg => deg * Math.PI / 180;
    const toDeg = rad => rad * 180 / Math.PI;

    const φ1 = toRad(start[1]), λ1 = toRad(start[0]);
    const φ2 = toRad(end[1]), λ2 = toRad(end[0]);
    const dλ = λ2 - λ1;
    const cosD = Math.sin(φ1) * Math.sin(φ2) + Math.cos(φ1) * Math.cos(φ2) * Math.cos(dλ);
    const distance = Math.acos(Math.min(Math.max(cosD, -1), 1));

    let segments = [];
    let currentSeg = [];
    let prevLng;

    for (let i = 0; i <= numPoints; i++) {
        const f = i / numPoints;
        const A = Math.sin((1 - f) * distance) / Math.sin(distance);
        const B = Math.sin(f * distance) / Math.sin(distance);

        const x = A * Math.cos(φ1) * Math.cos(λ1) + B * Math.cos(φ2) * Math.cos(λ2);
        const y = A * Math.cos(φ1) * Math.sin(λ1) + B * Math.cos(φ2) * Math.sin(λ2);
        const z = A * Math.sin(φ1) + B * Math.sin(φ2);

        const lat = toDeg(Math.atan2(z, Math.sqrt(x * x + y * y)));
        let lng = toDeg(Math.atan2(y, x));

        if (prevLng !== undefined) {
            lng = normalizeLng(lng, prevLng);
        }
        prevLng = lng;

        if (splitSegments && currentSeg.length && Math.abs(lng - currentSeg[currentSeg.length - 1][0]) > 180) {
            segments.push(currentSeg);
            currentSeg = [];
        }

        currentSeg.push([lng, lat]);
    }

    segments.push(currentSeg);
    return splitSegments ? segments : segments.flat();
}

// ---------------------------------------------------------------------------
// Live flight tracks
//
// A flight logged before it happens is stored as a 2-point line and drawn as a
// geodesic, which looks obviously fake next to a completed leg's real track.
// While it is airborne we have the flown-so-far path from FR24, so we swap it in
// and estimate only the remainder.
// ---------------------------------------------------------------------------

// Attach the real flown-so-far path to each trip that has a live track, as `livePath`.
//
// `trip.path` is deliberately left alone. It is the trip's stored route and the rest of
// the page reads it as such — the destination marker, for one, takes its position from
// the last point of it. Overwriting it with a track that stops at the aircraft dragged
// the end of the journey along with the plane. Anything that wants the live geometry
// opts in by reading livePath; everything else keeps seeing the real route.
//
// Both map surfaces already split an in-progress trip into travelled and remaining
// portions, so they render the estimated remainder with their own existing styling.
//
// A trip that was live and is no longer is ended (see endLiveTrack), so a landed flight
// never stays on the map as a frozen aircraft.
//
// `features` is optional. When given (the array returned by buildTripLayers), the
// matching feature's geometry is updated in place. In place matters: buildTripLayers
// installs a pulse animation that addresses features by array index, so the array
// must keep its identity, order and length across a refresh.
//
// Returns whether anything changed, so the caller only redraws when it has to.
function applyLiveTracks(trips, liveTracks, features) {
    let changed = false;

    trips.forEach(trip => {
        const live = liveTracks[trip.trip.uid];
        const feature = features && features.find(f => f && f.properties.id === trip.trip.uid);
        if (!live || live.status !== 'live' || !live.path || live.path.length < 2) {
            if (trip.liveTracked) {
                endLiveTrack(trip, live, feature);
                changed = true;
            }
            return;
        }
        changed = true;

        const original = trip.path || [];
        // Where the flight is meant to end. Taken from the stored route rather than the
        // live feed, so a diversion cannot silently retarget the estimate at an airport
        // the user never logged. Safe to recompute every refresh now that trip.path is
        // never overwritten.
        const destination = original.length ? original[original.length - 1] : null;
        // The origin, for the same reason plus one of its own: FR24's public track can
        // start at the first airborne radar contact rather than at the gate — several km
        // into the climb-out — and without this the route appears to begin in mid-air
        // near the airport instead of at it. liveOriginBridge decides whether that gap
        // actually exists on this flight; most of the time it does not.
        const origin = original.length ? original[0] : null;

        trip.livePath = live.path;
        trip.liveTracked = true;
        trip.liveOrigin = liveOriginBridge(origin, live.path[0]);
        // Landed and taxiing in (the server says, from the altitude): no remainder to draw
        trip.liveDestination = live.arrived ? null : destination;
        trip.liveUpdated = live.updated || null;
        // When the server will next have something new (a position, or the whole track)
        trip.liveNextUpdate = live.next_update || null;
        // Its ground speed over the last minute (m/s), to carry it on at between two fixes
        trip.liveSpeed = live.speed || null;
        // And its climb or descent rate (m/s), for the 3D view's height between two fixes
        trip.liveVspeed = live.vspeed || null;
        trip.liveOnGround = !!live.on_ground;
        trip.liveElevations = live.elevations || null;
        // The logged arrival airport, kept even once landed (liveDestination is then null)
        trip.liveDestinationPoint = destination;
        if (live.altitude) trip.altitude = live.altitude;
        if (live.timestamps) trip.timestamps = live.timestamps;

        if (feature) {
            // The whole route, not just the flown part: this feature stands for the
            // entire trip, and its layers include the drop shadow. Giving it only
            // the flown portion would end the shadow at the aircraft and leave the
            // remaining leg looking detached from the rest of the map. Bridged to
            // both airports so the line still runs terminal to terminal.
            let coords = live.path.map(c => [c[1], c[0]]);
            if (trip.liveOrigin) {
                coords = createGeodesicLine(
                    [trip.liveOrigin[1], trip.liveOrigin[0]], coords[0]
                ).concat(coords);
            }
            // On to the destination airport while far from it; on the approach not (the
            // airport's point is not its runway): the line is what it has flown, and the
            // aircraft carries on from its end (the trip page's dead reckoning)
            if (liveRemainderShown(trip)) {
                coords = coords.concat(
                    createGeodesicLine(coords[coords.length - 1],
                                       [destination[1], destination[0]])
                );
            }
            feature.geometry.coordinates = normalizePathCoords(coords);
        }
    });
    return changed;
}

// Back to an ordinary trip: it landed, with its final track in `live`, or the server
// stopped tracking it and it keeps the route it was logged with.
function endLiveTrack(trip, live, feature) {
    ['livePath', 'liveTracked', 'liveOrigin', 'liveDestination', 'liveUpdated', 'liveNextUpdate', 'liveSpeed', 'liveVspeed', 'liveOnGround', 'liveElevations', 'liveDestinationPoint']
        .forEach(key => delete trip[key]);
    // Only flights without a recorded track are tracked, so any altitude came from the
    // live feed and no longer matches the path.
    delete trip.altitude;
    delete trip.timestamps;

    const landed = live && live.status === 'landed' && live.path && live.path.length >= 2;
    if (landed) {
        trip.path = live.path;
        if (live.altitude) trip.altitude = live.altitude;
        if (live.timestamps) trip.timestamps = live.timestamps;
    }
    const fresh = feature ? tripToFeature(trip) : null;
    if (!feature) computeTimeStatus(trip);
    // Early arrivals are common: landed is over, whatever the timetable says.
    if (landed) trip.time = 'past';
    if (fresh) {
        feature.geometry.coordinates = fresh.geometry.coordinates;
        feature.properties.time = trip.time;
    }
}

// One trip -> its GeoJSON feature, or null when the path is unusable.
// Takes the path either as trip.path ([[lat, lng], ...], as the trip endpoints
// return it) or as trip.coords ([[lng, lat], ...], already decoded — the
// trips map builds those from encoded polylines and skips the swap).
function tripToFeature(trip) {
    trip = computeTimeStatus(trip);
    if (trip.trip.type == 'helicopter'){
        trip.trip.type = 'air';
    }

    try {
        let coords = trip.coords || trip.path.map(c => [c[1], c[0]]);
        coords = normalizePathCoords(coords);
        if ((trip.trip.type === 'air') && coords.length === 2) {
            coords = createGeodesicLine(coords[0], coords[1]);
        }

        return {
            type: 'Feature',
            geometry: { type: 'LineString', coordinates: coords },
            properties: {
                id: trip.trip.uid,
                type: trip.trip.type,
                time: trip.time,
                // -1 = undated past trip, matched by the 'undated' pseudo-year
                year: trip.trip.start_datetime === -1 ? 'undated'
                    : trip.trip.start_datetime !== 1
                    ? trip.trip.start_datetime.substring(0, 4)
                    : null,
                origin: trip.trip.origin_station,
                destination: trip.trip.destination_station
            }
        };
    } catch (error) {
        console.warn('Broken trip:', trip.trip.uid);
        return null;
    }
}

// Build trip layers for the map
function buildTripLayers(map, trips, transportTypes, options = {}) {
    const {
        onLayerClick = null,
        visibleTypes = new Set(transportTypes.map(t => t.id)),
        showShadows = true
    } = options;

    // Create features from trips
    const features = trips.map(tripToFeature).filter(feature => feature !== null);

    // Add source
    if (!map.getSource('trips')) {
        map.addSource('trips', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features },
            // lineMetrics: true // Enable line metrics for better performance
        });
    }

    // Build layers
    transportTypes.forEach(type => {
        if (!visibleTypes.has(type.id)) return;

        const basePaint = {
            'line-color': type.color,
            'line-width': (type.id === 'air') ? 1 : 3,
            'line-opacity': 0.8
        };

        // Add shadow layers if enabled
        if (showShadows) {
            const timeStatuses = ['past', 'future', 'plannedFuture', 'current'];
            timeStatuses.forEach(timeStatus => {
                const shadowId = `${type.id}-${timeStatus}-shadow`;
                if (!map.getLayer(shadowId)) {
                    map.addLayer({
                        id: shadowId,
                        type: 'line',
                        source: 'trips',
                        filter: ['all',
                            ['==', ['get', 'type'], type.id],
                            ['==', ['get', 'time'], timeStatus]
                        ],
                        layout: { 
                            'line-join': 'round', 
                            'line-cap': 'round',
                            'visibility': 'none'
                        },
                        paint: {
                            'line-color': tripShadowColor(map),
                            'line-width': (type.id === 'air') ? 3 : 5,
                            'line-opacity': timeStatus === 'future' ? 0.3 : 0.8,
                            'line-blur': 0.5
                        }
                    });
                }
            });
        }

        // Add main layers
        const layers = [
            {
                id: `${type.id}-past`,
                filter: ['==', ['get', 'time'], 'past'],
                paint: basePaint
            },
            {
                id: `${type.id}-future`,
                filter: ['==', ['get', 'time'], 'future'],
                paint: { ...basePaint, 'line-color': '#ffffff', 'line-opacity': 0.8 }
            },
            {
                id: `${type.id}-planned-base`,
                filter: ['==', ['get', 'time'], 'plannedFuture'],
                paint: basePaint
            },
            {
                id: `${type.id}-planned-overlay`,
                filter: ['==', ['get', 'time'], 'plannedFuture'],
                paint: { ...basePaint, 'line-color': '#ffffff', 'line-dasharray': [3, 3] }
            },
            {
                id: `${type.id}-current`,
                filter: ['==', ['get', 'time'], 'current'],
                paint: {
                    ...basePaint,
                    'line-color': '#ff0000',
                    'line-width': 4,
                    'line-opacity': ['case', ['boolean', ['feature-state', 'pulse'], false], 0.3, 1]
                }
            }
        ];

        layers.forEach(layer => {
            if (!map.getLayer(layer.id)) {
                map.addLayer({
                    id: layer.id,
                    type: 'line',
                    source: 'trips',
                    filter: ['all',
                        ['==', ['get', 'type'], type.id],
                        layer.filter
                    ],
                    layout: { 
                        'line-join': 'round', 
                        'line-cap': 'round',
                        'visibility': 'none'
                    },
                    paint: layer.paint
                });

                // Set up interaction
                if (onLayerClick) {
                    map.on('click', layer.id, onLayerClick);
                }
                map.on('mouseenter', layer.id, () => {
                    map.getCanvas().style.cursor = 'pointer';
                });
                map.on('mouseleave', layer.id, () => {
                    map.getCanvas().style.cursor = '';
                });
            }
        });
    });

    // Set up pulse animation for current trips
    let pulse = false;
    setInterval(() => {
        pulse = !pulse;
        features.forEach((f, i) => {
            if (f.properties.time === 'current') {
                map.setFeatureState({ source: 'trips', id: i }, { pulse });
            }
        });
    }, 1000);

    return features;
}

// Layers for one time slice of trips on its own source (the trips map draws
// its trips as consecutive slices in time, oldest at the bottom). Unlike
// buildTripLayers there is one layer per role, styled per feature, so lines
// stack in data order across transport types: the slice's shadows, then its
// lines (features in time order), then the dashes of planned trips. The
// source holds only the trips to display, so nothing is filtered here.
// Features need `type` and `time` properties; ids are promoted from `id` so
// feature-state (the pulse of current trips) can address them.
function addTripSliceLayers(map, sourceId, transportTypes, options = {}) {
    const { onLayerClick = null } = options;
    const time = ['get', 'time'];
    const isAir = ['==', ['get', 'type'], 'air'];

    if (!map.getSource(sourceId)) {
        map.addSource(sourceId, {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] },
            promoteId: 'id'
        });
    }

    const layout = { 'line-join': 'round', 'line-cap': 'round' };
    const layers = [
        {
            id: `${sourceId}-shadow`,
            paint: {
                'line-color': tripShadowColor(map),
                'line-width': ['case', isAir, 3, 5],
                'line-opacity': ['case', ['==', time, 'future'], 0.3, 0.8],
                'line-blur': 0.5
            }
        },
        {
            id: `${sourceId}-line`,
            clickable: true,
            paint: {
                'line-color': tripLineColor(transportTypes),
                'line-width': ['case', ['==', time, 'current'], 4, isAir, 1, 3],
                'line-opacity': ['case',
                    ['==', time, 'current'],
                    ['case', ['boolean', ['feature-state', 'pulse'], false], 0.3, 1],
                    0.8
                ]
            }
        },
        {
            // Planned trips: their line in the type colour, white dashes on top
            id: `${sourceId}-planned-overlay`,
            filter: ['==', time, 'plannedFuture'],
            clickable: true,
            paint: {
                'line-color': '#ffffff',
                'line-width': ['case', isAir, 1, 3],
                'line-opacity': 0.8,
                'line-dasharray': [3, 3]
            }
        }
    ];

    layers.forEach(layer => {
        if (map.getLayer(layer.id)) return;
        const spec = { id: layer.id, type: 'line', source: sourceId, layout, paint: layer.paint };
        if (layer.filter) spec.filter = layer.filter;
        map.addLayer(spec);
        if (layer.clickable) {
            if (onLayerClick) map.on('click', layer.id, onLayerClick);
            map.on('mouseenter', layer.id, () => { map.getCanvas().style.cursor = 'pointer'; });
            map.on('mouseleave', layer.id, () => { map.getCanvas().style.cursor = ''; });
        }
    });
}

// Line colour of the slice layers: by transport type, or by year when given a
// { year: colour } map. Current and future trips keep their own colours.
function tripLineColor(transportTypes, yearColors = null) {
    const pairs = yearColors
        ? Object.entries(yearColors).flat()
        : transportTypes.flatMap(type => [type.id, type.color]);
    const color = pairs.length
        ? ['match', ['get', yearColors ? 'year' : 'type'], ...pairs, '#888888']
        : '#888888';
    return ['case',
        ['==', ['get', 'time'], 'current'], '#ff0000',
        ['==', ['get', 'time'], 'future'], '#ffffff',
        color
    ];
}

// Oldest to newest. Changes hue all along, so neighbouring years stay apart,
// and ends in magenta rather than the red of current trips.
const YEAR_RAMP = ['#3b3eac', '#2f7fd8', '#17becf', '#2ca02c', '#c2b000', '#f28e2b', '#d6339b'];

// How much a year's count weighs on its colour, from the viewer's slider: 1 spreads
// the busy years over most of the ramp, 0 gives every year the same step
function yearSpread() {
    try {
        const spread = parseFloat(localStorage.getItem('yearColorSpread'));
        return spread >= 0 && spread <= 1 ? spread : 1;
    } catch (e) {
        return 1;
    }
}

function setYearSpread(spread) {
    try { localStorage.setItem('yearColorSpread', spread); } catch (e) {}
}

// { year: colour } from { year: count }. Each year sits on the ramp at the middle
// of its share of the weighted count, so with a full spread the busy years spread
// over most of it instead of a few sparse early ones
function yearColors(counts) {
    const years = Object.keys(counts).sort();
    const spread = yearSpread();
    const weights = years.map(year => counts[year] ** spread);
    const total = weights.reduce((n, weight) => n + weight, 0);
    let before = 0;
    const positions = weights.map(weight => {
        const position = (before + weight / 2) / total;
        before += weight;
        return position;
    });
    const first = positions[0], span = positions[positions.length - 1] - first;

    const rgb = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
    const colors = {};
    years.forEach((year, i) => {
        const x = (span ? (positions[i] - first) / span : 0.5) * (YEAR_RAMP.length - 1);
        const j = Math.min(Math.floor(x), YEAR_RAMP.length - 2);
        const a = rgb(YEAR_RAMP[j]), b = rgb(YEAR_RAMP[j + 1]);
        colors[year] = `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * (x - j))).join(',')})`;
    });
    return colors;
}

function toggleYearLegend(button) {
    const asList = button.parentElement.classList.toggle('as-list');
    try { localStorage.setItem('yearLegendAsList', asList); } catch (e) {}
}

// Legend of yearColors(): a row per year, newest first, or one bar, which a
// button switches between; the bar by default when the rows would be too many
function yearLegendHtml(colors) {
    const entries = Object.entries(colors);
    if (!entries.length) return '';
    const labels = [entries[0], entries[Math.floor(entries.length / 2)], entries[entries.length - 1]];
    let asList = entries.length <= 10;
    try { asList = (localStorage.getItem('yearLegendAsList') ?? String(asList)) === 'true'; } catch (e) {}
    return `
        <div class="year-legend${asList ? ' as-list' : ''}">
            <button type="button" class="year-legend-toggle" onclick="MapLibreUtils.toggleYearLegend(this)">
                <i class="fa-solid fa-list-ul"></i><i class="fa-solid fa-grip-lines"></i>
            </button>
            <div class="year-list">${entries.slice().reverse().map(([year, color]) => `
                <div class="legend-item">
                    <div class="legend-line" style="background-color: ${color};"></div>
                    <span>${year}</span>
                </div>`).join('')}</div>
            <div class="year-bar">
                <div class="year-ramp">${entries.map(([year, color]) =>
                    `<div title="${year}" style="background-color: ${color};"></div>`).join('')}</div>
                <div class="year-ramp-labels">${labels.map(([year]) => `<span>${year}</span>`).join('')}</div>
            </div>
        </div>`;
}

// Update layer visibility based on filters
function updateLayerVisibility(map, transportTypes, filters) {
    transportTypes.forEach(type => {
        const showType = filters.transportTypes.has(type.id);
        
        let timeFilter;
        switch (filters.viewMode) {
            case 'past':
                timeFilter = ['==', ['get', 'time'], 'past'];
                break;
            case 'planned':
                timeFilter = ['==', ['get', 'time'], 'plannedFuture'];
                break;
            case 'projects':
                timeFilter = ['==', ['get', 'time'], 'future'];
                break;
            case 'year':
                timeFilter = filters.years.length > 0
                    ? ['in', ['get', 'year'], ['literal', filters.years]]
                    : false;
                break;
            case 'all':
            default:
                timeFilter = true;
                break;
        }

        const updateLayer = (layerId, shadowId, filter, visible) => {
            if (shadowId && map.getLayer(shadowId)) {
                if (visible) {
                    map.setFilter(shadowId, filter);
                    map.setLayoutProperty(shadowId, 'visibility', 'visible');
                } else {
                    map.setLayoutProperty(shadowId, 'visibility', 'none');
                }
            }
            if (map.getLayer(layerId)) {
                if (visible) {
                    map.setFilter(layerId, filter);
                    map.setLayoutProperty(layerId, 'visibility', 'visible');
                } else {
                    map.setLayoutProperty(layerId, 'visibility', 'none');
                }
            }
        };

        // Update each layer type
        updateLayer(
            `${type.id}-past`,
            `${type.id}-past-shadow`,
            ['all', ['==', ['get', 'type'], type.id], ['==', ['get', 'time'], 'past'], timeFilter],
            timeFilter && showType
        );

        updateLayer(
            `${type.id}-future`,
            `${type.id}-future-shadow`,
            ['all', ['==', ['get', 'type'], type.id], ['==', ['get', 'time'], 'future'], timeFilter],
            timeFilter && showType && filters.viewMode !== 'past'
        );

        const plannedFilter = ['all', ['==', ['get', 'type'], type.id], ['==', ['get', 'time'], 'plannedFuture'], timeFilter];
        const showPlanned = timeFilter && showType && filters.viewMode !== 'past';
        updateLayer(`${type.id}-planned-base`, `${type.id}-plannedFuture-shadow`, plannedFilter, showPlanned);
        updateLayer(`${type.id}-planned-overlay`, null, plannedFilter, showPlanned);

        updateLayer(
            `${type.id}-current`,
            `${type.id}-current-shadow`,
            ['all', ['==', ['get', 'type'], type.id], ['==', ['get', 'time'], 'current'], timeFilter],
            timeFilter && showType
        );
    });
}

// Fit map bounds to visible features
// `fitOptions` is merged into the fitBounds options, e.g. { animate: false }.
function fitBoundsToVisibleFeatures(map, features, filters, type=null, fitOptions={}) {
    // Listen before moving: without animation, moveend fires inside fitBounds
    const moved = new Promise(resolve => {
        map.once('moveend', () => {
            resolve({
                center: map.getCenter(),
                zoom: map.getZoom()
            });
        });
    });

    const visibleFeatures = features.filter(feature => {
        const type = feature.properties.type;
        const time = feature.properties.time;
        const year = feature.properties.year;
        
        if (!filters.transportTypes.has(type)) {
            return false;
        }
        
        switch (filters.viewMode) {
            case 'past':
                return time === 'past';
            case 'planned':
                return time === 'plannedFuture';
            case 'projects':
                return time === 'future';
            case 'year':
                return filters.years.length > 0 && filters.years.includes(year);
            case 'all':
            default:
                return true;
        }
    });

    if (visibleFeatures.length > 0) {
        const bounds = visibleFeatures.reduce((bounds, feature) => {
            feature.geometry.coordinates.forEach(coord => {
                const [lng, lat] = coord;
                if (!isNaN(lng) && !isNaN(lat)) {
                    bounds.extend(coord);
                }
            });
            return bounds;
        }, new maplibregl.LngLatBounds());

        if (type == 'public_trip'){
            if (window.innerWidth <= 600) {
                // Mobile: smaller padding
                map.fitBounds(bounds, { padding: 50, ...fitOptions });
            } else {
                // Desktop: large padding with offset
                map.fitBounds(bounds, {
                    padding: 300,
                    offset: [-100, 0],
                    ...fitOptions
                });
            }
        } else {
            map.fitBounds(bounds, { padding: 50, ...fitOptions });
        }
    }
    return moved;
}

// Utility function to modify vector style sources
function modifyVectorStyle(style, modifications = {}) {
    const modifiedStyle = JSON.parse(JSON.stringify(style));
    
    // Apply source modifications
    if (modifications.sources) {
        Object.keys(modifications.sources).forEach(sourceId => {
            if (modifiedStyle.sources[sourceId]) {
                Object.assign(modifiedStyle.sources[sourceId], modifications.sources[sourceId]);
            }
        });
    }
    
    // Apply layer modifications
    if (modifications.layers) {
        modifications.layers.forEach(layerMod => {
            const layer = modifiedStyle.layers.find(l => l.id === layerMod.id);
            if (layer) {
                Object.assign(layer, layerMod);
            }
        });
    }
    
    return modifiedStyle;
}

// Export utilities
// ─── 3D flight tracks (altitude profile) ────────────────────────────────────

let _deckGLPromise = null;

// Lazy-load deck.gl from CDN (only when a 3D flight is actually present).
function loadDeckGL() {
    if (window.deck) return Promise.resolve(window.deck);
    if (_deckGLPromise) return _deckGLPromise;
    _deckGLPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'https://unpkg.com/deck.gl@9.0.0/dist.min.js';
        s.onload = () => resolve(window.deck);
        s.onerror = () => reject(new Error('Failed to load deck.gl'));
        document.head.appendChild(s);
    });
    return _deckGLPromise;
}

function _hexToRgbArray(hex) {
    const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || '');
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [64, 185, 31];
}

function _coerceArray(v) {
    if (Array.isArray(v)) return v;
    if (typeof v === 'string') { try { return JSON.parse(v); } catch (e) { return null; } }
    return null;
}

// Viewer-side preference (per-device, free for everyone — not premium) for
// rendering 3D flight tracks. Default ON so premium owners' flights show their
// 3D "clout", but any viewer can switch it off for performance. Stored in
// localStorage so it's a machine-specific choice and works for anonymous viewers.
const FLIGHT_3D_VIEW_KEY = 'trainlog_flight_3d_view';
function flight3DViewEnabled() {
    try { return localStorage.getItem(FLIGHT_3D_VIEW_KEY) !== 'off'; } catch (e) { return true; }
}
function setFlight3DViewEnabled(on) {
    try { localStorage.setItem(FLIGHT_3D_VIEW_KEY, on ? 'on' : 'off'); } catch (e) { /* ignore */ }
}

// A small button to toggle the 3D flight view. Only added when the page actually
// has 3D-capable (premium-owner) flights, so it doubles as a subtle signal of the
// premium flex without ever nagging for money. Positioned as a standalone button
// (not a corner control) so it stays clear of the zoom group and the sidebar
// handle, which both occupy the top-right corner stack.
function _addFlight3DToggleControl(map, onToggle, title) {
    const container = map.getContainer();
    // Reuse the existing button if the layer is rebuilt (a live flight track arrives
    // after first render), otherwise every rebuild would stack another toggle.
    if (map._flight3dToggleBtn) {
        const existing = map._flight3dToggleBtn;
        existing.onclick = () => {
            onToggle(!flight3DViewEnabled());
            existing.style.opacity = flight3DViewEnabled() ? '1' : '0.45';
        };
        return existing;
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.title = title || 'Toggle 3D flight altitude';
    btn.innerHTML = '<i class="fa-solid fa-plane-departure"></i>';
    btn.style.cssText =
        'position:absolute;z-index:399;background:#fff;border:none;border-radius:8px;' +
        'box-shadow:0 1px 5px rgba(0,0,0,.3);font-size:14px;color:#333;' +
        'cursor:pointer;padding:0;';
    container.appendChild(btn);

    // Align to the bottom-left legend ("i") button: same left edge and size,
    // stacked just above it. Falls back to a fixed bottom-left spot if absent.
    const legend = document.querySelector('.legend-control');
    function place() {
        if (legend) {
            const lr = legend.getBoundingClientRect();
            const cr = container.getBoundingClientRect();
            btn.style.left = (lr.left - cr.left) + 'px';
            btn.style.bottom = (cr.bottom - lr.top + 8) + 'px';
            btn.style.width = lr.width + 'px';
            btn.style.height = lr.height + 'px';
        } else {
            btn.style.left = '20px';
            btn.style.bottom = '68px';
            btn.style.width = '42px';
            btn.style.height = '38px';
        }
    }
    place();
    window.addEventListener('resize', place);

    const sync = () => { btn.style.opacity = flight3DViewEnabled() ? '1' : '0.45'; };
    // onclick (not addEventListener) so a rebuild can replace the handler outright
    // instead of leaving the previous one attached and firing the toggle twice.
    btn.onclick = () => { onToggle(!flight3DViewEnabled()); sync(); };
    sync();
    map._flight3dToggleBtn = btn;
    return btn;
}

// deck.gl runs in its own canvas stacked on top of the map (interleaved mode shares
// MapLibre's WebGL context and can blank a raster base map, so it is not an option).
// Left alone that canvas is added last and paints over everything, including the
// station pins and the current-position marker. Slot it in just above the map's own
// canvas instead, so the altitude profile floats over the map lines but still passes
// under the pins and popups.
// Initial compass bearing between two [lng, lat, ...] points, degrees clockwise from north.
function _bearingDeg(a, b) {
    const toRad = d => d * Math.PI / 180;
    const lat1 = toRad(a[1]), lat2 = toRad(b[1]), dLng = toRad(b[0] - a[0]);
    const y = Math.sin(dLng) * Math.cos(lat2);
    const x = Math.cos(lat1) * Math.sin(lat2)
            - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLng);
    return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

// The aircraft of a flight in progress: an airliner seen from above, pointing north, in a
// dark shade of the air green with a white outline, so it reads over land, sea and a
// dark map alike. One drawing for both the flat marker (inline SVG, sharp at any pixel
// ratio) and the 3D view's icon.
// Generated from one half, mirrored exactly (x -> 64 - x), and an engine under each wing.
const PLANE_PATH = 'M32 3.5 C33.9 3.5 35 6.3 35 10.5 L35 10.5 L35 22.5 L58.5 35.5 L58.5 39.5 L35 33.5 L35 48.5 L43.5 54.5 L43.5 57.5 L34.6 55.6 L33.2 60.4 L32 61.5 L30.8 60.4 L29.4 55.6 L20.5 57.5 L20.5 54.5 L29 48.5 L29 33.5 L5.5 39.5 L5.5 35.5 L29 22.5 L29 10.5 C29 6.3 30.1 3.5 32 3.5 Z' +
    ' M42 26.4a1.9 1.9 0 0 1 3.8 0v5.2a1.9 1.9 0 0 1-3.8 0z M18.2 26.4a1.9 1.9 0 0 1 3.8 0v5.2a1.9 1.9 0 0 1-3.8 0z';
function planeSvg(size) {
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size + '" viewBox="0 0 64 64">' +
        '<path d="' + PLANE_PATH + '" fill="#1f6e0d" stroke="#fff" stroke-width="3.5" stroke-linejoin="round"' +
        ' paint-order="stroke"/></svg>';
}
// The 3D icon. deck rasterises it into its atlas once, at its own width and height: three
// times the size it is drawn at, so it stays sharp on a high-density screen rather than
// being a blurred 24 px bitmap scaled up. (The width and height are required too: an SVG
// with only a viewBox makes createImageBitmap throw, and nothing is drawn.)
const PLANE_ICON_PX = 30;
const _PLANE_SVG = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(planeSvg(PLANE_ICON_PX * 3));

function _lowerDeckCanvas(map, attempt) {
    const container = map.getContainer();
    // deck.gl's canvas id has moved around between versions, so identify it by
    // elimination instead: any canvas in the container that isn't MapLibre's own.
    const mapCanvas = map.getCanvas();
    // Markers and popups live in the canvas container, right next to the map's own
    // canvas. Sliding deck's canvas in there too, directly after the map canvas, puts
    // it under them by document order alone — z-index tricks are unreliable here,
    // since it is up to MapLibre's stylesheet whether the containers around them form
    // stacking contexts of their own. deck positions its wrapper absolutely at
    // 0,0/100%x100%, so moving it does not change where it draws.
    const markerParent = map.getCanvasContainer ? map.getCanvasContainer() : mapCanvas.parentElement;
    let moved = 0;
    container.querySelectorAll('canvas').forEach(canvas => {
        if (canvas === mapCanvas) return;
        // deck.gl is installed with map.addControl, so its canvas starts out inside
        // MapLibre's control container (which stacks above the markers on purpose,
        // controls having to stay clickable). Take deck's own wrapper along, since
        // that is the element it sizes and positions.
        const wrapper = canvas.parentElement;
        const node = (wrapper && wrapper !== container && wrapper !== markerParent) ? wrapper : canvas;
        if (mapCanvas.nextSibling !== node) {
            markerParent.insertBefore(node, mapCanvas.nextSibling);
        }
        node.style.zIndex = '';
        node.style.pointerEvents = 'none';
        moved++;
    });
    // deck.gl creates its WebGL device asynchronously, so at addControl time the canvas
    // usually does not exist yet and there is nothing to move — which is why the canvas
    // ended up painting over the pins. Keep looking for a couple of seconds of frames.
    if (!moved && (attempt || 0) < 120) {
        requestAnimationFrame(() => _lowerDeckCanvas(map, (attempt || 0) + 1));
    }
}

// Show or hide the flat ground marker for a live position. While the 3D overlay is up
// the aircraft is drawn at its real altitude, so the ground copy is a duplicate sitting
// directly beneath it.
//
// Toggled directly rather than through a CSS class: the marker's own inline styles make
// a stylesheet rule an unreliable lever here. visibility, not display, because the
// element relies on an inline `display: flex` to centre its glyph.
function _setGroundAircraftVisible(map, visible) {
    map.getContainer().querySelectorAll('.marker-live').forEach(el => {
        el.style.visibility = visible ? 'visible' : 'hidden';
    });
}

// Render flights that carry an altitude array as a 3D deck.gl PathLayer. At pitch
// 0 the path projects exactly onto its 2D ground line (invisible from the top);
// tilting the map reveals the altitude profile rising above the ground track.
// Server only sends altitude for premium owners (the "clout" gate); here a free
// per-viewer toggle decides whether to actually render it (the performance gate).
// Altitudes as heights above the airports rather than above the sea. FR24 gives barometric
// altitude, but 0 on the ground, so over the flat map an approach to an airport 200 m up
// stayed 200 m above its runway and then dropped to the ground in one step. A field's
// elevation is what the aircraft read just off (onto) its runway; the profile is lowered
// by the one, blending into the other along the flight. Only from readings taken right at
// the transition (within a minute of being on the ground): a sparse track would otherwise
// sink the whole climb.
// `ends` (optional): { elevations: [departure m, arrival m] (the airports' own, from the
// airports table), coords: the track's [lat, lng], dest: the arrival airport's [lat, lng] }.
// They fill in what was not seen: an arrival not landed at yet is its departure's reading
// plus the difference in the two airports' elevations (a barometric offset seen there
// carries over), and along a flight not down yet the blend goes by how far it has come.
function _aboveAirports(altitude, timestamps, waypoints, ends) {
    let runway = timestamps && _runwayTimes(waypoints);
    // A take-off or landing the track shows but that has no runway time (yet): at its
    // first fix on the ground (off it), when that is close to the reading in the air
    if (timestamps) {
        const seen = _groundTransitions(altitude, timestamps);
        // Already braking at its first fix on the ground: it touched down at (about) its
        // last in the air, whose reading is then the runway's (FR24 flags the ground late)
        const touch = _touchdown(altitude, timestamps, ends && ends.coords);
        if (touch) {
            seen[1] = touch.at;
            ends = Object.assign({}, ends, { field1: touch.reading });
        }
        if (seen[0] != null || seen[1] != null) {
            runway = [runway && runway[0] != null ? runway[0] : seen[0], runway && runway[1] != null ? runway[1] : seen[1]];
        }
    }
    if (runway && (runway[0] != null || runway[1] != null)) {
        return _aboveRunwayTimes(altitude, timestamps, runway[0], runway[1], ends);
    }
    const air = [];
    altitude.forEach((a, i) => { if (a > 50) air.push(i); });
    if (!air.length) return altitude;
    const first = air[0], last = air[air.length - 1];
    const near = (i, j) => timestamps && timestamps[i] && timestamps[j] && Math.abs(timestamps[i] - timestamps[j]) <= 60;
    let e0 = first > 0 && near(first, first - 1) ? altitude[first] : null;
    let e1 = last < altitude.length - 1 && near(last, last + 1) ? altitude[last] : null;
    [e0, e1] = _withElevations(e0, e1, ends);
    if (e0 == null && e1 == null) return altitude;
    const a0 = e0 ?? e1, a1 = e1 ?? e0;
    const along = _alongRoute(ends);
    return altitude.map((a, i) => {
        if (!(a > 50)) return 0;
        const f = along ? along(i) : last > first ? (i - first) / (last - first) : 0;
        return Math.max(0, a - (a0 + (a1 - a0) * f));
    });
}

// [take-off, landing] epoch seconds as the readings show them, each null if not seen: the
// last fix on the ground before the first in the air, the first on the ground after the last
// in the air, each within a minute of it
function _groundTransitions(altitude, timestamps) {
    let first = -1, last = -1;
    altitude.forEach((a, i) => { if (a > 50) { if (first < 0) first = i; last = i; } });
    if (first < 0) return [null, null];
    const near = (i, j) => timestamps[i] && timestamps[j] && Math.abs(timestamps[i] - timestamps[j]) <= 60;
    const up = first > 0 && !(altitude[first - 1] > 50) && near(first, first - 1) ? timestamps[first - 1] : null;
    const down = last < altitude.length - 1 && !(altitude[last + 1] > 50) && near(last, last + 1) ? timestamps[last + 1] : null;
    return [up, down];
}

// Where the readings say it touched down, when the ground speed shows it: { at, reading } at
// its last fix in the air, when by its first on the ground it is already 15 % slower than
// on its approach (braking: on the runway before FR24 says so), else null.
function _touchdown(altitude, timestamps, coords) {
    if (!coords || coords.length !== altitude.length) return null;
    let last = -1;
    altitude.forEach((a, i) => { if (a > 50) last = i; });
    if (last < 1 || last >= altitude.length - 1 || altitude[last + 1] > 50) return null;
    const speed = (i, j) => {
        const dt = timestamps[j] - timestamps[i];
        if (!(dt > 0)) return null;
        const a = coords[i], b = coords[j], k = Math.cos(a[0] * Math.PI / 180);
        return Math.hypot((b[1] - a[1]) * k, b[0] - a[0]) * 111320 / dt;
    };
    const approach = [];
    for (let i = last; i > 0 && timestamps[last] - timestamps[i - 1] <= 60; i--) {
        const v = speed(i - 1, i);
        if (v) approach.push(v);
    }
    if (approach.length < 2) return null;
    approach.sort((x, y) => x - y);
    const v0 = approach[approach.length >> 1], v1 = speed(last, last + 1);
    return v1 != null && v1 < 0.85 * v0 ? { at: timestamps[last], reading: altitude[last] } : null;
}

// The two ends' readings with what the airports' elevations say about the missing one(s)
function _withElevations(r0, r1, ends) {
    const el = ends && ends.elevations;
    if (!el || el[0] == null || el[1] == null) return [r0, r1];
    if (r0 == null && r1 == null) return [el[0], el[1]];
    if (r1 == null) return [r0, r0 + el[1] - el[0]];
    if (r0 == null) return [r1 - (el[1] - el[0]), r1];
    // Both read off the track: the one that disagrees with the other by far more than the
    // airports' elevations do (a line carried to its runway time off a steep last descent)
    // gives way to what the other and the elevations say
    const off = (r1 - r0) - (el[1] - el[0]);
    // (the wrong one is the one further from its own airport's elevation)
    if (Math.abs(off) > 150) return Math.abs(r1 - el[1]) > Math.abs(r0 - el[0])
        ? [r0, r0 + el[1] - el[0]] : [r1 - (el[1] - el[0]), r1];
    return [r0, r1];
}

// How far along (0 at its departure, 1 at its arrival) track point i is, by its distances
// to the two airports, or null without them
function _alongRoute(ends) {
    const c = ends && ends.coords, dest = ends && ends.dest;
    if (!c || !c.length || !dest) return null;
    const o = c[0], d = (a, b) => Math.hypot((a[1] - b[1]) * Math.cos(a[0] * Math.PI / 180), a[0] - b[0]);
    return i => {
        const p = c[i] || c[c.length - 1], d0 = d(p, o), d1 = d(p, dest);
        return d0 + d1 > 0 ? d0 / (d0 + d1) : 0;
    };
}

// A flight's take-off and landing times (its runway stops, src/runway_stops.py), in epoch
// seconds, each null when it has none; null without either.
function _runwayTimes(waypoints) {
    let list;
    try { list = typeof waypoints === 'string' ? JSON.parse(waypoints) : waypoints; } catch (e) { return null; }
    const at = (kind, field) => {
        const st = ((list || []).find(w => w && w.stop && w.stop.kind === kind) || {}).stop;
        const ms = st ? Date.parse(st[field]) : NaN;
        return isNaN(ms) ? null : ms / 1000;
    };
    const t0 = at('takeoff', 'dep_rt'), t1 = at('landing', 'arr_rt');
    return t0 == null && t1 == null ? null : [t0, t1];
}

// The same, airborne by FR24's runway times rather than by a reading above 50 m: on a
// low-pressure day the barometric altitude reads 0 well short of the runway. What the
// aircraft would have read on each runway is its climb (descent) carried on to the take-off
// (landing) time; the readings stuck at 0 before touchdown follow that line too.
function _aboveRunwayTimes(altitude, timestamps, t0, t1, ends) {
    const start = t0 ?? -Infinity, end = t1 ?? Infinity;
    const clean = [];
    altitude.forEach((a, i) => {
        const t = timestamps[i];
        if (t && t > start && t < end && a > 50) clean.push(i);
    });
    if (!clean.length) return altitude.map(() => 0);
    // The line through four readings at one end, when they are close to its runway time
    function line(idx, T) {
        if (T == null || idx.length < 2 || Math.abs(timestamps[idx[0]] - T) > 180) return null;
        const n = idx.length, ts = idx.map(i => timestamps[i]), as = idx.map(i => altitude[i]);
        const mt = ts.reduce((s, v) => s + v, 0) / n, ma = as.reduce((s, v) => s + v, 0) / n;
        let num = 0, den = 0;
        ts.forEach((t, k) => { num += (t - mt) * (as[k] - ma); den += (t - mt) ** 2; });
        const slope = den ? num / den : 0;
        return t => ma + slope * (t - mt);
    }
    const climb = line(clean.slice(0, 4), t0);
    const descent = line(clean.slice(-4).reverse(), t1);
    let r0 = climb ? climb(t0) : null, r1 = descent ? descent(t1) : null;
    if (ends && ends.field1 != null) r1 = ends.field1;   // read on the runway (_touchdown)
    [r0, r1] = _withElevations(r0, r1, ends);
    const base0 = r0 ?? r1 ?? 0, base1 = r1 ?? r0 ?? 0;
    // Not landed yet (no landing time): along the route by distance, not time
    const along = !isFinite(end) ? _alongRoute(ends) : null;
    const tFirst = timestamps[clean[0]], tLast = timestamps[clean[clean.length - 1]];
    const heights = altitude.map((a, i) => {
        const t = timestamps[i];
        if (!t || t <= start || t >= end) return 0;
        let reading = a;
        if (!(a > 50)) {
            if (t < tFirst && climb) reading = climb(t);
            else if (t > tLast && descent) reading = descent(t);
        }
        const f = along ? along(i)
            : isFinite(start) && isFinite(end) ? (t - start) / (end - start) : (isFinite(start) ? 0 : 1);
        return Math.max(0, reading - (base0 + (base1 - base0) * f));
    });
    return _smoothRunwayEnds(heights, timestamps, t0, t1);
}

// The first and last minute and a half off and onto the runway as one smooth climb (descent)
// rather than FR24's first readings: in 25 ft steps and late, they rise in lumps (82 m in
// 7 s, then flat). h(x) = a·x + b·x², x the seconds from the runway time, 0 there, fitted
// to the heights of that stretch, then blended back into them by its end.
const RUNWAY_SMOOTH_S = 90;
function _smoothRunwayEnds(heights, timestamps, t0, t1) {
    const out = heights.slice();
    [[t0, 1], [t1, -1]].forEach(([T, dir]) => {
        if (T == null || !isFinite(T)) return;
        const idx = [];
        heights.forEach((h, i) => {
            const x = timestamps[i] ? (timestamps[i] - T) * dir : NaN;
            if (x > 0 && x <= RUNWAY_SMOOTH_S && h > 0) idx.push([i, x]);
        });
        if (idx.length < 3) return;
        // Least squares through the origin: [Σx² Σx³; Σx³ Σx⁴]·[a b] = [Σxh Σx²h]
        let s2 = 0, s3 = 0, s4 = 0, sh1 = 0, sh2 = 0;
        idx.forEach(([i, x]) => { const h = heights[i]; s2 += x * x; s3 += x ** 3; s4 += x ** 4; sh1 += x * h; sh2 += x * x * h; });
        const det = s2 * s4 - s3 * s3;
        let a = det ? (sh1 * s4 - sh2 * s3) / det : sh1 / s2, b = det ? (s2 * sh2 - s3 * sh1) / det : 0;
        // Climbing (descending) all the way, never back: else a straight line
        if (a < 0 || a + 2 * b * RUNWAY_SMOOTH_S < 0) { a = sh1 / s2; b = 0; }
        if (a <= 0) return;
        idx.forEach(([i, x]) => {
            const w = x / RUNWAY_SMOOTH_S, fit = a * x + b * x * x;
            out[i] = Math.max(0, fit * (1 - w) + heights[i] * w);
        });
    });
    return out;
}

async function build3DFlightLayer(map, trips, options = {}) {
    // deck.gl's overlay can't follow MapLibre's globe projection (the track
    // detaches from the sphere), so skip 3D entirely in globe mode — the flat 2D
    // lines render as before.
    const proj = map.getProjection && map.getProjection();
    if (proj && proj.type === 'globe') return null;

    // Every live refresh brings a longer track: the layer already up is updated in place,
    // not torn down and built anew, which flashed and put the aircraft back at its last
    // fix (until its next move) every time. Its new fixes' drop lines just appear.
    if (map._flight3dUpdate) return map._flight3dUpdate(trips);

    const exaggeration = options.exaggeration ?? MapConfig.altitudeExaggeration;
    // Draw a vertical line from every Nth track point down to the ground track.
    const dropStep = options.dropLineStep ?? 1;

    let flights = [];
    let dropLines = [];  // vertical segments {source:[lng,lat,alt], target:[lng,lat,0]}
    let aircraft = [];   // live positions, drawn at their real altitude
    const compute = trips => {
    flights = []; dropLines = []; aircraft = [];
    trips.forEach(trip => {
        // livePath when the flight is being tracked: the altitude array is parallel to
        // the live track, not to the stored two-point route.
        const path = _coerceArray(trip.livePath || trip.path);   // [[lat, lng], ...]
        let altitude = _coerceArray(trip.altitude);  // [m, ...] parallel to path
        if (!path || !altitude || altitude.length < 2) return;
        altitude = _aboveAirports(altitude, _coerceArray(trip.timestamps), trip.trip.waypoints, {
            elevations: trip.liveElevations || trip.trip.elevations,
            coords: path,
            // Where it is going: the logged arrival while it flies, its track's end once down
            dest: trip.liveTracked && trip.liveDestinationPoint ? trip.liveDestinationPoint
                : (_coerceArray(trip.path) || [])[(_coerceArray(trip.path) || []).length - 1]
        });
        const n = Math.min(path.length, altitude.length);
        const coords = [];
        for (let i = 0; i < n; i++) {
            const lng = path[i][1], lat = path[i][0];
            const z = (altitude[i] || 0) * exaggeration;
            // A point repeating the one before (an aircraft parked at its stand) is left out:
            // a zero-length segment makes deck's joints blow up into a wedge along the line.
            const prev = coords[coords.length - 1];
            if (prev && prev[0] === lng && prev[1] === lat && prev[2] === z) continue;
            coords.push([lng, lat, z]);
            if (i % dropStep === 0 && z > 0) {
                dropLines.push({ source: [lng, lat, z], target: [lng, lat, 0] });
            }
        }
        flights.push({ uid: trip.trip.uid, coords });

        // An in-flight aircraft belongs at the tip of its own altitude profile, not on
        // the ground below it. A DOM marker cannot carry a z coordinate, so the live
        // position is drawn here instead and the flat marker hides while 3D is on.
        // Not once it has arrived (landed, at its stand): the trip page shows none then
        if (trip.liveTracked && trip.liveDestination && coords.length >= 2) {
            const tip = coords[coords.length - 1];
            // Measured over several points: consecutive samples can be metres apart and
            // a bearing across those jitters the aircraft on every refresh.
            const back = coords[Math.max(0, coords.length - 6)];
            aircraft.push({ position: tip, angle: _bearingDeg(back, tip), uid: trip.trip.uid });
        }
    });
    };
    compute(trips);

    // No premium 3D flights on this page -> no overlay, no toggle, no nagging.
    if (flights.length === 0) return null;

    const airColor = _hexToRgbArray(
        (MapConfig.transportTypes.find(t => t.id === 'air') || {}).color
    );

    let overlay = null, render = null, lastMoved = {}, heights = {};
    async function buildOverlay() {
        if (overlay) return;
        const deck = await loadDeckGL();  // only loaded when the viewer wants 3D
        const pathLayer = data => new deck.PathLayer({
            id: 'flight-3d',
            data: data,
            getPath: d => d.coords,
            getColor: airColor,
            widthUnits: 'pixels',
            getWidth: 2,
            widthMinPixels: 1,
            jointRounded: true,
            capRounded: true
        });
        // Translucent "wall" of vertical droplines connecting the 3D track to its
        // 2D ground projection, so the altitude profile reads even when nearly flat.
        const dropLayer = data => new deck.LineLayer({
            id: 'flight-3d-drops',
            data: data,
            getSourcePosition: d => d.source,
            getTargetPosition: d => d.target,
            getColor: [...airColor, 70],
            getWidth: 1,
            widthUnits: 'pixels',
            widthMinPixels: 1
        });
        // Overlaid (not interleaved): deck.gl draws in its own canvas on top of the
        // map and still camera-syncs pitch for the 3D effect. Interleaved mode shares
        // MapLibre's WebGL context and can blank the (raster) base map.
        // The live aircraft, sitting on top of its altitude profile. IconLayer angles
        // counter-clockwise from the icon's own orientation, and the silhouette points
        // north, so a clockwise compass bearing goes in negated.
        const aircraftLayer = data => new deck.IconLayer({
            id: 'flight-3d-aircraft',
            data: data,
            getPosition: d => d.position,
            getAngle: d => -d.angle,
            getSize: PLANE_ICON_PX,
            sizeUnits: 'pixels',
            // Always over its own altitude line: depth-tested, the line it sits at the tip
            // of (same height) cuts through it. (deck 9: depthCompare, v8's depthTest.)
            parameters: { depthCompare: 'always', depthWriteEnabled: false },
            // Laid in the map plane, not billboarded at the camera: a billboarded icon
            // is angled in screen space, so rotating the map would leave the aircraft
            // pointing the same way on screen while its track turned underneath it.
            billboard: false,
            // The `id` is not optional in practice: deck caches icons by it, and
            // without one the icon can silently fail to rasterise and nothing draws.
            getIcon: () => ({
                id: 'live-plane-2', url: _PLANE_SVG, width: PLANE_ICON_PX * 3, height: PLANE_ICON_PX * 3,
                anchorY: PLANE_ICON_PX * 1.5, mask: false
            }),
            // It stands in for the flat marker it hides, so it answers clicks like it.
            // deck picks through the map's own events: its canvas takes none itself.
            pickable: !!options.onAircraftClick,
            onClick: info => {
                if (info.object) options.onAircraftClick(info.object.uid);
                return true;
            }
        });
        overlay = new deck.MapboxOverlay({ interleaved: false, layers: [] });
        map.addControl(overlay);
        _lowerDeckCanvas(map);
        // Drawn in the same frame as the map, not on deck's own next one: a frame apart,
        // the track and the aircraft shake against the map while it moves (followed).
        map.on('render', syncDeck);
        // Moved with the flat marker it stands in for (setFlight3DAircraft): along the
        // route with the clock between two fixes, at the altitude of the last one.
        // Its line runs on from the last fix to where it is now, at that fix's height, so
        // it never flies ahead of its own track. The drop lines stay at the real fixes only:
        // the last of them is where it was last seen.
        // Drawn where the flat markers last were (`lastMoved`), so a refresh carries the
        // aircraft on from there rather than back to its fix.
        // Its height: the last fix's, carried on at its climb or descent rate (`dz`, from the
        // trip page), never below the ground; a new fix's difference glides out over 8 s.
        render = () => {
            const t = Date.now();
            const data = shown = aircraft.map(a => {
                const m = lastMoved[a.uid];
                if (!m) return a;
                const target = Math.max(0, a.position[2] + (m.dz || 0));
                let z = heights[a.uid];
                if (!z) z = heights[a.uid] = { tip: a.position[2], shown: target, offset: 0, at: t };
                if (z.tip !== a.position[2]) Object.assign(z, { tip: a.position[2], offset: z.shown - target, at: t });
                const k = Math.min(1, (t - z.at) / 8000), left = 1 - k * k * (3 - 2 * k);
                z.shown = Math.max(0, target + z.offset * left);
                return { ...a, position: [m.position[0], m.position[1], z.shown], angle: m.angle };
            });
            const now = {};
            data.forEach(a => { now[a.uid] = a.position; });
            // Extended to the aircraft only when it is somewhere else than its last fix: the
            // same point twice (stood still, landed) is a zero-length segment, drawn as a wedge
            const paths = flights.map(f => {
                const to = now[f.uid], last = f.coords[f.coords.length - 1];
                const apart = to && last && _metresBetween(last, to) > 1;
                return apart ? { ...f, coords: f.coords.concat([to]) } : f;
            });
            overlay.setProps({ layers: [dropLayer(dropLines), pathLayer(paths), aircraftLayer(data)] });
        };
        render();
        setAircraft = moved => { lastMoved = moved; render(); };
        // Conditional on there actually being one: if no live aircraft is drawn up
        // here, hiding the ground marker would leave the flight with no marker at all.
        // Remembered for markers made after this (createCustomMarker reads it).
        map._flight3dAircraftUp = aircraft.length > 0;
        if (aircraft.length) _setGroundAircraftVisible(map, false);
    }
    let setAircraft = null, shown = [];
    function syncDeck() {
        const deckInstance = overlay && overlay._deck;   // not public API: guarded
        if (deckInstance && deckInstance.redraw) deckInstance.redraw('map render');
    }
    function removeOverlay() {
        map.off('render', syncDeck);
        if (overlay) { map.removeControl(overlay); overlay = null; }
        setAircraft = null;
        render = null;
        shown = [];
        // Back to 2D: the ground marker is the only aircraft left, so restore it.
        map._flight3dAircraftUp = false;
        _setGroundAircraftVisible(map, true);
    }
    map._flight3dRemove = removeOverlay;
    // The next refresh's data, into the layer as it is (see the top)
    map._flight3dUpdate = async trips => {
        compute(trips);
        if (!flights.length) {
            removeOverlay();
            return null;
        }
        if (render) {
            render();
            map._flight3dAircraftUp = aircraft.length > 0;
            _setGroundAircraftVisible(map, !aircraft.length);
        }
        return overlay;
    };
    map._flight3dSetAircraft = moved => { if (setAircraft) setAircraft(moved); };
    // Where the aircraft `uid` is drawn on screen, up at its altitude ([x, y], CSS px), or
    // null without a 3D view: what its popup is pinned to rather than the ground below it.
    // (The overlay's deck instance is not public API: guarded, falls back to the ground.)
    // The height (m, as drawn) the 3D view puts the aircraft `uid` at, or null without one
    map._flight3dAltitude = uid => {
        const a = overlay && shown.find(x => x.uid == uid);
        return a ? a.position[2] || 0 : null;
    };
    map._flight3dScreenPoint = uid => {
        const a = shown.find(x => x.uid == uid);
        const deckInstance = overlay && overlay._deck;
        const viewport = a && deckInstance && deckInstance.getViewports && deckInstance.getViewports()[0];
        return viewport ? viewport.project(a.position) : null;
    };

    _addFlight3DToggleControl(map, async (on) => {
        setFlight3DViewEnabled(on);
        if (on) await buildOverlay(); else removeOverlay();
    }, options.toggleTitle);

    if (flight3DViewEnabled()) await buildOverlay();
    return overlay;
}

// Where the 3D view's aircraft are now: {uid: {position: [lng, lat], angle}}, from the
// flat markers they stand in for. Nothing without a 3D view up.
function setFlight3DAircraft(map, moved) {
    if (map._flight3dSetAircraft) map._flight3dSetAircraft(moved);
}
// How near its destination a live flight stops being drawn on to it (on the approach):
// the airport's point is its middle, not the runway it is landing on. As the server's
// APPROACH_KM.
const LIVE_REMAINDER_NEAR_KM = 30;
// Whether a live flight's remaining leg to its destination is drawn: far from it, yes;
// on the approach, landed, or with nowhere logged, no.
function liveRemainderShown(trip) {
    const path = trip.livePath, dest = trip.liveDestination;
    if (!trip.liveTracked || !dest || !path || !path.length) return false;
    const last = path[path.length - 1];   // [lat, lng]
    const k = Math.cos(last[0] * Math.PI / 180);
    const km = Math.hypot((dest[1] - last[1]) * k, dest[0] - last[0]) * 111.32;
    return km > LIVE_REMAINDER_NEAR_KM;
}

// Ground distance between two [lng, lat(, z)] points, metres (flat: they are close)
function _metresBetween(a, b) {
    const k = Math.cos(a[1] * Math.PI / 180);
    return Math.hypot((b[0] - a[0]) * k, b[1] - a[1]) * 111320;
}

// The height the 3D view draws the aircraft `uid` at (m), or null without a 3D view.
function flight3DAltitude(map, uid) {
    return map._flight3dAltitude ? map._flight3dAltitude(uid) : null;
}
// The screen point the 3D view draws the aircraft `uid` at, or null (see build3DFlightLayer).
function flight3DScreenPoint(map, uid) {
    return map._flight3dScreenPoint ? map._flight3dScreenPoint(uid) : null;
}

window.MapLibreUtils = {
    MapConfig,
    build3DFlightLayer,
    setFlight3DAircraft,
    liveRemainderShown,
    flight3DScreenPoint,
    flight3DAltitude,
    planeSvg,
    initializeMapLibre,
    changeBackground,
    tripShadowColor,
    isVectorTileServer,
    getVectorStyleUrl,
    createRasterStyle,
    getTileServerConfig,
    createGeodesicLine,
    liveOriginBridge,
    computeTimeStatus,
    applyLiveTracks,
    tripToFeature,
    buildTripLayers,
    addTripSliceLayers,
    tripLineColor,
    yearColors,
    yearSpread,
    setYearSpread,
    toggleYearLegend,
    yearLegendHtml,
    updateLayerVisibility,
    fitBoundsToVisibleFeatures,
    modifyVectorStyle
};