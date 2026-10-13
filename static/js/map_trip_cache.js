// Device cache + loader for the trips map (new_map.html).
//
// The map used to download every trip with its full-resolution path as nested
// JSON arrays, then hand that whole structure to localforage. Structured-cloning
// millions of [lat, lng] pairs into IndexedDB is slow everywhere and on iPhones
// often fails outright (quota / eviction), and a half-written cache then forced
// a full re-download on every visit.
//
// Now:
//   - trips metadata (no geometry) comes from /mapTrips; the last copy is kept
//     and revalidated by hash, so an unchanged map costs one tiny request;
//   - paths are Google encoded polylines (several times smaller than JSON),
//     stored one IndexedDB record per trip as a plain string — cheap to clone,
//     written in small batches, so a failed write only loses that batch;
//   - each path carries a version and is only re-downloaded when it changed;
//   - two levels of detail, each its own store: a simplified `overview` (~22 m,
//     what the map shows zoomed out, ~10x fewer points) and the `full` path,
//     only fetched for trips in view when zoomed in;
//   - records are read per trip when needed, never the whole store at once.
//
// Every cache operation is best effort with a timeout: Safari's IndexedDB can
// hang (notably right after the page is restored), and a broken cache must
// only ever mean "download it", never a map that doesn't load.
const MapTripCache = (() => {
    const DB_NAME = 'trainlog-map';
    const DB_VERSION = 2;  // 2: + 'overview' store
    const TIMEOUT_MS = 4000;

    let dbPromise = null;

    function withTimeout(promise, fallback) {
        return Promise.race([
            promise.catch(() => fallback),
            new Promise(resolve => setTimeout(() => resolve(fallback), TIMEOUT_MS))
        ]);
    }

    function openDb() {
        if (dbPromise) return dbPromise;
        dbPromise = withTimeout(new Promise((resolve, reject) => {
            if (!window.indexedDB) return reject(new Error('no IndexedDB'));
            const req = indexedDB.open(DB_NAME, DB_VERSION);
            req.onupgradeneeded = () => {
                const db = req.result;
                if (!db.objectStoreNames.contains('meta')) db.createObjectStore('meta');
                if (!db.objectStoreNames.contains('paths')) db.createObjectStore('paths');
                if (!db.objectStoreNames.contains('overview')) db.createObjectStore('overview');
            };
            req.onsuccess = () => {
                const db = req.result;
                // Let a newer page version upgrade the schema instead of blocking it
                db.onversionchange = () => db.close();
                resolve(db);
            };
            req.onerror = () => reject(req.error);
        }), null);
        return dbPromise;
    }

    // Run fn(store) in a transaction; resolves with fn's IDBRequest result (if
    // any) once the transaction completes, or `fallback` on any failure.
    async function run(storeName, mode, fn, fallback) {
        const db = await openDb();
        if (!db) return fallback;
        return withTimeout(new Promise((resolve, reject) => {
            const tx = db.transaction(storeName, mode);
            const req = fn(tx.objectStore(storeName));
            tx.oncomplete = () => resolve(req ? req.result : undefined);
            tx.onerror = () => reject(tx.error);
            tx.onabort = () => reject(tx.error);
        }), fallback);
    }

    function getMeta(key) {
        return run('meta', 'readonly', store => store.get(key), null);
    }

    function putMeta(key, value) {
        return run('meta', 'readwrite', store => { store.put(value, key); }, null);
    }

    function getAllMeta() {
        return run('meta', 'readonly', store => store.getAll(), null);
    }

    // Path records are "<version> <polyline>" strings: a space never occurs
    // in a polyline (alphabet is ASCII 63-126), and one flat string per trip is
    // the cheapest thing to put through IndexedDB.
    // Reads the given keys in one transaction -> Map uid -> { v, p }
    async function getPaths(storeName, uids) {
        const found = new Map();
        const db = await openDb();
        if (!db || !uids.length) return found;
        return withTimeout(new Promise((resolve, reject) => {
            const tx = db.transaction(storeName, 'readonly');
            const store = tx.objectStore(storeName);
            uids.forEach(uid => {
                const req = store.get(uid);
                req.onsuccess = () => {
                    const value = req.result;
                    const sep = typeof value === 'string' ? value.indexOf(' ') : -1;
                    if (sep > 0) found.set(uid, { v: Number(value.slice(0, sep)), p: value.slice(sep + 1) });
                };
            });
            tx.oncomplete = () => resolve(found);
            tx.onerror = () => reject(tx.error);
            tx.onabort = () => reject(tx.error);
        }), found);
    }

    function putPaths(storeName, entries) {
        return run(storeName, 'readwrite', store => {
            entries.forEach(([uid, v, p]) => store.put(v + ' ' + p, uid));
        }, null);
    }

    function getPathKeys(storeName) {
        return run(storeName, 'readonly', store => store.getAllKeys(), null);
    }

    function deletePaths(storeName, uids) {
        return run(storeName, 'readwrite', store => {
            uids.forEach(uid => store.delete(uid));
        }, null);
    }

    // Google encoded polyline -> [[lng, lat], ...]. PostGIS encodes lat first.
    function decodePolyline(str, precision = 6) {
        const factor = Math.pow(10, precision);
        const coords = [];
        let index = 0, lat = 0, lng = 0;
        while (index < str.length) {
            let b, shift = 0, result = 0;
            do {
                b = str.charCodeAt(index++) - 63;
                result |= (b & 0x1f) << shift;
                shift += 5;
            } while (b >= 0x20);
            lat += (result & 1) ? ~(result >> 1) : (result >> 1);

            shift = 0; result = 0;
            do {
                b = str.charCodeAt(index++) - 63;
                result |= (b & 0x1f) << shift;
                shift += 5;
            } while (b >= 0x20);
            lng += (result & 1) ? ~(result >> 1) : (result >> 1);

            coords.push([lng / factor, lat / factor]);
        }
        return coords;
    }

    // One level of detail of the trips' paths: memory, then the device
    // store, then the server. Decoded features are kept with their bbox.
    function createPathSet({ pathsUrl, storeName, lod, batchSize, parallel }) {
        const paths = new Map();       // uid -> { v, p } (encoded)
        const features = new Map();    // uid -> { v, time, feature, bbox }
        const inflight = new Map();    // uid -> Promise
        const missing = new Map();     // uid -> version the server had no path for
        const triedCache = new Set();  // uids already looked up on the device
        let precision = lod === 'overview' ? 5 : 6;

        function hasPath(trip) {
            const entry = paths.get(trip.trip.uid);
            return (entry && entry.v === trip.v) || missing.get(trip.trip.uid) === trip.v;
        }

        async function fetchBatch(trips) {
            const res = await fetch(pathsUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: trips.map(t => t.trip.uid), lod })
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            if (data.precision) precision = data.precision;
            const got = new Set();
            data.paths.forEach(([uid, v, p]) => {
                paths.set(uid, { v, p });
                got.add(uid);
            });
            trips.forEach(t => { if (!got.has(t.trip.uid)) missing.set(t.trip.uid, t.v); });
            if (data.paths.length) putPaths(storeName, data.paths);
        }

        // Make sure the given trips' paths are in memory: device cache first,
        // then the server in batches (a few at a time). Resolves when done;
        // failed batches are logged and simply left missing.
        async function ensure(trips) {
            const lookup = trips.filter(t => !hasPath(t) && !triedCache.has(t.trip.uid));
            if (lookup.length) {
                lookup.forEach(t => triedCache.add(t.trip.uid));
                const cached = await getPaths(storeName, lookup.map(t => t.trip.uid));
                // Anything downloaded meanwhile wins over the cache
                cached.forEach((entry, uid) => { if (!paths.has(uid)) paths.set(uid, entry); });
            }

            const waits = [];
            const todo = [];
            trips.forEach(t => {
                if (hasPath(t)) return;
                const pending = inflight.get(t.trip.uid);
                if (pending) waits.push(pending);
                else todo.push(t);
            });

            const batches = [];
            for (let i = 0; i < todo.length; i += batchSize) batches.push(todo.slice(i, i + batchSize));

            let next = 0;
            const worker = async () => {
                while (next < batches.length) {
                    // A queued batch may have been overtaken by a more urgent
                    // ensure() (the user switched year mid-way): skip what it got
                    const batch = batches[next++].filter(t => !hasPath(t) && !inflight.has(t.trip.uid));
                    if (!batch.length) continue;
                    const p = fetchBatch(batch).catch(err => console.error('Trips map: path batch failed', err));
                    batch.forEach(t => inflight.set(t.trip.uid, p));
                    await p;
                    batch.forEach(t => inflight.delete(t.trip.uid));
                }
            };
            const workers = [];
            for (let i = 0; i < Math.min(parallel, batches.length); i++) workers.push(worker());
            await Promise.all(workers.concat(waits));
        }

        function entryFor(trip) {
            const uid = trip.trip.uid;
            const cached = features.get(uid);
            if (cached && cached.v === trip.v && cached.time === trip.time) return cached;
            const entry = paths.get(uid);
            if (!entry || entry.v !== trip.v) return null;
            const feature = MapLibreUtils.tripToFeature({
                trip: trip.trip,
                time: trip.time,
                coords: decodePolyline(entry.p, precision)
            });
            let bbox = null;
            if (feature) {
                bbox = [Infinity, Infinity, -Infinity, -Infinity];
                feature.geometry.coordinates.forEach(([lng, lat]) => {
                    if (lng < bbox[0]) bbox[0] = lng;
                    if (lat < bbox[1]) bbox[1] = lat;
                    if (lng > bbox[2]) bbox[2] = lng;
                    if (lat > bbox[3]) bbox[3] = lat;
                });
            }
            const result = { v: trip.v, time: trip.time, feature, bbox };
            features.set(uid, result);
            return result;
        }

        // Feature for a trip whose path is loaded (null otherwise / if unusable)
        function featureFor(trip) {
            const entry = entryFor(trip);
            return entry ? entry.feature : null;
        }

        // [west, south, east, north] of the trip's path, null if not loaded
        function bboxFor(trip) {
            const entry = entryFor(trip);
            return entry ? entry.bbox : null;
        }

        // Drop stored records whose trip is not in `keep`
        async function prune(keep) {
            const keys = await getPathKeys(storeName);
            if (!keys) return;
            const stale = keys.filter(uid => !keep.has(uid));
            if (stale.length) deletePaths(storeName, stale);
        }

        return { ensure, featureFor, bboxFor, prune };
    }

    // Loader for one map: the trips list (stale-while-revalidate) and the two
    // levels of detail of their paths.
    //   tripsUrl / pathsUrl — the /mapTrips and /mapPaths endpoints
    function createLoader({ tripsUrl, pathsUrl, batchSize = 200, parallel = 3 }) {
        const metaKey = 'trips:' + tripsUrl;
        let currentUids = new Set();

        const overview = createPathSet({ pathsUrl, storeName: 'overview', lod: 'overview', batchSize: batchSize * 2, parallel });
        const full = createPathSet({ pathsUrl, storeName: 'paths', lod: 'full', batchSize, parallel });

        // Rows: [uid, type, start_datetime, utc_start, utc_end, origin, destination, path_version]
        function rowToTrip(row) {
            return {
                v: row[7],
                trip: {
                    uid: row[0],
                    type: row[1] === 'helicopter' ? 'air' : row[1],
                    start_datetime: row[2],
                    utc_filtered_start_datetime: row[3],
                    utc_filtered_end_datetime: row[4],
                    origin_station: row[5],
                    destination_station: row[6]
                }
            };
        }

        // Stale-while-revalidate: the page draws loadCached()'s trips right
        // away, while revalidate() asks the server whether they are current.
        let cachedMeta;        // undefined = not read yet, null = none on device
        let revalidating = null;

        // Trips from the device copy, or null when there is none
        async function loadCached() {
            if (cachedMeta === undefined) {
                const meta = await getMeta(metaKey);
                cachedMeta = meta && Array.isArray(meta.rows) ? meta : null;
            }
            if (!cachedMeta) return null;
            currentUids = new Set(cachedMeta.rows.map(row => row[0]));
            return cachedMeta.rows.map(rowToTrip);
        }

        // Fresh trips when they differ from the device copy, null when that
        // copy is current — or when the server can't be reached but we have
        // one. Throws only when there is nothing at all to show.
        function revalidate() {
            if (!revalidating) revalidating = (async () => {
                await loadCached();
                try {
                    const url = tripsUrl + (cachedMeta ? '?h=' + encodeURIComponent(cachedMeta.h) : '');
                    const res = await fetch(url, { credentials: 'same-origin' });
                    if (!res.ok) throw new Error('HTTP ' + res.status);
                    const data = await res.json();
                    if (data.unchanged && cachedMeta) return null;
                    cachedMeta = { h: data.h, rows: data.rows };
                    putMeta(metaKey, cachedMeta);
                    currentUids = new Set(data.rows.map(row => row[0]));
                    return data.rows.map(rowToTrip);
                } catch (err) {
                    if (!cachedMeta) throw err;
                    console.warn('Trips map: server unreachable, keeping cached trips', err);
                    return null;
                }
            })();
            return revalidating;
        }

        // Both steps in one, for callers that don't draw in between
        async function loadTrips() {
            const cached = await loadCached();
            return (await revalidate()) || cached;
        }

        // Drop stored paths that no cached map refers to any more (deleted
        // trips; changed ones are simply overwritten). Only touches the device
        // cache, never what's in memory.
        async function prune() {
            // Prune against the server's list, not a possibly stale cached one
            if (revalidating) await revalidating.catch(() => {});
            const metas = await getAllMeta();
            if (!metas) return;  // can't tell what other maps still need
            const keep = new Set(currentUids);
            metas.forEach(m => (m && m.rows || []).forEach(row => keep.add(row[0])));
            await overview.prune(keep);
            await full.prune(keep);
        }

        return { loadCached, revalidate, loadTrips, overview, full, prune };
    }

    // Wipe the whole cache (settings page "clear cache")
    function clear() {
        dbPromise = null;
        return new Promise(resolve => {
            try {
                const req = indexedDB.deleteDatabase(DB_NAME);
                req.onsuccess = req.onerror = req.onblocked = () => resolve();
            } catch (e) {
                resolve();
            }
        });
    }

    return { createLoader, decodePolyline, clear };
})();
