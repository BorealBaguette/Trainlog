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
    jawgAllowedLangs: ["de", "en", "es", "fr", "it", "ja", "ko", "nl", "ru", "zh"],
    vectorStylePaths: {
        "jawg-streets-v2":'/getVectorStyle/{language}/jawg-streets.json',
        "jawg-lagoon-v2":'/getVectorStyle/{language}/jawg-lagoon.json',
        "trainlog-lagoon-v2":'/getVectorStyle/{language}/trainlog-lagoon.json',
        "dark-train":       '/getVectorStyle/{language}/trainlog-dark.json',
        "ofm-liberty": 'https://tiles.openfreemap.org/styles/liberty',
        "ofm-bright": 'https://tiles.openfreemap.org/styles/bright',
        "ofm-positron": 'https://tiles.openfreemap.org/styles/positron'
    }
};

// Map initialization function with vector tile support
async function initializeMapLibre(options = {}) {
    const {
        container = 'map',
        tileserver = 'osm',
        useGlobe = false,
        userLanguage = 'en',
        center = [10, 50],
        zoom = 5,
        styleUrl = null
    } = options;

    let mapStyle;

    // Handle different tile server types
    if (tileserver === 'none') {
        mapStyle = { version: 8, sources: {}, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#f8f9fa' } }] };
        if (useGlobe) mapStyle.projection = { type: 'globe' };
    } else if (isVectorTileServer(tileserver) || styleUrl) {
        // Load vector style
        const url = styleUrl || getVectorStyleUrl(tileserver, userLanguage);
        try {
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`Failed to load style: ${response.status}`);
            }
            mapStyle = await response.json();
            
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
        mapStyle = createRasterStyle(tileserver, userLanguage, useGlobe);
    }

    // Create map
    const map = new maplibregl.Map({
        container: container,
        style: mapStyle,
        center: center,
        zoom: zoom,
        doubleClickZoom: false
    });

    // Add navigation controls
    map.addControl(new maplibregl.NavigationControl(), 'top-right');

    return map;
}

// Check if tileserver is a vector tile server
function isVectorTileServer(tileserver) {
    const vectorServers = [
        'jawg-streets-v2',
        'jawg-lagoon-v2',
        'trainlog-lagoon-v2',
        'dark-train',
        'ofm-liberty',
        'ofm-bright',
        'ofm-positron'
    ];
    return vectorServers.includes(tileserver);
}

// Get vector style URL based on tileserver
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
            }else if (serverType && serverType.startsWith("openrailwaymap-")) {
                var baseStyle = "osm"; // default
                if (serverType.includes(".")) {
                    var parts = serverType.split(".");
                    serverType = parts[0];
                    baseStyle = parts[1];
                }
                tileUrl = `https://tiles.trainlog.me/tile/${serverType}/{x}/{y}/{z}?base_style=${baseStyle}`;
                attribution = '© Openrailwaymap, © OpenStreetMap contributors, © Jawg';
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

// Build trip layers for the map
function buildTripLayers(map, trips, transportTypes, options = {}) {
    const {
        onLayerClick = null,
        visibleTypes = new Set(transportTypes.map(t => t.id)),
        showShadows = true
    } = options;

    // Create features from trips
    const features = trips.map(trip => {
        trip = computeTimeStatus(trip);
        if (trip.trip.type == 'helicopter'){
            trip.trip.type = 'air';
        }

        try {
            let coords = trip.path.map(c => [c[1], c[0]]);
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
                    year: trip.trip.start_datetime !== 1 && trip.trip.start_datetime !== -1
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
    }).filter(feature => feature !== null);

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
                            'line-color': '#000000',
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
function fitBoundsToVisibleFeatures(map, features, filters, type=null) {
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
                map.fitBounds(bounds, { padding: 50 });
            } else {
                // Desktop: large padding with offset
                map.fitBounds(bounds, {
                    padding: 300,
                    offset: [-100, 0]
                });
            }
        } else {
            map.fitBounds(bounds, { padding: 50 });
        }
    }
    return new Promise(resolve => {
        map.once('moveend', () => {
            resolve({
                center: map.getCenter(),
                zoom: map.getZoom()
            });
        });
    });
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
window.MapLibreUtils = {
    MapConfig,
    initializeMapLibre,
    isVectorTileServer,
    getVectorStyleUrl,
    createRasterStyle,
    getTileServerConfig,
    createGeodesicLine,
    computeTimeStatus,
    buildTripLayers,
    updateLayerVisibility,
    fitBoundsToVisibleFeatures,
    modifyVectorStyle
};