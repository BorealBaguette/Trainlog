// Determine which marker icons to use
const startIconUrl = window.colorblindMode
    ? '/static/images/icons/marker-icon-2x-purple.png'
    : '/static/images/icons/marker-icon-2x-green.png';

const endIconUrl = window.colorblindMode
    ? '/static/images/icons/marker-icon-2x-orange.png'
    : '/static/images/icons/marker-icon-2x-red.png';
// routing.js — safe fallback if not defined by the page
window.flutterBridge = window.flutterBridge || {
  _send()      {},
  loading()    {},
  routeInfo()  {},
  routingError(){},
  saveTripDone(){},
  saveError()  {},
};

var markerIconStart = L.icon({
    iconUrl: startIconUrl,
    iconRetinaUrl: startIconUrl,
    iconSize:    [25, 41],
    iconAnchor:  [12, 41],
    popupAnchor: [1, -34],
    tooltipAnchor: [16, -28],
});

var markerIconEnd = L.icon({
    iconUrl: endIconUrl,
    iconRetinaUrl: endIconUrl,
    iconSize:    [25, 41],
    iconAnchor:  [12, 41],
    popupAnchor: [1, -34],
    tooltipAnchor: [16, -28],
});

var urlParams = new URLSearchParams(window.location.search);
var gpx = urlParams.get('gpx');
var geojson = urlParams.get('geojson');
var useAntPath = urlParams.get('antpath') === 'true' ? true : false

antpathStyles =  {
  antpath:true,
  opacity: 0.9,
  delay: 800,
  dashArray: [32, 100],
  weight: 3,
  color: "#52b0fe",
  pulseColor: "#FFFFFF",
  paused: false,
  reverse: false,
  hardwareAccelerated: true
};

var useNewRouter = false;
// Persists the ferry-split checkbox's state across re-renders (routeWhileDragging
// fires routeselected repeatedly, which fully re-creates the sidebar HTML — without
// this, an unchecked box would silently reset to checked on the next drag/reroute).
var ferrySplitEnabled = false;
// Persists the new-router profile select across the same re-renders; seeded from
// `type` the first time it's needed (see routing()).
var newRouterProfile = null;

var markergroup = new L.featureGroup(markerIconStart, markerIconEnd);

var routeDetails = null;

// Bumped on every reroute so a slow /api/electrification-preview response from an
// earlier route can't overwrite the sidebar after a newer one has already resolved.
var elecPreviewRequestId = 0;

// Last computed preview plus the exact path object it was computed for. Lets
// power-type toggles and plain re-renders reuse it instead of re-running the
// (expensive — see /api/electrification-preview) country walk server-side.
var lastElecPreview = null;
var lastElecPreviewPath = null;

// Which upstream router answered the last bus routing request (HTTP status set by
// forward_routing_core in src/routing.py). 234/235 mean the road (car) router was
// used as a fallback, so the route may follow roads buses aren't allowed on.
var busRouterCode = null;
var BUS_ROUTER_CODES = {
  231: { key: "busRouterDedicated", warn: false },
  233: { key: "busRouterDedicated", warn: false },
  234: { key: "busRouterFallback",  warn: true  },
  235: { key: "busRouterFallbackError", warn: true },
};

// Small info/warning icon shown next to the distance telling which bus router answered.
function busRouterHint() {
  var info = BUS_ROUTER_CODES[busRouterCode];
  if (!info || !texts[info.key]) return '';
  var icon = info.warn ? 'fa-triangle-exclamation' : 'fa-circle-info';
  return `<details class="route-hint${info.warn ? ' route-hint-warn' : ''}">`
       + `<summary><i class="fa-solid ${icon}"></i></summary>`
       + `<div class="route-bubble">${texts[info.key]}</div></details>`;
}

// The hint bubbles are <details>, which stay open until re-clicked — close them on any
// click outside so they behave like a popover. Listen on the capture phase: the
// leaflet-sidebar plugin stops click propagation on its content container (to keep
// clicks from reaching the map) during the bubble phase, which would otherwise
// swallow clicks made anywhere inside the sidebar before they reach this listener.
document.addEventListener('click', function (e) {
  document.querySelectorAll('#sidebar .route-hint[open]').forEach(function (d) {
    if (!d.contains(e.target)) d.removeAttribute('open');
  });
}, true);

// A bubble is anchored to its badge, so one near either edge of the panel would
// otherwise overflow and get clipped by #sidebar's overflow:auto. Nudge it back
// inside on open, and shift the caret the opposite way so it still points at the
// badge. 'toggle' doesn't bubble, hence the capture-phase listener.
function clampRouteBubble(details) {
  var bubble = details.querySelector('.route-bubble');
  var panel = document.getElementById('sidebar');
  if (!bubble || !panel) return;

  bubble.style.transform = '';
  bubble.style.removeProperty('--bubble-dx');

  var b = bubble.getBoundingClientRect();
  var p = panel.getBoundingClientRect();
  var pad = 8;
  var dx = 0;
  if (b.right > p.right - pad) dx = (p.right - pad) - b.right;
  if (b.left + dx < p.left + pad) dx = (p.left + pad) - b.left;
  if (!dx) return;

  bubble.style.transform = 'translateX(' + dx + 'px)';
  bubble.style.setProperty('--bubble-dx', dx + 'px');
}

document.addEventListener('toggle', function (e) {
  var d = e.target;
  if (d && d.matches && d.matches('#sidebar .route-hint[open]')) clampRouteBubble(d);
}, true);

(function() {
  var originalOpen = XMLHttpRequest.prototype.open;
  var originalSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function(method, url) {
    this._requestUrl = url;
    return originalOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function() {
    var self = this;
    var originalOnReadyStateChange = this.onreadystatechange;

    this.onreadystatechange = function() {
      if (self.readyState === 4 && self._requestUrl && self._requestUrl.includes('/route/')) {
        // /forwardRouting/bus picks between several upstream routers and reports which
        // one answered through the (2xx) status code — see BUS_ROUTER_CODES below.
        if (self._requestUrl.includes('/forwardRouting/bus/')) {
          busRouterCode = self.status;
        }
        if (self.status === 200) {
          // Check if this is an OSRM routing request
          try {
            var response = JSON.parse(self.responseText);
            if (response.routes && response.routes[0] && response.routes[0].details) {
              routeDetails = response.routes[0].details;
            }
          } catch(e) {
            console.error('Error parsing OSRM response:', e);
          }
        }
      }

      if (originalOnReadyStateChange) {
        return originalOnReadyStateChange.apply(this, arguments);
      }
    };

    return originalSend.apply(this, arguments);
  };
})();

// Track freehand segments (from waypoint i to i+1)
var freehandSegments = new Set();
var freehandLines = []; // transparent click-intercept polylines for freehand sections

function downloadCurrentRouteAsGeoJSON(distance) {
  var routeCoordinates = currentRoute.map(function(point) {
    return [point.lng, point.lat];
  });

  var geojsonObject = {
    "type": "Feature",
    "properties": {},
    "geometry": {
      "type": "LineString",
      "coordinates": routeCoordinates
    }
  };

  var dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(geojsonObject));
  var downloadAnchorNode = document.createElement('a');
  downloadAnchorNode.setAttribute("href", dataStr);
  downloadAnchorNode.setAttribute("download", `${origLabel}-to-${destLabel}-${distance}m.geojson`);
  document.body.appendChild(downloadAnchorNode);
  downloadAnchorNode.click();
  downloadAnchorNode.remove();
}

function recomputeRoute() {
    var excludelist = [];
    if (document.getElementById('only1435').checked) {
    excludelist.push('nonstdgauge');
    }
    if (document.getElementById('onlyelec').checked) {
    excludelist.push('notelectrified');
    }
    if (document.getElementById('nohs').checked) {
    excludelist.push('highspeed');
    }
    if (excludelist.length) {
    window.baseRouter.options.requestParameters = {exclude: excludelist.join(',')};
    } else {
    delete window.baseRouter.options.requestParameters;
    }
    control.route();
}

function handleGpxUpload(event) {
  var file = event.target.files[0];
  var reader = new FileReader();
  reader.onload = function(e) {
      var gpxData = e.target.result;
      var parser = new DOMParser();
      var xmlDoc = parser.parseFromString(gpxData, "application/xml");

      // Extracting track points from GPX data
      var trackPoints = xmlDoc.getElementsByTagName("trkpt");
      if (trackPoints.length == 0)
      {
        trackPoints = xmlDoc.getElementsByTagName("rtept");
      }
      currentRoute = []; // Initialize currentRoute here
      var totalDistance = 0;
      var totalTime = 0;
      var prevPoint = null;

      for (var i = 0; i < trackPoints.length; i++) {
          var lat = parseFloat(trackPoints[i].getAttribute("lat"));
          var lon = parseFloat(trackPoints[i].getAttribute("lon"));
          currentRoute.push({lat: lat, lng: lon});

          if (prevPoint) {
              var prevLatLng = L.latLng(prevPoint.lat, prevPoint.lng);
              var currLatLng = L.latLng(lat, lon);
              totalDistance += prevLatLng.distanceTo(currLatLng);
          }

          var timeElements = trackPoints[i].getElementsByTagName("time");
          if (timeElements.length > 0) {
              var time = new Date(timeElements[0].textContent).getTime();
              if (prevPoint && prevPoint.time) {
                  totalTime += (time - prevPoint.time) / 1000; // Convert milliseconds to seconds
              }
              prevPoint = {lat: lat, lng: lon, time: time};
          } else {
              prevPoint = {lat: lat, lng: lon};
          }
      }

      var trip_length = totalDistance; // in meters
      var estimated_trip_duration = totalTime; // in seconds

      // Now add the GPX layer to the map
      var gpxLayer = new L.GPX(gpxData, {
          async: true,
          marker_options: {
              startIconUrl: '/static/images/icons/marker-icon-2x-green.png',
              endIconUrl: '/static/images/icons/marker-icon-2x-red.png',
              shadowUrl: '/static/images/icons/marker-shadow.png'
          }
      }).on('loaded', function(e) {
          map.fitBounds(e.target.getBounds());
          var gpxContent = `<h4>GPX Route</h4>`;
          gpxContent += `<p><button id="saveTrip" type="button" onclick="saveTrip()"> Submit </button></p>`;
          sidebar.setContent(gpxContent);
          
          // You can still use leaflet polyline to visualize the route on the map
          L.polyline(currentRoute, {color: 'blue'}).addTo(map);
      }).on('error', function() {
          sidebar.setContent(errorContent);
      }).addTo(map);

      // Assign the extracted values to the appropriate variables
      newTrip["trip_length"] = trip_length;
      newTrip["estimated_trip_duration"] = estimated_trip_duration;
  };
  reader.readAsText(file);
}

function switchRouter() {
  useNewRouter = document.getElementById('newRouterToggle').checked;
  var profileSelect = document.getElementById('newRouterProfile');
  if (profileSelect) {
    profileSelect.style.display = useNewRouter ? '' : 'none';
  }

  // Show loading indicator
  sidebar.setContent(spinnerContent);

  // Clear route details when switching routers to prevent mixing data
  routeDetails = null;
  if (newTrip["details"]) {
    delete newTrip["details"];
  }

  // Update the underlying OSRM router (baseRouter) directly — the control's router is
  // a freehand wrapper with no .options of its own.
  var routerUrl = `${window.location.origin}/forwardRouting/${type}/route/v1`;
  window.baseRouter.options.serviceUrl = routerUrl;

  // Preserve existing parameters (like exclude from recomputeRoute)
  var currentParams = window.baseRouter.options.requestParameters || {};

  // Update the use_new_router parameter
  if (useNewRouter) {
    currentParams.use_new_router = 'true';
    currentParams.profile = newRouterProfile;
  } else {
    delete currentParams.use_new_router;
    delete currentParams.profile;
  }

  // Only set requestParameters if there are any parameters to set
  if (Object.keys(currentParams).length > 0) {
    window.baseRouter.options.requestParameters = currentParams;
  } else {
    delete window.baseRouter.options.requestParameters;
  }

  // Recompute the route with the new router
  control.route();
}

function buildNewRouterToggleHtml() {
  var options = [
    ["train", texts.train], ["tram", texts.tram], ["metro", texts.metro], ["all", texts.all]
  ].map(function(p) {
    var selected = newRouterProfile === p[0] ? 'selected' : '';
    return `<option value="${p[0]}" ${selected}>${p[1]}</option>`;
  }).join('');

  return `
    <div style="margin: 10px 0; padding: 10px; background-color: #f0f0f0; border-radius: 4px;">
      <label style="display: flex; align-items: center; cursor: pointer;">
        <input
          type="checkbox"
          id="newRouterToggle"
          onchange="switchRouter()"
          style="margin-right: 8px;"
          ${useNewRouter ? 'checked' : ''}
        >
        <span class="route-dist-wrap" style="display: inline-flex; align-items: center;">
          ${texts.useNewRouter}
          <details class="route-hint" style="position: static; margin-left: 6px;"><summary><i class="fa-solid fa-circle-info"></i></summary><div class="route-bubble">${texts.useNewRouterHint}</div></details>
        </span>
      </label>
      <select id="newRouterProfile" class="form-select form-select-sm" onchange="switchRouterProfile(this.value)" style="width: auto; margin-top: 8px; ${useNewRouter ? '' : 'display: none;'}">
        ${options}
      </select>
    </div>
  `;
}

function switchRouterProfile(value) {
  if (!useNewRouter) return;
  newRouterProfile = value;
  var currentParams = window.baseRouter.options.requestParameters || {};
  currentParams.profile = newRouterProfile;
  window.baseRouter.options.requestParameters = currentParams;

  sidebar.setContent(spinnerContent);
  routeDetails = null;
  if (newTrip["details"]) {
    delete newTrip["details"];
  }
  control.route();
}

window.removeWaypoint = function(index) {
  // Close any open popups
  map.closePopup();
  
  // Update freehand segments in place (reassigning would break createCustomRouter's closure).
  var toAdjust = [];
  freehandSegments.forEach(function(segIndex) {
    if (segIndex >= index) toAdjust.push(segIndex);
  });
  toAdjust.forEach(function(segIndex) {
    freehandSegments.delete(segIndex);
    if (segIndex > index) {
      freehandSegments.add(segIndex - 1); // shift down; segIndex === index is simply removed
    }
  });
  
  // Find the plan instance and remove the waypoint
  if (window.currentPlan) {
    window.currentPlan.spliceWaypoints(index, 1);
  }
};

window.toggleFreehand = function(index) {
  // Close popup
  map.closePopup();
  
  // For waypoint at index, toggle the segment FROM index TO index+1
  if (freehandSegments.has(index)) {
    freehandSegments.delete(index);
  } else {
    freehandSegments.add(index);
  }
  
  // Update marker visual appearance
  updateMarkerVisuals();
  
  // Force re-route to update the display
  if (window.currentControl) {
    window.currentControl.route();
  }
};

// Function to update all marker visual indicators
window.updateMarkerVisuals = function() {
  if (window.currentPlan && window.currentPlan._markers) {
    window.currentPlan._markers.forEach(function(marker, index) {
      if (index > 0 && index < window.currentPlan._markers.length - 1) {
        let segmentIsFreehand = freehandSegments.has(index);
        
        setTimeout(() => {
          if (marker.getElement()) {
            if (segmentIsFreehand) {
              addFreehandOverlay(marker.getElement());
            } else {
              removeFreehandOverlay(marker.getElement());
            }
          }
        }, 100);
      }
    });
  }
};

// Function to add freehand overlay
window.addFreehandOverlay = function(element) {
  // Remove existing overlay if present
  removeFreehandOverlay(element);
  
  // Create star overlay
  const overlay = document.createElement('div');
  overlay.className = 'freehand-overlay';
  overlay.innerHTML = '★';
  overlay.style.cssText = `
    position: absolute;
    top: -5px;
    right: -5px;
    color: #ff8800;
    font-size: 16px;
    font-weight: bold;
    text-shadow: 1px 1px 2px rgba(0,0,0,0.5);
    pointer-events: none;
    z-index: 1000;
    line-height: 1;
  `;
  
  element.style.position = 'relative';
  element.appendChild(overlay);
};

// Function to remove freehand overlay
window.removeFreehandOverlay = function(element) {
  const existing = element.querySelector('.freehand-overlay');
  if (existing) {
    existing.remove();
  }
};

// Custom router that handles freehand segments
function createCustomRouter(baseRouter, freehandSegments) {
  return {
    route: function(waypoints, callback, context, options) {
      // Clear previous freehand click-intercept layers
      freehandLines.forEach(function(line) { map.removeLayer(line); });
      freehandLines = [];

      // If no waypoints or only one, return early
      if (!waypoints || waypoints.length < 2) {
        callback.call(context, null, [{
          name: 'Empty route',
          coordinates: [],
          instructions: [],
          summary: { totalDistance: 0, totalTime: 0 },
          waypoints: waypoints || [],
          inputWaypoints: waypoints || []
        }]);
        return;
      }
      
      // Build segments based on freehand configuration
      var segments = [];
      var currentRouted = [];
      
      for (var i = 0; i < waypoints.length; i++) {
        currentRouted.push(waypoints[i]);
        
        // Check if the segment FROM i TO i+1 is freehand
        var segmentIsFreehand = freehandSegments.has(i);
        
        if (segmentIsFreehand && i < waypoints.length - 1) {
          // End current routed segment (if it has multiple points)
          if (currentRouted.length > 1) {
            segments.push({
              waypoints: [...currentRouted],
              isFreehand: false,
              type: 'routed'
            });
          }
          
          // Add freehand segment
          segments.push({
            waypoints: [waypoints[i], waypoints[i + 1]],
            isFreehand: true,
            type: 'freehand'
          });
          
          // Start new routed segment with the end point
          currentRouted = [waypoints[i + 1]];
        } else if (i === waypoints.length - 1) {
          // Last waypoint - finish current segment if it has multiple points
          if (currentRouted.length > 1) {
            segments.push({
              waypoints: [...currentRouted],
              isFreehand: false,
              type: 'routed'
            });
          }
        }
      }
      
      // Handle case where we have no segments (single waypoint)
      if (segments.length === 0) {
        callback.call(context, null, [{
          name: 'Single point',
          coordinates: waypoints.length > 0 ? [{lat: waypoints[0].latLng.lat, lng: waypoints[0].latLng.lng}] : [],
          instructions: [],
          summary: { totalDistance: 0, totalTime: 0 },
          waypoints: waypoints,
          inputWaypoints: waypoints
        }]);
        return;
      }
      
      // Process all segments
      var allRoutes = new Array(segments.length);
      var processedSegments = 0;
      var hasError = false;
      
      segments.forEach(function(segment, segmentIndex) {
        if (segment.isFreehand) {
          // Handle freehand segment
          var start = segment.waypoints[0].latLng;
          var end = segment.waypoints[1].latLng;
          
          // Transparent hit area — intercepts clicks so LRM's own proportional
          // mapping (which gets the wrong index for short freehand sections) never fires.
          // Coordinates are initially set to marker positions; combineRoutes will
          // update them to the actual snapped route endpoints (B_snapped → C_snapped).
          var hitArea = L.polyline([start, end], {
            weight: 12,
            opacity: 0,
            interactive: true
          }).addTo(map);
          freehandLines.push(hitArea);
          var _wpStartIdx = waypoints.indexOf(segment.waypoints[0]);
          (function(wpStartIdx, ha) {
            ha.on('click', function(e) {
              L.DomEvent.stopPropagation(e);
              if (window.currentPlan) {
                window.currentPlan.spliceWaypoints(wpStartIdx + 1, 0, L.Routing.waypoint(e.latlng));
              }
            });
          })(_wpStartIdx, hitArea);

          var distance = start.distanceTo(end);

          allRoutes[segmentIndex] = {
            coordinates: [
              {lat: start.lat, lng: start.lng},
              {lat: end.lat, lng: end.lng}
            ],
            _hitArea: hitArea,
            instructions: [{
              type: 'Straight',
              text: 'Freehand segment',
              distance: distance,
              time: 0,
              index: 0
            }],
            summary: {
              totalDistance: distance,
              totalTime: 0
            },
            inputWaypoints: segment.waypoints,
            isFreehand: true
          };
          
          processedSegments++;
          if (processedSegments === segments.length && !hasError) {
            combineRoutes(allRoutes, waypoints, callback, context);
          }
          
        } else {
          // Handle routed segment
          baseRouter.route(segment.waypoints, function(err, routes) {
            if (err) {
              hasError = true;
              callback.call(context, err);
              return;
            }
            
            if (routes && routes[0]) {
              if (!routes[0].instructions) {
                routes[0].instructions = [];
              }
              allRoutes[segmentIndex] = routes[0];
            } else {
              // Create a fallback route
              allRoutes[segmentIndex] = {
                coordinates: segment.waypoints.map(function(wp) {
                  return {lat: wp.latLng.lat, lng: wp.latLng.lng};
                }),
                instructions: [],
                summary: { totalDistance: 0, totalTime: 0 },
                inputWaypoints: segment.waypoints
              };
            }
            
            processedSegments++;
            if (processedSegments === segments.length && !hasError) {
              combineRoutes(allRoutes, waypoints, callback, context);
            }
          }, context, options);
        }
      });
    }
  };
}

function combineRoutes(routes, waypoints, callback, context) {
  var combinedCoordinates = [];
  var combinedInstructions = [];
  var totalDistance = 0;
  var totalTime = 0;
  
  routes.forEach(function(route, idx) {
    if (route && route.coordinates) {
      totalDistance += route.summary.totalDistance;
      if (!route.isFreehand) {
        totalTime += route.summary.totalTime;
      }

      if (route.isFreehand) {
        // Draw straight line from the last snapped route coordinate to the first
        // coordinate of the next routed segment, bypassing the marker positions.
        // This avoids the LRM snap-line zigzag (B_snapped→B_marker→C_marker→C_snapped).
        var nextRoute = null;
        for (var j = idx + 1; j < routes.length; j++) {
          if (routes[j] && !routes[j].isFreehand) { nextRoute = routes[j]; break; }
        }
        var freeEnd = nextRoute && nextRoute.coordinates.length > 0
          ? nextRoute.coordinates[0]
          : route.coordinates[route.coordinates.length - 1]; // last freehand WP if no next route

        if (freeEnd) {
          var freeStart = combinedCoordinates.length > 0
            ? combinedCoordinates[combinedCoordinates.length - 1]
            : route.coordinates[0];
          combinedCoordinates.push(freeEnd);

          // Update hit area to cover the actual snapped span
          if (route._hitArea) {
            route._hitArea.setLatLngs([
              L.latLng(freeStart.lat, freeStart.lng),
              L.latLng(freeEnd.lat, freeEnd.lng)
            ]);
          }
        }
      } else {
        // Avoid duplicating connection points between segments
        if (combinedCoordinates.length > 0 && route.coordinates.length > 0) {
          var lastCoord = combinedCoordinates[combinedCoordinates.length - 1];
          var firstCoord = route.coordinates[0];
          if (Math.abs(lastCoord.lat - firstCoord.lat) < 0.00001 &&
              Math.abs(lastCoord.lng - firstCoord.lng) < 0.00001) {
            combinedCoordinates = combinedCoordinates.concat(route.coordinates.slice(1));
          } else {
            combinedCoordinates = combinedCoordinates.concat(route.coordinates);
          }
        } else {
          combinedCoordinates = combinedCoordinates.concat(route.coordinates);
        }

        // Add instructions
        if (route.instructions && route.instructions.length > 0) {
          var instructionsToAdd = route.instructions.map(function(instruction) {
            return {
              ...instruction,
              index: instruction.index + combinedCoordinates.length - route.coordinates.length
            };
          });
          combinedInstructions = combinedInstructions.concat(instructionsToAdd);
        }
      }
    }
  });
  
  // Ensure we have at least one instruction
  if (combinedInstructions.length === 0) {
    combinedInstructions = [{
      type: 'Head',
      text: 'Route',
      distance: totalDistance,
      time: totalTime,
      index: 0
    }];
  }
  
  var combinedRoute = {
    name: 'Combined Route',
    coordinates: combinedCoordinates,
    instructions: combinedInstructions,
    summary: {
      totalDistance: totalDistance,
      totalTime: totalTime
    },
    waypoints: waypoints,
    inputWaypoints: waypoints
  };
  
  callback.call(context, null, [combinedRoute]);
}

// Trip types whose OSRM profile can plausibly cross a ferry leg (car/bus ferries,
// foot/bike passenger ferries, train ferries). Trams, metros, air, etc. never do.
var FERRY_SPLIT_TYPES = ['car', 'bus', 'train', 'cycle', 'walk'];
window.FERRY_SPLIT_TYPES = FERRY_SPLIT_TYPES;

// Rail-family types the electrification preview applies to. Tram/metro/funicular
// are always treated as fully electric server-side (getCountriesFromPath in
// py/utils.py); train is the only one where it's actually detected (OSM data from
// the new router) or estimated (per-country defaults) rather than known outright.
var ELEC_PREVIEW_TYPES = ['train', 'tram', 'metro', 'funicular'];

// Plural form selection lives in util.js as window.pluralize (shared, CLDR-based).

// Car-carrying rail shuttles (Channel Tunnel "Le Shuttle"/Eurotunnel, Alpine
// Autoverlad, Sylt Autozug, motorail, …) are tagged route=shuttle_train in OSM,
// which OSRM reports with mode 'ferry' — but they're trains, not ferries, so we
// must NOT offer to split them off as a ferry leg. The step name is the only
// signal OSRM gives us to tell them apart from real ferries.
var SHUTTLE_TRAIN_RE = /shuttle|eurotunnel|autoverlad|autozug|auto-?train|motorail|verladung|vereina|l[oö]tschberg|furka|oberalp|tauernschleuse|autoreisezug/i;

// Group a route's instructions into contiguous driving/ferry segments, using each
// instruction's coordinate-array `index` to slice out per-segment coordinates.
// Freehand placeholder instructions carry no `.mode`, so they're treated as
// 'driving' and simply merge into whichever driving segment surrounds them.
function detectModeSegments(route) {
  var instructions = route.instructions, coords = route.coordinates;
  var segments = []; // {mode, startIdx, roadName, distance, time, coordinates}
  instructions.forEach(function(instr) {
    var mode = 'driving';
    if (instr.mode === 'ferry') {
      // OSRM reports car-shuttle trains as 'ferry' too; give them their own 'train'
      // segment so the split saves them as a train leg instead of a ferry leg.
      mode = (instr.road && SHUTTLE_TRAIN_RE.test(instr.road)) ? 'train' : 'ferry';
    }
    var cur = segments[segments.length - 1];
    if (!cur || cur.mode !== mode) {
      cur = { mode: mode, startIdx: instr.index, distance: 0, time: 0, roadName: null };
      segments.push(cur);
    }
    cur.distance += instr.distance;
    cur.time += instr.time;
    if (mode !== 'driving' && !cur.roadName) cur.roadName = instr.road; // OSRM crossing step name
  });
  for (var i = 0; i < segments.length; i++) {
    var endIdx = (i < segments.length - 1) ? segments[i + 1].startIdx : coords.length - 1;
    segments[i].coordinates = coords.slice(segments[i].startIdx, endIdx + 1);
  }
  return segments;
}

function routing(map, showSidebar=true, type, allowFerrySplit=false){
  flutterBridge.loading(true);

  sidebar = L.control.sidebar('sidebar', {
      closeButton: true,
      position: 'right',
      autoPan: autoPan
  }).addTo(map);
  sidebar.setContent(spinnerContent);

  L.Control.MyControl = L.Control.extend({
    onAdd: function(map) {
      var el = L.DomUtil.create('div', 'reopen-panel-control');
      if (showSidebar){
        el.innerHTML += '<button type="button" class="btn btn-primary btn-sm reopen-panel-btn" onclick="sidebar.show()">'
                      + '<i class="fa-solid fa-arrow-left"></i></button>';
      }

      return el;
    }
  });

  L.control.myControl = function(opts) {
    return new L.Control.MyControl(opts);
  }

  L.control.myControl({
    position: 'topright'
  }).addTo(map);

  if (["accommodation", "restaurant", "poi"].includes(type)) {
    // Add a single marker for the accommodation at wplist[0] coordinates
    var accommodationMarker = L.marker([wplist[0][0], wplist[0][1]], {
      draggable: true,
      icon: new L.Icon.Default()
    }).addTo(map);

    currentRoute = [{'lat': wplist[0][0], 'lng': wplist[0][1]}];

    accommodationMarker.on('move', function(event) {
      var newLatLng = event.target.getLatLng();
      currentRoute = [{'lat': newLatLng.lat, 'lng': newLatLng.lng}];
    });

    // Center the map on the accommodation marker
    map.setView([wplist[0][0], wplist[0][1]], 13);
    var content = `<h4>${origLabel}</h4>`;
    content += `<p><button id="saveTrip" type="button" onclick="saveTrip()"> Submit </button></p>`;        
    sidebar.setContent(content);
  }
  else if(gpx){
      map.setView([wplist[0][0], wplist[0][1]], 13);
      var content = `
        <input type="file" id="gpxUpload" accept=".gpx" style="display:none;" onchange="handleGpxUpload(event)" />
        <button id="uploadGpxBtn" onclick="document.getElementById('gpxUpload').click()">Upload GPX</button>
      `;
      sidebar.setContent(content);

  }
  else{
    var plan = new L.Routing.Plan(wplist, {
      reverseWaypoints: true,
      routeWhileDragging: true,
      createMarker: function(i, wp, n) {
        let icon;
        
        if (i === 0) {
          icon = markerIconStart;
        } else if (i === n - 1) {
          icon = markerIconEnd;
        } else {
          icon = new L.NumberedDivIcon({ number: i });
        }

        const marker = L.marker(wp.latLng, {
          draggable: true,
          icon: icon
        });

        // For intermediate waypoints, add popup with delete and freehand toggle
        if (i > 0 && i < n - 1) {
          // Check if the segment FROM this waypoint is freehand
          let segmentIsFreehand = freehandSegments.has(i);
          
          // Add visual indicator for freehand waypoints
          if (segmentIsFreehand) {
            // Add a simple star overlay to indicate freehand segment starts here
            setTimeout(() => {
              if (marker.getElement()) {
                addFreehandOverlay(marker.getElement());
              }
            }, 100);
          } else {
            // Remove overlay for non-freehand waypoints
            setTimeout(() => {
              if (marker.getElement()) {
                removeFreehandOverlay(marker.getElement());
              }
            }, 100);
          }
          
          // Create popup content with delete button and freehand toggle
          const freehandLabel = segmentIsFreehand ? (texts.normalRoute || 'Normal Route') : (texts.freehandRoute || 'Freehand Route');
          const freehandButtonColor = segmentIsFreehand ? '#28a745' : '#ff8800';
          const freehandIcon = segmentIsFreehand ? '🔄' : '✏️';
          
          const popupContent = `
            <div style="text-align: center; min-width: 150px;">
              <p style="margin: 5px 0 10px 0;">
                ${segmentIsFreehand ? '✏️ ' : ''}${texts.waypoint || 'Waypoint'} ${i}
                ${segmentIsFreehand ? ' (Freehand Start)' : ''}
              </p>
              <button 
                onclick="toggleFreehand(${i})" 
                style="
                  background-color: ${freehandButtonColor};
                  color: white;
                  border: none;
                  padding: 5px 10px;
                  border-radius: 4px;
                  cursor: pointer;
                  font-size: 13px;
                  margin-bottom: 5px;
                  width: 100%;
                  font-weight: bold;
                "
                onmouseover="this.style.opacity='0.8'"
                onmouseout="this.style.opacity='1'"
                title="Toggle freehand for segment from this waypoint to next"
              >
                ${freehandIcon} ${freehandLabel}
              </button>
              <button 
                onclick="removeWaypoint(${i})" 
                style="
                  background-color: #dc3545;
                  color: white;
                  border: none;
                  padding: 5px 10px;
                  border-radius: 4px;
                  cursor: pointer;
                  font-size: 13px;
                  width: 100%;
                "
                onmouseover="this.style.backgroundColor='#c82333'"
                onmouseout="this.style.backgroundColor='#dc3545'"
              >
                🗑️ ${texts.remove || 'Remove'}
              </button>
            </div>
          `;
          
          marker.bindPopup(popupContent, {
            closeButton: true,
            autoClose: false,
            closeOnClick: false
          });

          // Open popup on click (works for both desktop and mobile)
          marker.on('click', function(e) {
            e.target.openPopup();
          });
        }

        return marker;
      },
      waypointMode: 'snap',
      addWaypoints: true
    });
    window.currentPlan = plan;

    // Intercept spliceWaypoints to keep freehandSegments indices in sync.
    // removeWaypoint() already adjusts freehandSegments before calling splice,
    // so we only need to handle pure insertions (remove === 0).
    var _origSplice = plan.spliceWaypoints.bind(plan);
    plan.spliceWaypoints = function(index, remove) {
      var added = Array.prototype.slice.call(arguments, 2);
      if (remove === 0 && added.length > 0) {
        // Mutate the existing Set in place — createCustomRouter captured this
        // object by reference, so a reassignment would break its closure.
        var toShift = [];
        freehandSegments.forEach(function(seg) {
          if (seg >= index) toShift.push(seg);
        });
        toShift.forEach(function(seg) {
          freehandSegments.delete(seg);
          freehandSegments.add(seg + added.length);
        });
      }
      return _origSplice.apply(plan, arguments);
    };

    if (window.innerWidth > 600){
      var autoPan = true;
    }
    else{
      var autoPan = false;
    }

    if (newRouterProfile === null) {
      newRouterProfile = ["train", "tram", "metro"].includes(type) ? type : "train";
    }

    var profile = "train"
    if (type == "bus" ){
      profile = "driving";
    }
    else if(type == "ferry" ){
      profile = "ferry";
    }

    var baseRouter = L.Routing.osrmv1({serviceUrl: routerurl, profile: profile, useHints: false});
    window.baseRouter = baseRouter;
    var customRouter = createCustomRouter(baseRouter, freehandSegments);

    // The freehand-toggle button is appended into .route-meta by the page (see
    // routing.html). While it fits beside the chips it sits right-aligned; once it
    // wraps it would otherwise sit stranded on the right of an empty row, so give
    // it the full row instead. Flexbox can't express "only when wrapped", hence
    // the offsetTop comparison.
    function syncFreehandWrap() {
      var meta = document.querySelector('#sidebar .route-meta');
      if (!meta) return;
      var btn = meta.querySelector('.freehand-toggle-btn');
      var chips = meta.querySelector('.route-meta-chips');
      if (!btn || !chips) return;
      btn.classList.remove('fh-wrapped'); // measure in its natural width first
      meta.classList.remove('chips-full');
      if (btn.offsetTop > chips.offsetTop) {
        btn.classList.add('fh-wrapped');
        // Chips now have the row to themselves, so stretch them across it to
        // match the full-width button below instead of stopping short.
        meta.classList.add('chips-full');
      }
    }
    window.addEventListener('resize', syncFreehandWrap);

    function renderElecPreview(data) {
      var el = document.getElementById('elecPreview');
      if (!el) return;
      if (!data || data.percent === null || data.percent === undefined) {
        el.innerHTML = ''; // nothing to show — drop the loading state
        syncFreehandWrap();
        return;
      }

      // Same icon+km convention as the country-flag hover (static/js/util.js
      // getFlagEmojiListNew): ⚡ for electrified, 🛢️ for non-electrified.
      var parts = [];
      if (data.elec_m) parts.push(`⚡${mToKm(data.elec_m)}km`);
      if (data.nonelec_m) parts.push(`🛢️${mToKm(data.nonelec_m)}km`);
      if (!parts.length) {
        el.innerHTML = '';
        syncFreehandWrap();
        return;
      }

      var countryCodes = data.countries ? Object.keys(data.countries) : [];

      var explanation;
      if (data.source === 'osm') {
        explanation = texts.electrificationOsm;
      } else if (data.source === 'estimate') {
        explanation = texts.electrificationEstimate;
      } else if (data.source === 'forced') {
        // Tram/metro/funicular are always electric (no propulsion choice exists
        // for them); train/bus/car/cycle/ferry got here because the user picked
        // an explicit propulsion via the radio buttons rather than "auto".
        explanation = ['tram', 'metro', 'funicular'].includes(type)
          ? texts.electrificationTypeElectric
          : (newTrip["powerType"] === 'electric' ? texts.electrificationUserElectric : texts.electrificationUserThermic);
      } else {
        explanation = '';
      }
      // Per-country breakdown, shown even for a single country: it's what names
      // the country (with its flag), which the sentence above deliberately doesn't.
      var countryRows = countryCodes.map(function(cc) {
        var cd = data.countries[cc];
        var ccTotal = cd.elec_m + cd.nonelec_m;
        if (!ccTotal) return '';
        var ccPercent = Math.round(cd.elec_m / ccTotal * 100);
        return `<div class="elec-country-row"><span>${getFlagEmoji(cc)} ${regionNames.of(cc)}</span>`
             + `<span class="elec-country-km">${mToKm(ccTotal)} km</span>`
             + `<span>${ccPercent}%</span></div>`;
      }).join('');
      if (countryRows) countryRows = `<div class="elec-country-list">${countryRows}</div>`;

      // Only train/rail have a propulsion choice to override — tram/metro/funicular
      // are always electric, and their split doesn't depend on powerType at all.
      var overrideHtml = '';
      if (type === 'train' || type === 'rail') {
        var current = newTrip["powerType"] || 'auto';
        var options = [['auto', texts.auto], ['electric', texts.electric], ['thermic', texts.thermic]]
          .map(function(o) {
            return `<option value="${o[0]}"${current === o[0] ? ' selected' : ''}>${o[1]}</option>`;
          }).join('');
        overrideHtml = `<label class="elec-override"><span>${texts.powerType}</span>`
                     + `<select class="elec-override-select">${options}</select></label>`;
      }

      var infoHtml = (explanation || countryRows || overrideHtml)
        ? `<details class="route-hint"><summary><i class="fa-solid fa-circle-info"></i></summary><div class="route-bubble">${explanation.replace("{percent}", data.percent)}${countryRows}${overrideHtml}</div></details>`
        : '';

      // Re-rendering replaces the <details>, which would collapse an open bubble —
      // and the override lives inside it, so keep it open across its own changes.
      var wasOpen = !!el.querySelector('.route-hint[open]');
      el.innerHTML = `<span class="route-dist route-elec">${parts.join(' ')}</span>${infoHtml}`;
      if (wasOpen) {
        var reopened = el.querySelector('.route-hint');
        if (reopened) reopened.open = true; // fires 'toggle' → clampRouteBubble
      }

      var select = el.querySelector('.elec-override-select');
      // Listener goes straight on the element: the leaflet-sidebar plugin stops
      // event propagation at its content container, so delegation from document
      // would never see it (same reason the outside-click handler uses capture).
      if (select) select.addEventListener('change', function() { setPowerType(this.value); });

      syncFreehandWrap();
    }

    // Applies a propulsion override chosen from the sidebar. newTrip["powerType"]
    // is what the save posts, so this changes the stored value as well as the
    // display; the form radios are kept in step because edit_copy.html reads them
    // back when closing the map modal and would otherwise clobber this.
    function setPowerType(value) {
      newTrip["powerType"] = value;
      if (routeDetails) routeDetails["powerType"] = value;
      var radio = document.querySelector('input[name="powerType"][value="' + value + '"]');
      if (radio) radio.checked = true;
      refreshElecPreview();
    }

    // Reclassifies an already-known per-country elec/nonelec split against the
    // *current* powerType, without any network call — used when the user toggles
    // the propulsion radios after a route has already been fetched. Tram/metro/
    // funicular ignore powerType entirely (always forced electric server-side).
    function deriveElecPreview(base, currentPowerType) {
      if (!base || base.percent === null || base.percent === undefined) return base;
      if (!currentPowerType || currentPowerType === 'auto') return base;
      if (['tram', 'metro', 'funicular'].includes(type)) return base;

      var countries = {};
      var elec_m = 0, nonelec_m = 0;
      Object.keys(base.countries || {}).forEach(function(cc) {
        var total = base.countries[cc].elec_m + base.countries[cc].nonelec_m;
        var ccElec = currentPowerType === 'electric' ? total : 0;
        countries[cc] = {elec_m: ccElec, nonelec_m: total - ccElec};
        elec_m += ccElec;
        nonelec_m += total - ccElec;
      });
      var total_m = elec_m + nonelec_m;
      return {
        percent: total_m > 0 ? Math.round((elec_m / total_m) * 1000) / 10 : null,
        elec_m: elec_m,
        nonelec_m: nonelec_m,
        countries: countries,
        source: 'forced'
      };
    }

    function refreshElecPreview() {
      // The server-side country walk is expensive (it's O(points), and routes run
      // to tens of thousands of points), so only ever ask for a path we haven't
      // already asked about.
      var powerType = newTrip["powerType"];
      var cached = (lastElecPreview && lastElecPreviewPath === currentRoute) ? lastElecPreview : null;
      if (cached) {
        if (powerType && powerType !== 'auto') {
          // Explicit propulsion is pure reclassification of the known totals.
          renderElecPreview(deriveElecPreview(cached, powerType));
          return;
        }
        // Back to "auto": only the cached result of an auto request describes the
        // detected/estimated split — a forced one has lost it and must be refetched.
        if (cached.source !== 'forced') {
          renderElecPreview(cached);
          return;
        }
      }
      if (!currentRoute) return;
      var requestId = ++elecPreviewRequestId;
      var requestedPath = currentRoute;
      fetch('/api/electrification-preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: currentRoute.map(function(p) { return {lat: p.lat, lng: p.lng}; }),
          type: type,
          powerType: newTrip["powerType"],
          details: newTrip["details"] || {}
        })
      }).then(function(r) { return r.json(); }).then(function(data) {
        if (requestId !== elecPreviewRequestId) return; // a newer request already resolved
        lastElecPreview = data;
        lastElecPreviewPath = requestedPath;
        renderElecPreview(data);
      }).catch(function() {
        if (requestId === elecPreviewRequestId) renderElecPreview(null); // clear the spinner
      });
    }

    // Live-update the preview when the user overrides the propulsion type via the
    // powerType radios (edit_copy.html/new.html) instead of waiting for a reroute.
    document.addEventListener('change', function(e) {
      if (!e.target || e.target.name !== 'powerType') return;
      newTrip["powerType"] = e.target.value;
      if (routeDetails) routeDetails["powerType"] = e.target.value;
      if (ELEC_PREVIEW_TYPES.includes(type)) refreshElecPreview();
    });

    var control = L.Routing.control({
      routeWhileDragging: true,
      plan: plan,
      show: true,
      lineOptions: {
        styles: [
          {
            color: 'transparent', // Invisible wider line for interaction
            weight: 30, // Adjust the weight to create a larger clickable area
            interactive: true // Ensure it is interactive
          },
          {
            color: 'black',
            opacity: 0.6,
            weight: 6 // Visible line
          },
          useAntPath ? antpathStyles : {color: '#52b0fe', opacity: 0.9, weight: 3}
        ],
        addWaypoints: true  // Allow adding waypoints on regular segments
      },
      router: customRouter
    }).on('routeselected', function(){
      var content = `<h4>${texts.routeTitle.replace("{origLabel}", origLabel).replace("{destLabel}", destLabel)}</h4>`;
      var hintHtml = ''; // "adjust the markers" hint, shown inline next to the distance (train only)

      // Detect car/ferry mode transitions so the ferry-split toggle and
      // saveTripSplit() in routing.html can offer splitting into separate trips.
      // Only offered on the dedicated new-trip routing page (allowFerrySplit) — the
      // edit/copy path editor and the AI-compose map reuse this same routing() control
      // but only ever save a single trip, so splitting isn't wired up there.
      window.modeSegments = (allowFerrySplit && FERRY_SPLIT_TYPES.includes(type)) ? detectModeSegments(this._selectedRoute) : null;
      var ferryCount = window.modeSegments ? window.modeSegments.filter(function(s) { return s.mode === 'ferry'; }).length : 0;
      var trainCount = window.modeSegments ? window.modeSegments.filter(function(s) { return s.mode === 'train'; }).length : 0;

      // Add router selector for train, tram, metro
      if(["train", "tram", "metro"].includes(type)){
        content += buildNewRouterToggleHtml();
        // Tuck the "adjust the markers" hint behind a small info icon (rendered inline with distance).
        hintHtml = `<details class="route-hint"><summary><i class="fa-solid fa-circle-info"></i></summary><div class="route-bubble">${texts.fineTuneNote}</div></details>`;
      } else if (type === "bus") {
        hintHtml = busRouterHint();
      }
      
      // Add note about freehand segments if any exist
      if (freehandSegments.size > 0) {
        content += `<p><small>⚠️ Route includes ${freehandSegments.size} freehand segment(s) shown as orange dashed lines</small></p>`;
      }

      // Ferry-split toggle: offered when adding a new leg (plain trip or new plan leg).
      // allowFerrySplit is false on the edit/copy path editor, so editing an existing
      // trip or plan leg's route never shows this — splitting an in-place edit into a
      // different number of trips/legs is out of scope.
      if (allowFerrySplit && FERRY_SPLIT_TYPES.includes(type) && (ferryCount || trainCount)) {
        // One descriptive line per crossing type present (a route usually has only
        // one kind), and a single checkbox that splits at every crossing. The label
        // uses the ferry wording unless the only crossings are rail shuttles.
        var noteLines = '';
        if (ferryCount) noteLines += `<p style="margin: 0 0 8px 0;">${pluralize(texts.ferrySplitNote, ferryCount)}</p>`;
        if (trainCount) noteLines += `<p style="margin: 0 0 8px 0;">${pluralize(texts.trainSplitNote, trainCount)}</p>`;
        var splitLabel = (trainCount && !ferryCount) ? texts.trainSplitOption : texts.ferrySplitOption;
        content += `
          <div style="margin: 10px 0; padding: 10px; background-color: #eef6ff; border-radius: 4px;">
            ${noteLines}
            <label style="display: flex; align-items: center; cursor: pointer;">
              <input
                type="checkbox"
                id="ferrySplitToggle"
                onchange="ferrySplitEnabled = this.checked"
                ${ferrySplitEnabled ? 'checked' : ''}
                style="margin-right: 8px;"
              >
              <span>${splitLabel}</span>
            </label>
          </div>
        `;
      }

      var distanceM = this._selectedRoute.summary.totalDistance;
      var durationS = this._selectedRoute.summary.totalTime;
      var km = mToKm(distanceM);
      var m = Math.floor(distanceM);
      var time = secondsToDhm(durationS, "en");
      
      var formattedData = `${texts.distanceTime.replace("{km}", km).replace("{time}", time)}`;
      // The hint badge sits on the distance chip's top-right corner, so it has to live
      // inside the (relatively positioned) wrapper rather than beside the chip.
      // The chips are grouped in their own flex container (.route-meta-chips) so that,
      // when the freehand-toggle button is injected into .route-meta afterward (see
      // routing.html/edit_copy.html), it wraps as a single unit relative to the chips
      // instead of fighting each chip individually for space in the same flex row.
      content += `<div class="route-meta"><span class="route-meta-chips"><span class="route-dist-wrap"><span class="route-dist">${formattedData}</span>${hintHtml}</span>`;
      if (ELEC_PREVIEW_TYPES.includes(type)) {
        // Rendered up front in a loading state so the chip's space is already
        // reserved: refreshElecPreview() then fills it in place instead of the
        // row visibly growing a new chip once the request lands.
        content += `<span class="route-dist-wrap" id="elecPreview">`
                 + `<span class="route-dist route-elec elec-loading">`
                 + `<i class="fa-solid fa-circle-notch fa-spin"></i></span></span>`;
      }
      content += `</span></div>`;

      flutterBridge.routeInfo(formattedData, distanceM, durationS);
      flutterBridge.loading(false);
    
      if(geojson){
        content += `<div class="submit-control"><button id="downloadGeoJSON" class="submit-main" type="button" onclick="downloadCurrentRouteAsGeoJSON(${m})">${texts.downloadGeoJSONButton}</button></div>`;
      } else {
        content += buildSubmitControl({
          saveLabel: texts.saveTripButton,
          continueLabel: texts.saveTripContinueButton,
          showContinue: newTrip.precision == "preciseDates" || !!newTrip.plan_uuid
        });
      }
       
      sidebar.setContent(content);
      syncFreehandWrap(); // the page appends the freehand button during setContent

      currentRoute = this._selectedRoute.coordinates;
      newTrip["trip_length"] = this._selectedRoute.summary.totalDistance;
      newTrip["estimated_trip_duration"] = this._selectedRoute.summary.totalTime;
      
      if(routeDetails) {
        routeDetails["powerType"] = newTrip["powerType"]
        newTrip["details"] = routeDetails;
      }

      if (ELEC_PREVIEW_TYPES.includes(type)) refreshElecPreview();

      const waypoints = this._selectedRoute.waypoints;
      console.log(this._selectedRoute)

      if(waypoints.length > 2) {
          const latLngs = waypoints.slice(1, -1).map(point => point.latLng);
          newTrip["waypoints"] = JSON.stringify(latLngs);
      }
      
      // Store freehand segment indices
      if (freehandSegments.size > 0) {
          newTrip["freehandSegments"] = JSON.stringify(Array.from(freehandSegments));
      }
    }).on('routingerror', function(){
      var errorContentWithToggle = errorContent;
      
      // Add router selector for train, tram, metro even on error
      if(["train", "tram", "metro"].includes(type)){
        errorContentWithToggle = buildNewRouterToggleHtml() + errorContent;
        flutterBridge.routingError('Routing failed');
        flutterBridge.loading(false);
      }
      
      sidebar.setContent(errorContentWithToggle);
    }).addTo(map);

    // Dragging a waypoint marker made LRM request the exact same route twice:
    // its Plan.dragEnd fires 'waypointdragend' (which the control routes on,
    // because routeWhileDragging is set) and then immediately _fireChanged() →
    // 'waypointschanged' (which the control routes on again, via autoRoute).
    // Neither request cancels the other, since createCustomRouter.route() returns
    // no abortable handle for LRM's _pendingRequest.abort().
    // Drop the second by muting the waypointschanged handler for the rest of the
    // tick — it stays active for waypoint add/remove (removeWaypoint() and the
    // click-to-insert on the line), which have no dragend and rely on it to reroute.
    plan.on('waypointdragend', function() {
      plan.off('waypointschanged', control._onWaypointsChanged, control);
      setTimeout(function() {
        plan.on('waypointschanged', control._onWaypointsChanged, control);
      }, 0);
    });

    // After LRM draws the route line, bring freehand hit areas to the SVG front
    // so they sit on top of the route line and capture clicks first.
    control.on('routesfound', function() {
      setTimeout(function() {
        freehandLines.forEach(function(l) { l.bringToFront(); });
      }, 0);
    });

    // Store control globally: window.control for switchRouter, window.currentControl for toggleFreehand
    window.control = control;
    window.currentControl = control;
  }

  if (showSidebar){
    setTimeout(function () {
      sidebar.show();
    }, 500);
  }
}
window.switchRouter = switchRouter;

// Build the submit control for the sidebar: "Valider" on its own, plus — when a
// "save & continue" action applies — a second, visually distinct button below it.
// Shared by routing.js, routing.html and air_routing.html.
function submitBtn(id, cls, icon, label, continueTrip) {
  return '<button id="' + id + '" class="' + cls + '" type="button" onclick="saveTrip(' +
    (continueTrip ? 'true' : 'false') + ')"><i class="fa-solid ' + icon + '"></i>' +
    '<span>' + label + '</span></button>';
}
function buildSubmitControl(opts) {
  var save = submitBtn('saveTrip', 'submit-main', 'fa-check', opts.saveLabel, false);
  if (!opts.showContinue) {
    return '<div class="submit-control">' + save + '</div>';
  }
  return '<div class="submit-control">' + save +
    submitBtn('saveTripContinue', 'submit-continue', 'fa-circle-plus', opts.continueLabel, true) +
    '</div>';
}
window.buildSubmitControl = buildSubmitControl;