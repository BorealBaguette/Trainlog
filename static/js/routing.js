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

// The GraphHopper router is the default for every rail type; set by routing() the
// first time it runs (alongside newRouterProfile), then kept across re-renders.
var useNewRouter = false;
var NEW_ROUTER_TYPES = ["train", "tram", "metro", "funicular", "rail"];
// The new router's profile a trip type starts on: funiculars and other rail (monorails,
// suspension railways) aren't in the train graph.
function defaultRouterProfile(t) {
  return ["train", "tram", "metro"].includes(t) ? t : "all";
}
// Persists the ferry-split checkbox's state across re-renders (routeWhileDragging
// fires routeselected repeatedly, which fully re-creates the sidebar HTML — without
// this, an unchecked box would silently reset to checked on the next drag/reroute).
var ferrySplitEnabled = false;
// Persists the new-router profile select across the same re-renders; seeded from
// `type` the first time it's needed (see routing()).
var newRouterProfile = null;

var markergroup = new L.featureGroup(markerIconStart, markerIconEnd);

var routeDetails = null;
// [from, to, ms] per stretch of the last routed line (point indexes), new router only
var routeTimeDetail = null;

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
              // The new router's travel time per stretch of the line, for the timeline's
              // estimates: kept apart so it isn't saved with the trip's details.
              routeTimeDetail = routeDetails.time || null;
              delete routeDetails.time;
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
  // The switch is "use the legacy router": the new one is the default.
  useNewRouter = !document.getElementById('newRouterToggle').checked;
  var profileSelect = document.getElementById('newRouterProfile');
  if (profileSelect) {
    profileSelect.style.display = useNewRouter ? '' : 'none';
  }
  var allExactWrap = document.getElementById('allExactWrap');   // a new-router option too
  if (allExactWrap) allExactWrap.style.display = useNewRouter ? '' : 'none';

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
  updateMarkerVisuals(); // hard-waypoint badges only apply to the new router
}

function buildNewRouterToggleHtml() {
  // Styled in waypoint_popup.css (.router-box), all of it in the options tray. The
  // trip-type choice and "every point exact" belong to the new router, so they show
  // only while it is in use; the legacy router is the fallback, last.
  var profiles = [
    ["train", texts.train, "fa-train"], ["tram", texts.tram, "fa-train-tram"],
    ["metro", texts.metro, "fa-train-subway"], ["all", texts.all, "fa-layer-group"]
  ].map(function(p) {
    var active = newRouterProfile === p[0];
    return `<button type="button" class="router-seg-btn${active ? ' active' : ''}" aria-pressed="${active}"
              onclick="switchRouterProfile('${p[0]}')"><i class="fa-solid ${p[2]}" aria-hidden="true"></i><span>${p[1]}</span></button>`;
  }).join('');

  return `
    <div class="router-box">
      ${buildRouterTrayHeaderHtml()}
      <div class="router-advanced" style="${routerAdvancedOpen ? '' : 'display: none;'}">
      <div class="router-seg" id="newRouterProfile" role="group" style="${useNewRouter ? '' : 'display: none;'}">
        ${profiles}
      </div>
      <label class="router-row" id="allExactWrap" title="${texts.allWaypointsExactHint}" style="${useNewRouter ? '' : 'display: none;'}">
        <i class="fa-solid fa-crosshairs router-row-icon"></i>
        <span class="router-row-label">${texts.allWaypointsExact}</span>
        <input type="checkbox" class="router-switch all-exact-toggle" onchange="setAllWaypointsExact(this.checked)"
               ${allWaypointsExact ? 'checked' : ''}>
      </label>
      ${buildRouteFiltersHtml()}
      ${buildOrmOverlayHtml()}
      <div class="router-row">
        <i class="fa-solid fa-clock-rotate-left router-row-icon"></i>
        <span class="router-row-label">
          <label for="newRouterToggle">${texts.useLegacyRouter}</label>
          <!-- Outside the label: a click on it must not flip the switch -->
          <details class="route-hint"><summary><i class="fa-solid fa-circle-info"></i></summary><div class="route-bubble">${texts.useLegacyRouterHint}</div></details>
        </span>
        <input type="checkbox" class="router-switch" id="newRouterToggle" onchange="switchRouter()"
               ${useNewRouter ? '' : 'checked'}>
      </div>
      </div>
    </div>
  `;
}

function switchRouterProfile(value) {
  if (!useNewRouter || value === newRouterProfile) return;
  newRouterProfile = value;
  document.querySelectorAll('#newRouterProfile .router-seg-btn').forEach(function (btn) {
    var on = btn.getAttribute('onclick').indexOf("'" + value + "'") > -1;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', String(on));
  });
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

// Re-render the open waypoint popup after one of its buttons changed the waypoint,
// so it stays open showing the new state (its content is a function, see createMarker).
function refreshWaypointPopup(index) {
  var marker = window.currentPlan && window.currentPlan._markers && window.currentPlan._markers[index];
  var popup = marker && marker.getPopup();
  if (popup && popup.isOpen()) popup.update();
}

window.toggleFreehand = function(index) {
  // For waypoint at index, toggle the segment FROM index TO index+1
  if (freehandSegments.has(index)) {
    freehandSegments.delete(index);
  } else {
    freehandSegments.add(index);
  }
  
  // Update marker visual appearance
  updateMarkerVisuals();
  refreshWaypointPopup(index);
  
  // Force re-route to update the display
  if (window.currentControl) {
    window.currentControl.route();
  }
};

// Function to update all marker visual indicators
window.updateMarkerVisuals = function() {
  if (window.currentPlan && window.currentPlan._markers) {
    window.currentPlan._markers.forEach(function(marker, index) {
      {
        // No freehand badge on the destination: there is no segment after it.
        let segmentIsFreehand = index < window.currentPlan._markers.length - 1 && freehandSegments.has(index);
        let hard = useNewRouter && isHardWaypoint(window.currentPlan.getWaypoints()[index]);
        
        setTimeout(() => {
          if (marker.getElement()) {
            if (segmentIsFreehand) {
              addFreehandOverlay(marker.getElement());
            } else {
              removeFreehandOverlay(marker.getElement());
            }
            if (hard) {
              addHardOverlay(marker.getElement());
            } else {
              removeHardOverlay(marker.getElement());
            }
          }
        }, 100);
      }
    });
  }
};

// Marker badges (styled in waypoint_popup.css): freehand from this point / exact point.
function setMarkerBadge(element, kind, icon, on) {
  const existing = element.querySelector('.wp-badge-' + kind);
  if (!on) {
    if (existing) existing.remove();
    return;
  }
  if (existing) return;
  const badge = document.createElement('div');
  badge.className = 'wp-badge wp-badge-' + kind;
  badge.innerHTML = `<i class="fa-solid ${icon}"></i>`;
  element.style.position = 'relative';
  element.appendChild(badge);
}

window.addFreehandOverlay = function(element) { setMarkerBadge(element, 'freehand', 'fa-pen-nib', true); };
window.removeFreehandOverlay = function(element) { setMarkerBadge(element, 'freehand', 'fa-pen-nib', false); };

// Hard ("exactly here") vs soft ("roughly here, the train passes by") waypoints, for
// the new router only (see HANDOFF.md / waypoint_modes). The mode lives on the
// waypoint object itself (wp.options.hard), which Leaflet Routing Machine keeps across
// drags, reroutes and splices, so it can't drift out of step with the waypoints the way
// an index-keyed set would. Soft is the router's default and ours.
// A plan waypoint at [lat, lng] from a page's {name, hard, stop} (routingWaypointMeta).
// hard: false (not just absent) is a point made approximate by hand (see saving).
function waypointFromMeta(c, meta) {
  meta = meta || {};
  // auto: made exact by the page, not by the user (a timetable's stop put on its direction's
  // stop position): kept exact only where that does not lengthen the route (createCustomRouter).
  var options = { hard: meta.hard === true, modeSet: meta.hard === false,
                  autoHard: meta.hard === true && meta.auto === true };
  if (meta.name) options.label = meta.name;
  if (meta.stop && typeof meta.stop === 'object') options.stop = meta.stop;
  return L.Routing.waypoint(L.latLng(c[0], c[1]), meta.name || '', options);
}
window.waypointFromMeta = waypointFromMeta;

// A waypoint's name. Leaflet Routing Machine clears wp.name when the waypoint is dragged,
// so it is also kept on the waypoint's options (label, set when the drag starts), and a
// timetable stop has its own.
function waypointLabel(wp) {
  var o = (wp && wp.options) || {};
  return (wp && wp.name) || o.label || (o.stop && o.stop.name) || '';
}

// A named point or timetable stop dragged this far from where it was (the stop's own
// position for a stop) is no longer that place: just a point on the route. Closer, it's a
// nudge onto the right track, and stays the stop. Big stations' platforms and their
// station point can be a few hundred metres apart.
var STOP_KEEP_M = 500;

// "11:28 – 11:36 · Pl. 8" for a timetable stop, in the stop's own time zone: the actual
// times and platform (live or corrected by hand, *_rt) where known, else the scheduled.
// Anything else needing a stop's time should do the same, and with neither, shift the
// scheduled time by the trip's departure/arrival delays.
function stopTimesLabel(stop) {
  function clock(iso) {
    if (!iso) return '';
    try {
      return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', timeZone: stop.tz || undefined })
        .format(new Date(iso));
    } catch (e) { return ''; }
  }
  var arr = clock(stop.arr_rt || stop.arr), dep = clock(stop.dep_rt || stop.dep);
  var times = arr && dep && arr !== dep ? arr + ' \u2013 ' + dep : (arr || dep);
  return [times, trackLabel(stop.platform_rt || stop.platform)].filter(Boolean).join(' \u00b7 ');
}

// "Pl. 8" for a platform as given ("Gleis 8", "8"), or '' without one: a track by train, a
// stand by bus (texts.motisStand), a pier by ferry (texts.motisPier), where the page has them.
var routingTripType = null;   // as given to routing()
function trackLabel(track) {
  var pattern = ({ bus: texts.motisStand, ferry: texts.motisPier })[routingTripType] || texts.motisTrack;
  if (!track || !pattern) return '';
  var bare = String(track).replace(/^(gl\.?|gleis|voie|quai|track|platform|pl\.?|spor|spår|bstg\.?|bin\.?)\s*/i, '');
  return pattern.replace('{track}', bare || track);
}

// "Every point exact" (setAllWaypointsExact) overrides that while it is on, for places
// where the timetable puts its stops on their tracks; each waypoint keeps its own flag
// underneath, which applies again once it is off.
// Starts on for users who chose that in their settings; a point made approximate by
// hand (modeSet) stays approximate then, as it was saved.
var allWaypointsExact = !!window.exactWaypointsDefault;
// Set while the comparison route (createCustomRouter) is requested: the points made exact by the
// page count as approximate for it.
var relaxAutoExact = false;
function isHardWaypoint(wp) {
  if (!wp) return false;
  var o = wp.options || {};
  if (relaxAutoExact && o.autoHard) return false;
  return o.modeSet ? !!o.hard : (allWaypointsExact || !!o.hard);
}

// A point placed by hand (any pin dragged, origin and destination included, or one
// added by dragging the line) is meant to be passed through right there, so it becomes
// exact, at any zoom. Never back to approximate on its own: Ctrl-click or the popup
// does that. And not a point whose mode was set by hand (wp.options.modeSet): one made
// approximate on purpose stays so when dragged again. New router only.
function placedExactly() {
  return useNewRouter;
}

window.setWaypointHard = function(index, hard) {
  var wps = window.currentPlan && window.currentPlan.getWaypoints();
  if (!wps || !wps[index]) return;
  if (isHardWaypoint(wps[index]) === !!hard) return; // already in that mode: nothing to reroute
  if (allWaypointsExact && !hard) {
    // One point made approximate while every point is exact: the others stay exact,
    // now each on its own flag.
    wps.forEach(function (wp) {
      if (!(wp.options && wp.options.modeSet)) wp.options = L.extend({}, wp.options, { hard: true });
    });
    allWaypointsExact = false;
    syncAllExactToggles();
  }
  // modeSet: chosen by hand, so dragging the point later leaves it as it is.
  wps[index].options = L.extend({}, wps[index].options, { hard: !!hard, modeSet: true, autoHard: false });
  updateMarkerVisuals();
  refreshWaypointPopup(index);
  if (window.currentControl) window.currentControl.route();
};

// The "every point exact" switches (routing sidebar, compose, edit dialog) call this.
window.setAllWaypointsExact = function(on) {
  if (allWaypointsExact === !!on) return;
  allWaypointsExact = !!on;
  syncAllExactToggles();
  // Turned on by hand: every point, including those set approximate, becomes exact.
  var plan = window.currentPlan && window.currentPlan.getWaypoints();
  if (on && plan) plan.forEach(function (wp) { wp.options = L.extend({}, wp.options, { modeSet: false }); });
  updateMarkerVisuals();
  var wps = window.currentPlan && window.currentPlan.getWaypoints();
  if (wps) wps.forEach(function (_, i) { refreshWaypointPopup(i); });
  if (window.currentControl) window.currentControl.route();
};
function syncAllExactToggles() {
  document.querySelectorAll('.all-exact-toggle').forEach(function (el) { el.checked = allWaypointsExact; });
}
document.addEventListener('DOMContentLoaded', syncAllExactToggles);

// One waypoint popup at a time (Leaflet's autoClose), closed by a click anywhere outside
// it: on the map (closeOnClick) and also elsewhere on the page (sidebar, edit form),
// which Leaflet doesn't watch. Capture phase, because the sidebar plugin stops click
// propagation. Clicks inside the popup (its buttons) and on markers (which open
// their own) are left alone.
document.addEventListener('click', function(e) {
  if (typeof map === 'undefined' || !map || !map._popup || !map.hasLayer(map._popup)) return;
  if (map._popup.options.className !== 'wp-leaflet-popup') return;
  if (e.target.closest('.leaflet-popup, .leaflet-marker-icon')) return;
  if (map.getContainer().contains(e.target)) return; // the map handles its own clicks
  map.closePopup();
}, true);

window.addHardOverlay = function(element) { setMarkerBadge(element, 'hard', 'fa-crosshairs', true); };
window.removeHardOverlay = function(element) { setMarkerBadge(element, 'hard', 'fa-crosshairs', false); };

// Tells the new router which of `waypoints` are hard. Set on the base router right
// before each segment request (its URL is built synchronously), and omitted when every
// point is soft, the router's default, so routers without the parameter keep working.
function applyWaypointModes(router, waypoints) {
  var params = router.options.requestParameters;
  if (!params) return;
  delete params.waypoint_modes;
  if (!params.use_new_router || !waypoints.some(isHardWaypoint)) return;
  params.waypoint_modes = waypoints.map(function(wp) {
    return isHardWaypoint(wp) ? 'hard' : 'soft';
  }).join(',');
}

// The router options sit in a tray, closed by default to keep the sidebar short on
// phones; closing it only hides them. The track filters among them are only taken by
// the new router's train profile (src/routing.py turns them into its custom_model).
// On desktop there is room for it, so it starts open.
var routerAdvancedOpen = window.matchMedia('(min-width: 768px)').matches;
var routeFilters = {};
var ROUTE_FILTER_KEYS = ['avoid_highspeed', 'max_speed', 'electrified', 'power', 'gauge'];
// Widest first: value, text key of its name, width shown, width drawn by gaugeGlyph.
// Every gauge of 1067 mm or less is the one narrow choice. Kept in step with GAUGES in
// src/routing.py.
var ROUTE_GAUGES = [
  ['1676', 'routeGaugeIndian', '1676 mm', 1676], ['1668', 'routeGaugeIberian', '1668 mm', 1668],
  ['1600', 'routeGaugeIrish', '1600 mm', 1600], ['1524', 'routeGaugeFinnish', '1524 mm', 1524],
  ['1520', 'routeGaugeRussian', '1520 mm', 1520], ['1435', 'routeGaugeStandard', '1435 mm', 1435],
  ['narrow', 'routeGaugeNarrow', '≤ 1067 mm', 1000]
];
var routeFilterTimer = null;
var routeFiltersNoRoute = false;   // the last request found nothing that passes them

function routeFiltersAvailable() {
  return useNewRouter && newRouterProfile === 'train';
}

function routeFiltersActive() {
  return routeFiltersAvailable() && Object.keys(routeFilters).length > 0;
}

function applyRouteFilters(router) {
  var params = router.options.requestParameters;
  if (!params) return;
  ROUTE_FILTER_KEYS.forEach(function (k) { delete params[k]; });
  if (routeFiltersActive()) Object.assign(params, routeFilters);
}

function buildRouteFiltersHtml() {
  function row(icon, label, control) {
    return `<label class="router-row"><i class="fa-solid ${icon} router-row-icon"></i>
              <span class="router-row-label">${label}</span>${control}</label>`;
  }
  // AC as a sine, DC as its symbol (a line over a dashed one)
  var ac = '<svg viewBox="0 0 22 16" width="22" height="16" aria-hidden="true"><path d="M3 8C5.5 2 8.5 2 11 8S16.5 14 19 8" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
  var dc = '<svg viewBox="0 0 22 16" width="22" height="16" aria-hidden="true"><path d="M3 5.5H19M3 10.5H6.5M9.25 10.5H12.75M15.5 10.5H19" stroke="currentColor" stroke-width="1.8"/></svg>';
  // No filter: worded per row, to agree with each label
  var anyItem = function (text) { return ['', text, '', '']; };

  return `
    <div class="route-filters-box" style="${routeFiltersAvailable() ? '' : 'display: none;'}">
      <div class="route-filters">
        ${row('fa-gauge-high', texts.routeFilterAvoidHighspeed,
              `<input type="checkbox" class="router-switch" onchange="setRouteFilter('avoid_highspeed', this.checked ? '1' : '')"
                      ${routeFilters.avoid_highspeed ? 'checked' : ''}>`)}
        ${buildRouterPickerHtml('fa-gauge', texts.routeFilterMaxSpeed,
              [anyItem(texts.routeFilterSpeedAny)].concat([120, 160, 200].map(function (v) { return [String(v), v + ' km/h', '', '']; })),
              routeFilters.max_speed || '', 'setRouteMaxSpeed')}
        ${row('fa-bolt', texts.routeFilterElectrifiedOnly,
              `<input type="checkbox" class="router-switch" onchange="setRouteFilter('electrified', this.checked ? 'yes' : '')"
                      ${routeFilters.electrified ? 'checked' : ''}>`)}
        ${buildRouterPickerHtml('fa-plug', texts.routeFilterPower, [anyItem(texts.routeFilterPowerAny),
              ['25kv', '25 kV', ac, '50 Hz'], ['15kv', '15 kV', ac, '16.7 Hz'], ['3kv', '3 kV', dc, 'DC'],
              ['1.5kv', '1.5 kV', dc, 'DC'], ['750v', '750 V', dc, 'DC']
            ], routeFilters.power || '', 'setRoutePower', true)}
        ${buildRouterPickerHtml('fa-ruler-horizontal', texts.routeFilterGauge,
              [anyItem(texts.routeFilterGaugeAny)].concat(ROUTE_GAUGES.map(function (g) {
                return [g[0], texts[g[1]], gaugeGlyph(g[3]), g[2], g[2]];
              })), routeFilters.gauge || '', 'setRouteGauge')}
      </div>
    </div>
  `;
}

function setRouteGauge(value) { setRouteFilter('gauge', value); }
function setRouteMaxSpeed(value) { setRouteFilter('max_speed', value); }
function setRoutePower(value) { setRouteFilter('power', value); }

// A track seen from above: two rails across three sleepers, to scale (the widest gauge
// spans 16 of the 22 units).
function gaugeGlyph(mm) {
  var half = mm / 1676 * 16 / 2, l = 11 - half, r = 11 + half;
  return `<svg class="gauge-glyph" viewBox="0 0 22 16" width="22" height="16" aria-hidden="true">
            <path d="M${l - 2.5} 3H${r + 2.5}M${l - 2.5} 8H${r + 2.5}M${l - 2.5} 13H${r + 2.5}" stroke="currentColor" stroke-opacity=".35" stroke-width="1.6"/>
            <path d="M${l} 0V16M${r} 0V16" stroke="currentColor" stroke-width="2"/></svg>`;
}

// A choice whose options show more than a <select> can: each item is
// [value, name, glyph html, side text, text shown closed (defaults to the name)].
// The list opens in place under its row, as a floating one would be cut off by
// compose's scrolling bubble. onPick names a global function given the value. A multi
// picker takes and gives its values comma-separated, stays open while they are ticked,
// and its '' item (none) clears them.
function buildRouterPickerHtml(icon, label, items, value, onPick, multi) {
  var picked = multi ? (value ? value.split(',') : []) : [value];
  if (!multi && !items.some(function (i) { return i[0] === value; })) picked = [items[0][0]];
  var isOn = function (i) { return multi && i[0] === '' ? picked.length === 0 : picked.includes(i[0]); };
  var options = items.map(function (i) {
    var on = isOn(i);
    return `<button type="button" role="option" class="router-picker-opt${on ? ' active' : ''}" aria-selected="${on}"
              data-value="${i[0]}" data-short="${i[4] || i[1]}" onclick="pickRouterOption(this)">
              <span class="router-picker-glyph">${i[2]}</span><span class="router-picker-name">${i[1]}</span>
              <span class="router-picker-side">${i[3]}</span><i class="fa-solid fa-check router-picker-check"></i></button>`;
  }).join('');
  var current = items.filter(isOn);
  return `
    <div class="router-picker" data-on-pick="${onPick}"${multi ? ' data-multi="1"' : ''}>
      <button type="button" class="router-row router-picker-toggle" aria-expanded="false" onclick="toggleRouterPicker(this)">
        <i class="fa-solid ${icon} router-row-icon"></i>
        <span class="router-row-label">${label}</span>
        ${pickerCurrentHtml(current.map(function (i) { return i[4] || i[1]; }))}
        <i class="fa-solid fa-chevron-down router-tray-chevron"></i>
      </button>
      <div class="router-picker-list" role="listbox" aria-label="${label}"${multi ? ' aria-multiselectable="true"' : ''}
           hidden onkeydown="routerPickerKey(event, this)">${options}</div>
    </div>`;
}

// The closed picker's summary: the short texts of its picked items (no glyph: the
// row's icon already tells what it is).
function pickerCurrentHtml(labels) {
  return `<span class="router-picker-current">${labels.join(' · ')}</span>`;
}

function toggleRouterPicker(toggle, open) {
  var list = toggle.nextElementSibling;
  if (open === undefined) open = list.hidden;
  list.hidden = !open;
  toggle.classList.toggle('open', open);
  toggle.setAttribute('aria-expanded', String(open));
  if (open) (list.querySelector('.active') || list.firstElementChild).focus();
}

function pickRouterOption(opt) {
  var picker = opt.closest('.router-picker');
  var toggle = picker.querySelector('.router-picker-toggle');
  var opts = Array.prototype.slice.call(picker.querySelectorAll('.router-picker-opt'));
  var multi = !!picker.dataset.multi;
  opts.forEach(function (o) {
    var on = !multi ? o === opt
      : opt.dataset.value === '' ? o === opt
      : o.dataset.value === '' ? false
      : o === opt ? !o.classList.contains('active') : o.classList.contains('active');
    o.classList.toggle('active', on);
    o.setAttribute('aria-selected', String(on));
  });
  var picked = opts.filter(function (o) { return o.dataset.value !== '' && o.classList.contains('active'); });
  if (multi && !picked.length) {
    opts[0].classList.add('active');
    opts[0].setAttribute('aria-selected', 'true');
  }
  var active = opts.filter(function (o) { return o.classList.contains('active'); });
  picker.querySelector('.router-picker-current').outerHTML =
    pickerCurrentHtml(active.map(function (o) { return o.dataset.short; }));
  if (!multi) {
    toggleRouterPicker(toggle, false);
    toggle.focus();
  }
  window[picker.dataset.onPick](multi ? picked.map(function (o) { return o.dataset.value; }).join(',') : opt.dataset.value);
}

function routerPickerKey(e, list) {
  var opts = Array.prototype.slice.call(list.children), i = opts.indexOf(document.activeElement);
  if (e.key === 'Escape') {
    e.preventDefault();
    toggleRouterPicker(list.previousElementSibling, false);
    list.previousElementSibling.focus();
  } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    opts[(i + (e.key === 'ArrowDown' ? 1 : opts.length - 1)) % opts.length].focus();
  }
}

// OpenRailwayMap over the routing map, to see which lines a filter keeps.
// openrailwaymap.org's raster tiles: ours are vector ones, which Leaflet can't draw. The choice is remembered on this browser only.
var ORM_OVERLAYS = [
  ['standard', 'ormLayerStandard', 'fa-train'], ['maxspeed', 'ormLayerMaxspeed', 'fa-gauge-high'],
  ['electrification', 'ormLayerElectrified', 'fa-bolt'], ['gauge', 'ormLayerGauge', 'fa-ruler-horizontal'],
  ['signals', 'ormLayerSignals', 'fa-traffic-light']
];
var ormOverlayLayer = null;
var ormOverlayType = '';
try { ormOverlayType = localStorage.getItem('routeOrmOverlay') || ''; } catch (e) {}

function buildOrmOverlayHtml() {
  return buildRouterPickerHtml('fa-layer-group', 'OpenRailwayMap',
    [['', texts.routeOverlayNone, '<i class="fa-solid fa-ban"></i>', '']].concat(ORM_OVERLAYS.map(function (o) {
      return [o[0], texts[o[1]], `<i class="fa-solid ${o[2]}"></i>`, ''];
    })), ormOverlayType, 'setOrmOverlay');
}

function setOrmOverlay(type) {
  ormOverlayType = ORM_OVERLAYS.some(function (o) { return o[0] === type; }) ? type : '';
  try {
    if (ormOverlayType) localStorage.setItem('routeOrmOverlay', ormOverlayType);
    else localStorage.removeItem('routeOrmOverlay');
  } catch (e) {}
  applyOrmOverlay();
}

function applyOrmOverlay() {
  if (typeof map === 'undefined' || !map) return;
  if (ormOverlayLayer) map.removeLayer(ormOverlayLayer);
  ormOverlayLayer = null;
  if (!ormOverlayType) return;
  ormOverlayLayer = L.tileLayer(`https://tiles.openrailwaymap.org/${ormOverlayType}/{z}/{x}/{y}.png`, {
    maxZoom: 19, zIndex: 5,
    attribution: '&copy; <a href="https://www.openrailwaymap.org/" target="_blank">OpenRailwayMap</a>'
  }).addTo(map);
}

// The tray's header, with the count of filters still applied while it is closed, and
// the no-route message outside the tray so it shows either way.
function buildRouterTrayHeaderHtml() {
  return `
    <button type="button" class="router-tray-toggle${routerAdvancedOpen ? ' open' : ''}" aria-expanded="${routerAdvancedOpen}"
            onclick="toggleRouterAdvanced()">
      <i class="fa-solid fa-sliders router-row-icon"></i>
      <span class="router-row-label">${texts.routeFiltersAdvanced}</span>
      <span class="router-filter-count">${activeFilterCount() || ''}</span>
      <i class="fa-solid fa-chevron-down router-tray-chevron"></i>
    </button>
    <div class="route-filters-error" style="${routeFiltersNoRoute ? '' : 'display: none;'}">
      <i class="fa-solid fa-triangle-exclamation"></i> ${texts.routeFiltersNoRoute}</div>
  `;
}

// A stop's departure ('dep') or arrival ('arr') as minutes since midnight at the stop.
function stopClockMinutes(stop, which) {
  var iso = stop[which + '_rt'] || stop[which] || stop[which === 'dep' ? 'arr' : 'dep'];
  if (!iso) return null;
  try {
    var parts = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: stop.tz || undefined })
      .formatToParts(new Date(iso));
    var h = 0, m = 0;
    parts.forEach(function(x) { if (x.type === 'hour') h = +x.value; else if (x.type === 'minute') m = +x.value; });
    return h * 60 + m;
  } catch (e) { return null; }
}

// Rough times for the points between two timed ones: where each lies along the route in
// the router's own travel time, spread over the gap between the nearest timed points
// either side. Only for the points without a time; a few array passes over the route.
// Returns {index: minutes since midnight}, possibly past 1440 after midnight.
function estimateWaypointMinutes(wps, leave, reach, route) {
  var anchors = leave;   // when each timed point is left; `reach` is when it is arrived at
  var coords = route && route.coordinates, instr = route && route.instructions;
  if (!coords || coords.length < 2) return {};
  var last = wps.length - 1, pending = false;
  for (var i = 1; i < last; i++) if (anchors[i] == null) { pending = true; break; }
  if (!pending || anchors[0] == null || anchors[last] == null) return {};

  // Router time at points along the line: from the new router's time per stretch when it
  // covers this very line (not with freehand parts, which change the point list), else
  // from the steps of the legacy one. Interpolated between those, by point.
  var starts = [], cums = [], cum = 0;
  var tl = routeTimeDetail;
  if (tl && tl.length && tl[tl.length - 1][1] === coords.length - 1) {
    tl.forEach(function(t) { starts.push(t[0]); cums.push(cum); cum += t[2]; });
    starts.push(coords.length - 1); cums.push(cum);
  } else if (instr && instr.length && instr.some(function(it) { return it.time; })) {
    instr.forEach(function(it) { starts.push(it.index); cums.push(cum); cum += it.time || 0; });
    starts.push(coords.length - 1); cums.push(cum);
  } else return {};
  var k = 0;
  function timeAt(idx) {
    while (k < starts.length - 2 && idx >= starts[k + 1]) k++;
    var span = starts[k + 1] - starts[k];
    return span > 0 ? cums[k] + (cums[k + 1] - cums[k]) * (idx - starts[k]) / span : cums[k];
  }

  // Each waypoint's coordinate on the route, searched forward from the previous one
  var at = [], from = 0;
  wps.forEach(function(wp) {
    var cos = Math.cos(wp.latLng.lat * Math.PI / 180), best = from, bestD = Infinity;
    for (var j = from; j < coords.length; j++) {
      var dy = coords[j].lat - wp.latLng.lat, dx = (coords[j].lng - wp.latLng.lng) * cos, d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = j; }
    }
    at.push(timeAt(best)); from = best;
  });

  // Unrolled across midnight so the anchors only ever increase
  var abs = [], absReach = [], prev = -Infinity;
  anchors.forEach(function(m, i) {
    if (m == null) { abs.push(null); absReach.push(null); return; }
    var r = reach[i] != null ? reach[i] : m;
    while (r < prev) r += 1440;
    while (m < r) m += 1440;
    absReach.push(r); abs.push(m); prev = m;
  });
  var out = {}, before = 0;
  for (var w = 1; w < last; w++) {
    if (abs[w] != null) { before = w; continue; }
    var after = w + 1;
    while (abs[after] == null) after++;
    var gap = at[after] - at[before];
    if (gap <= 0) continue;
    out[w] = abs[before] + (absReach[after] - abs[before]) * (at[w] - at[before]) / gap;
  }
  return out;
}

// Desktop only (the panel is short on a phone), and only on the routing page: the route as
// a timeline, origin and destination large, the points between them smaller, with their
// times when they are timetable stops (or a "~" estimate, on a timed trip). Rebuilt with
// the rest of the panel on every reroute, so it follows each added, moved or removed point.
function buildRouteTimelineHtml(wps, route) {
  if (!window.matchMedia('(min-width: 768px)').matches || !wps || wps.length < 2) return '';
  function esc(t) { return String(t).replace(/[&<>"]/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function item(cls, name, times) {
    return '<li class="rt-item ' + cls + '"><span class="rt-name">' + esc(name) + '</span>'
      + (times ? '<small class="rt-time">' + esc(times) + '</small>' : '') + '</li>';
  }
  var last = wps.length - 1;
  // The trip's own start and end, when it has them: a stop's times win where there are some
  function tripTime(v, planned) {
    if (v && v.length === 16) return v.slice(11);
    return /^\d{1,2}:\d{2}$/.test(planned || '') ? planned : '';
  }
  function toMinutes(t) { var p = t.split(':'); return +p[0] * 60 + +p[1]; }
  var startTime = tripTime(newTrip.newTripStart, newTrip.planStartTime);
  var endTime = tripTime(newTrip.newTripEnd, newTrip.planEndTime);

  // The minutes each point is known to be left (dep) or arrived at (arr): a stop's own times
  function anchor(which) {
    return wps.map(function(wp, i) {
      var stop = wp.options && wp.options.stop;
      if (stop && typeof stop === 'object') {
        var m = stopClockMinutes(stop, which);
        if (m != null) return m;
      }
      if (i === 0 && startTime) return toMinutes(startTime);
      if (i === last && endTime) return toMinutes(endTime);
      return null;
    });
  }
  var guessed = estimateWaypointMinutes(wps, anchor('dep'), anchor('arr'), route);

  var items = wps.map(function(wp, i) {
    var stop = wp.options && wp.options.stop;
    var times = stop && typeof stop === 'object' ? stopTimesLabel(stop) : '';
    // The ends' platforms are the trip's own (picked on the form), not the stop's.
    function withTrack(t, track) { return [t, trackLabel(track)].filter(Boolean).join(' \u00b7 '); }
    if (i === 0) return item('rt-end', waypointLabel(wp) || origLabel, times || withTrack(startTime, newTrip.departurePlatform));
    if (i === last) return item('rt-end', waypointLabel(wp) || destLabel, times || withTrack(endTime, newTrip.arrivalPlatform));
    var name = waypointLabel(wp);
    if (!times && guessed[i] != null) {
      var g = Math.round(guessed[i]) % 1440;
      times = '~' + String(Math.floor(g / 60)).padStart(2, '0') + ':' + String(g % 60).padStart(2, '0');
    }
    return item('rt-via' + (name ? '' : ' rt-unnamed'), name || (wp.latLng.lat.toFixed(3) + ', ' + wp.latLng.lng.toFixed(3)), times);
  }).join('');
  return '<ol class="route-timeline">' + items + '</ol>';
}

function activeFilterCount() {
  return routeFiltersAvailable() ? Object.keys(routeFilters).length : 0;
}

// Shown or hidden in place where the page doesn't re-render its router box (compose).
function syncRouteFiltersVisibility() {
  document.querySelectorAll('.route-filters-box').forEach(function (el) {
    el.style.display = routeFiltersAvailable() ? '' : 'none';
  });
  document.querySelectorAll('.router-filter-count').forEach(function (el) { el.textContent = activeFilterCount() || ''; });
}

function toggleRouterAdvanced() {
  routerAdvancedOpen = !routerAdvancedOpen;
  document.querySelectorAll('.router-advanced').forEach(function (el) { el.style.display = routerAdvancedOpen ? '' : 'none'; });
  document.querySelectorAll('.router-tray-toggle').forEach(function (el) {
    el.classList.toggle('open', routerAdvancedOpen);
    el.setAttribute('aria-expanded', String(routerAdvancedOpen));
  });
}

function setRouteFilter(key, value) {
  if (value) routeFilters[key] = value;
  else delete routeFilters[key];
  document.querySelectorAll('.router-filter-count').forEach(function (el) { el.textContent = activeFilterCount() || ''; });
  rerouteForFilters();
}

// Debounced: a filter forcing a detour takes seconds to route, so a few quick changes
// make one request. The sidebar keeps the controls meanwhile (no spinner swap).
function rerouteForFilters() {
  clearTimeout(routeFilterTimer);
  document.querySelectorAll('.route-filters-box').forEach(function (el) { el.classList.add('loading'); });
  routeFilterTimer = setTimeout(function () {
    routeDetails = null;
    delete newTrip.details;
    lastElecPreview = null;
    lastElecPreviewPath = null;
    window.control.route();
  }, 600);
}

// Custom router that handles freehand segments
// How much longer than with them approximate a route may be with the points the page made
// exact (autoHard), and still keep them: right, they change its length by a few metres each;
// one on the wrong track (the other direction's, a line crossing at another level, a stop
// mapped on the wrong way) sends the route past it to turn back, hundreds of metres to
// kilometres longer. In metres, not a share of the route: Köln-Mülheim's turn back added 2km
// to a route ten times as long.
var AUTO_EXACT_MARGIN_M = 300, AUTO_EXACT_PER_POINT_M = 15;

// Where a route turns back on itself: its direction flips by more than TURN_BACK_DEG between
// points TURN_STEP_M apart. A train reverses at a terminus it stops at, the turn then at the
// point itself (within TURN_AT_POINT_M); one turning back between that and TURN_NEAR_M from a
// point the page made exact went past it to come back: that point is on the wrong track.
var TURN_BACK_DEG = 150, TURN_STEP_M = 20, TURN_AT_POINT_M = 30, TURN_NEAR_M = 1500;

function turnBacks(coordinates) {
  var pts = [];
  (coordinates || []).forEach(function (c) {
    var p = L.latLng(c.lat, c.lng);
    if (!pts.length || pts[pts.length - 1].distanceTo(p) >= TURN_STEP_M) pts.push(p);
  });
  function heading(a, b) {
    var k = Math.cos(a.lat * Math.PI / 180);
    return Math.atan2(b.lat - a.lat, (b.lng - a.lng) * k);
  }
  var found = [];
  for (var i = 1; i < pts.length - 1; i++) {
    var turn = Math.abs(heading(pts[i - 1], pts[i]) - heading(pts[i], pts[i + 1])) * 180 / Math.PI;
    if (turn > 180) turn = 360 - turn;
    if (turn > TURN_BACK_DEG) found.push(pts[i]);
  }
  return found;
}

// The points the page made exact that the route turns back near, not at (see TURN_*).
function turnedBackAt(points, coordinates) {
  var turns = turnBacks(coordinates);
  return points.filter(function (wp) {
    return turns.some(function (t) {
      var m = wp.latLng.distanceTo(t);
      return m > TURN_AT_POINT_M && m <= TURN_NEAR_M;
    });
  });
}

function createCustomRouter(baseRouter, freehandSegments) {
  return {
    // The route, with the points the page made exact checked: asked for twice at once, with
    // them exact and approximate, and the approximate kept (they turn approximate) where the
    // exact is much longer. The user's own exact points are never second-guessed.
    route: function(waypoints, callback, context, options) {
      var self = this;
      var auto = (waypoints || []).filter(function (wp) {
        return wp.options && wp.options.autoHard && isHardWaypoint(wp);
      });
      if (!auto.length || !useNewRouter) return self.routeOnce(waypoints, callback, context, options);
      var results = {}, pending = 2;
      function settle() {
        if (--pending) return;
        var exact = results.exact, loose = results.loose;
        var length = function (r) { return r && !r.err && r.routes && r.routes[0] ? r.routes[0].summary.totalDistance : null; };
        var e = length(exact), l = length(loose);
        // The points the exact route turns back near are on the wrong track: those alone
        // turn approximate, and the route is asked for again with the others.
        var wrong = e != null ? turnedBackAt(auto, exact.routes[0].coordinates) : [];
        if (wrong.length && wrong.length < auto.length) {
          wrong.forEach(function (wp) { wp.options = L.extend({}, wp.options, { hard: false, autoHard: false }); });
          updateMarkerVisuals();
          self.route(waypoints, callback, context, options);
          return;
        }
        if (wrong.length || (l != null && (e == null || e > l + AUTO_EXACT_MARGIN_M + AUTO_EXACT_PER_POINT_M * auto.length))) {
          auto.forEach(function (wp) { wp.options = L.extend({}, wp.options, { hard: false, autoHard: false }); });
          updateMarkerVisuals();
          callback.call(context, loose.err, loose.routes);
        } else {
          callback.call(context, exact.err, exact.routes);
        }
      }
      self.routeOnce(waypoints, function (err, routes) { results.exact = { err: err, routes: routes }; settle(); }, context, options);
      // The segment requests are built as they are sent, so the flag holds for them all.
      relaxAutoExact = true;
      try {
        self.routeOnce(waypoints, function (err, routes) { results.loose = { err: err, routes: routes }; settle(); }, context, options);
      } finally {
        relaxAutoExact = false;
      }
    },
    routeOnce: function(waypoints, callback, context, options) {
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
          applyWaypointModes(baseRouter, segment.waypoints);
          applyRouteFilters(baseRouter);
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
          // Freehand from the origin: nothing before it to continue from, so the line
          // has to start at the origin itself (otherwise it would begin at the next
          // point, or be a single point when the whole trip is freehand).
          if (combinedCoordinates.length === 0) combinedCoordinates.push(freeStart);
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

// Fits of this map (the route once found, an imported GPX…) frame what the open panel
// leaves visible: padding on whichever side it covers, the right on a wide screen, the
// top on a phone. Leaflet Routing Machine fits the route without knowing about
// the panel, hence on the map's fitBounds itself. A fit that sets its own padding, or a
// page that hides the panel (compose), is left alone.
function fitAroundSidebar(map) {
  if (map._fitsAroundSidebar) return;
  map._fitsAroundSidebar = true;
  var fitBounds = map.fitBounds;
  map.fitBounds = function (bounds, options) {
    var o = options || {};
    if (!o.padding && !o.paddingTopLeft && !o.paddingBottomRight) {
      var pad = sidebarPadding(map);
      if (pad) options = L.extend({}, o, pad);
    }
    return fitBounds.call(this, bounds, options);
  };
}
function sidebarPadding(map) {
  var el = document.getElementById('sidebar');
  if (!map._sidebarOpen || !el || !el.offsetWidth || !el.offsetHeight) return null;
  // Its size, not its position: it slides in, and the first route can land mid-slide.
  // The sidebar's own .leaflet-sidebar wrapper holds it off the map's edge.
  var wrap = el.parentElement || el, size = map.getSize(), MARGIN = 24;
  var width = wrap.offsetWidth, height = wrap.offsetHeight;
  // A side panel takes part of the map's width; on phones it is a sheet across the top
  // (routing_panel.css).
  if (width < size.x * 0.8) {
    return { paddingTopLeft: [MARGIN, MARGIN], paddingBottomRight: [width + MARGIN, MARGIN] };
  }
  if (height < size.y) {
    return { paddingTopLeft: [MARGIN, height + MARGIN], paddingBottomRight: [MARGIN, MARGIN] };
  }
  return null;
}

function routing(map, showSidebar=true, type, allowFerrySplit=false){
  routingTripType = type;
  flutterBridge.loading(true);

  sidebar = L.control.sidebar('sidebar', {
      closeButton: true,
      position: 'right',
      // No pan on opening: fits leave room for the panel instead (fitAroundSidebar),
      // and it opens after the first fit has usually happened.
      autoPan: false
  }).addTo(map);
  // Room is kept for the panel from the start when the page shows it (it slides in
  // half a second after this, often after the route has been fitted), until closed.
  map._sidebarOpen = !!showSidebar;
  sidebar.on('show', function () { map._sidebarOpen = true; });
  sidebar.on('hide', function () { map._sidebarOpen = false; });
  fitAroundSidebar(map);
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
    if (newRouterProfile === null) {
      newRouterProfile = defaultRouterProfile(type);
      useNewRouter = NEW_ROUTER_TYPES.includes(type);
    }

    // Intermediate waypoints may come with a name (a via station, a timetable stop), a
    // hard/soft mode (a reopened trip) and a timetable stop's record (stop: name,
    // UTC arr/dep, tz, platform, the stop's own lat/lng), given by the page as
    // window.routingWaypointMeta, one {name, hard, stop} per intermediate point.
    // The two ends may likewise come as exact (window.routingEndpointMeta, [origin, destination]).
    var wpMeta = window.routingWaypointMeta || [];
    var endMeta = window.routingEndpointMeta || [];
    var planWaypoints = wplist.map(function(c, i) {
      if (i === 0) return waypointFromMeta(c, endMeta[0] || {});
      if (i === wplist.length - 1) return waypointFromMeta(c, endMeta[1] || {});
      return waypointFromMeta(c, wpMeta[i - 1] || {});
    });

    var plan = new L.Routing.Plan(planWaypoints, {
      reverseWaypoints: true,
      routeWhileDragging: true,
      createMarker: function(i, wp, n) {
        const isStart = i === 0, isEnd = i === n - 1;
        let icon;
        
        if (isStart || isEnd) {
          // The start/end pins wrapped in a div icon (same image, size and anchors), so
          // they can carry the freehand/exact badges like the numbered markers — an
          // <img> icon can't hold child elements.
          const base = isStart ? markerIconStart : markerIconEnd;
          icon = L.divIcon({
            className: 'wp-endpoint-icon',
            html: `<img src="${base.options.iconUrl}" width="25" height="41" alt="">`,
            iconSize: base.options.iconSize,
            iconAnchor: base.options.iconAnchor,
            popupAnchor: base.options.popupAnchor,
            tooltipAnchor: base.options.tooltipAnchor,
          });
        } else {
          icon = new L.NumberedDivIcon({ number: i });
        }

        const marker = L.marker(wp.latLng, {
          draggable: true,
          icon: icon
        });

        // Badges: exact point (new router only), freehand from here (not on the
        // destination, which has no segment after it).
        setTimeout(() => {
          const el = marker.getElement();
          if (!el) return;
          if (useNewRouter && isHardWaypoint(wp)) addHardOverlay(el); else removeHardOverlay(el);
          if (!isEnd && freehandSegments.has(i)) addFreehandOverlay(el); else removeFreehandOverlay(el);
        }, 100);

        // Built when opened, so it reflects the waypoint's current freehand/hard
        // state and the router in use (hard/soft only exists on the new router).
        const popupContent = function() {
          const freehand = !isEnd && freehandSegments.has(i);
          const hard = isHardWaypoint(wp);
          const endpointName = isStart ? origLabel : isEnd ? destLabel : '';
          const name = waypointLabel(wp) || endpointName;
          const stop = wp.options && wp.options.stop;
          const stopLine = stop ? stopTimesLabel(stop) : '';
          const title = name ? sanitize(name) : `${texts.waypoint || 'Waypoint'} ${i}`;
          const badge = isStart || isEnd
            ? `<span class="wp-popup-index wp-popup-${isStart ? 'start' : 'end'}${window.colorblindMode ? ' colorblind' : ''}"><i class="fa-solid ${isStart ? 'fa-flag' : 'fa-flag-checkered'}"></i></span>`
            : `<span class="wp-popup-index">${i}</span>`;
          const modeButton = function(isHard, icon, label) {
            const active = hard === isHard;
            return `<button type="button" class="wp-mode-btn${active ? ' active' : ''}"
                aria-pressed="${active}" onclick="setWaypointHard(${i}, ${isHard})">
                <i class="fa-solid ${icon}"></i>${label}</button>`;
          };
          // Approximate = pulled onto the line that passes by (magnet); exact = crosshairs.
          const modeToggle = useNewRouter ? `
            <div class="wp-popup-mode">
              <div class="wp-mode" role="group">
                ${modeButton(false, 'fa-magnet', texts.waypointSoft || 'Approximate')}
                ${modeButton(true, 'fa-crosshairs', texts.waypointHard || 'Exact')}
              </div>
              <p class="wp-popup-hint">${hard ? (texts.waypointHardHint || '') : (texts.waypointSoftHint || '')}</p>
            </div>` : '';
          // Freehand applies to the segment towards the next point, so not from the
          // destination; the endpoints can't be removed.
          const actions = [
            isEnd ? '' : `
              <button type="button" class="wp-action${freehand ? ' active' : ''}" aria-pressed="${freehand}"
                  onclick="toggleFreehand(${i})">
                <i class="fa-solid fa-pen-nib"></i>${texts.freehandMode || 'Freehand'}
                ${freehand ? '<i class="fa-solid fa-check wp-action-state"></i>' : ''}
              </button>`,
            isStart || isEnd ? '' : `
              <button type="button" class="wp-action wp-action-danger" onclick="removeWaypoint(${i})">
                <i class="fa-regular fa-trash-can"></i>${texts.remove || 'Remove'}
              </button>`,
          ].join('');
          return `
            <div class="wp-popup">
              <div class="wp-popup-title">
                ${badge}
                <span class="wp-popup-name" title="${title}">${title}</span>
              </div>
              ${stopLine ? `<div class="wp-popup-stop"><i class="fa-regular fa-clock"></i>${sanitize(stopLine)}</div>` : ''}
              ${modeToggle}
              ${actions.trim() ? `<div class="wp-popup-actions">${actions}</div>` : ''}
            </div>`;
        };

        marker.bindPopup(popupContent, {
          className: 'wp-leaflet-popup',
          minWidth: 220,
          maxWidth: 220,
          closeButton: true,
          autoClose: true,
          closeOnClick: true
        });

        // Open popup on click (works for both desktop and mobile); Ctrl/⌘-click
        // toggles the exact point instead (new router only).
        marker.on('click', function(e) {
          const oe = e.originalEvent;
          if (useNewRouter && oe && (oe.ctrlKey || oe.metaKey)) {
            e.target.closePopup();
            window.setWaypointHard(i, !isHardWaypoint(wp));
            return;
          }
          e.target.openPopup();
        });

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
      var args = Array.prototype.slice.call(arguments);
      var added = args.slice(2);
      // A waypoint inserted on the route is placed by hand (only user gestures insert
      // this way), so it is an exact point (placedExactly). Made hard before the
      // splice, which routes straight away.
      if (remove === 0 && added.length > 0 && placedExactly()) {
        added = added.map(function(a) {
          var wp = a && a.hasOwnProperty('latLng') ? a : L.Routing.waypoint(a);
          wp.options = L.extend({}, wp.options, { hard: true });
          return wp;
        });
        args = [index, remove].concat(added);
      }
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
      return _origSplice.apply(plan, args);
    };

    if (window.innerWidth > 600){
      var autoPan = true;
    }
    else{
      var autoPan = false;
    }

    var profile = "train"
    if (type == "bus" ){
      profile = "driving";
    }
    else if(type == "ferry" ){
      profile = "ferry";
    }

    var baseRouter = L.Routing.osrmv1({serviceUrl: routerurl, profile: profile, useHints: false});
    // Coordinates to 6 decimals (~10 cm) in the request: a long run's stops at full
    // float precision outgrew the server's request line (Bergen–Voss bus).
    var _buildRouteUrl = baseRouter.buildRouteUrl;
    baseRouter.buildRouteUrl = function(waypoints, options) {
      var rounded = waypoints.map(function(wp) {
        var ll = L.latLng(+wp.latLng.lat.toFixed(6), +wp.latLng.lng.toFixed(6));
        return L.Routing.waypoint(ll, wp.name, wp.options);
      });
      return _buildRouteUrl.call(this, rounded, options);
    };
    if (useNewRouter) {
      baseRouter.options.requestParameters = { use_new_router: 'true', profile: newRouterProfile };
    }
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
      // #elecPreview lives in the (hidden, on compose.html) routing sidebar; pages
      // that show electrification inline instead (compose.html) mark their own slot
      // with .elec-preview-mirror and get the exact same markup written into it.
      var targets = document.querySelectorAll('#elecPreview, .elec-preview-mirror');
      if (!targets.length) return;
      if (!data || data.percent === null || data.percent === undefined) {
        targets.forEach(function(el) { el.innerHTML = ''; }); // nothing to show — drop the loading state
        syncFreehandWrap();
        return;
      }

      // Same icon+km convention as the country-flag hover (static/js/util.js
      // getFlagEmojiListNew): ⚡ for electrified, 🛢️ for non-electrified.
      var parts = [];
      if (data.elec_m) parts.push(`⚡${mToKm(data.elec_m)}km`);
      if (data.nonelec_m) parts.push(`🛢️${mToKm(data.nonelec_m)}km`);
      if (!parts.length) {
        targets.forEach(function(el) { el.innerHTML = ''; });
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
      // A page whose texts object is missing one of the keys above (undefined, not a
      // string) would otherwise throw on .replace() below — inside an async fetch
      // .then(), which silently blanks the whole chip via the .catch() instead of
      // surfacing an error. Degrade to no explanation rather than no chip at all.
      explanation = explanation || '';
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

      targets.forEach(function(el) {
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
      });

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
      if(NEW_ROUTER_TYPES.includes(type)){
        content += buildNewRouterToggleHtml();
        // Tuck the routing hint behind a small info icon (rendered inline with distance):
        // what the new router prefers, or for the legacy one that it treats every rail
        // type alike.
        var note = useNewRouter ? texts.fineTuneNoteNewRouter : texts.fineTuneNote;
        hintHtml = `<details class="route-hint"><summary><i class="fa-solid fa-circle-info"></i></summary><div class="route-bubble">${note}</div></details>`;
      } else if (type === "bus") {
        hintHtml = busRouterHint();
      }
      
      if (allowFerrySplit && window.currentPlan) content += buildRouteTimelineHtml(window.currentPlan.getWaypoints(), this._selectedRoute);

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
        var elecLoadingHtml = `<span class="route-dist route-elec elec-loading">`
                             + `<i class="fa-solid fa-circle-notch fa-spin"></i></span>`;
        content += `<span class="route-dist-wrap" id="elecPreview">${elecLoadingHtml}</span>`;
        // #elecPreview above is written into the (hidden, on compose.html) sidebar
        // content further down; pages with their own visible slot (.elec-preview-mirror)
        // need the same loading state set here too, since renderElecPreview() only
        // fires once refreshElecPreview()'s fetch actually resolves.
        document.querySelectorAll('.elec-preview-mirror').forEach(function(el) {
          el.innerHTML = elecLoadingHtml;
        });
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
          // {lat, lng} plus the name, hard flag and timetable stop when set, so a
          // reopened trip shows the same names and stops and keeps its exact points exact.
          // Trips saved before stops were kept have only {lat, lng, name?, hard?}.
          const latLngs = waypoints.slice(1, -1).map(function(point) {
            var saved = { lat: point.latLng.lat, lng: point.latLng.lng };
            var label = waypointLabel(point);
            if (label) saved.name = label;
            // A timetable stop's record: name, UTC arr/dep, tz, platform, its own lat/lng.
            if (point.options && point.options.stop) saved.stop = point.options.stop;
            if (isHardWaypoint(point)) saved.hard = true;
            // Approximate by choice is kept too, so dragging it after reopening the
            // trip doesn't make it exact (placedExactly).
            else if (point.options && point.options.modeSet) saved.hard = false;
            return saved;
          });
          newTrip["waypoints"] = JSON.stringify(latLngs);
      } else if (newTrip["waypoints"]) {
          // Every intermediate point gone (removed on the map or in the stops dialog):
          // say so, rather than leave the previous list to be saved again.
          newTrip["waypoints"] = "[]";
      }
      
      // Store freehand segment indices
      if (freehandSegments.size > 0) {
          newTrip["freehandSegments"] = JSON.stringify(Array.from(freehandSegments));
      }
    }).on('routingerror', function(){
      // Strict filters often leave no route at all: the filter box says so (see below)
      // rather than "routing failed"
      var errorContentWithToggle = routeFiltersActive() ? '' : errorContent;

      // Add router selector for train, tram, metro even on error
      if(NEW_ROUTER_TYPES.includes(type)){
        errorContentWithToggle = buildNewRouterToggleHtml() + errorContentWithToggle;
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
    // Dragging any pin makes it exact (placedExactly).
    // On drag start rather than end: routing while dragging, and on the drop, happens
    // before any dragend listener of ours would run.
    plan.on('waypointdragstart', function(e) {
      var wp = plan.getWaypoints()[e.index];
      // Placed by hand now: the user's, no longer one the page made exact.
      if (wp && wp.options && wp.options.autoHard) wp.options = L.extend({}, wp.options, { autoHard: false });
      if (!placedExactly() || isHardWaypoint(wp) || (wp.options && wp.options.modeSet)) return;
      wp.options = L.extend({}, wp.options, { hard: true });
      updateMarkerVisuals();
    });
    // Names and timetable stops survive a drag nearby, and go with one far away
    // (STOP_KEEP_M): kept aside when the drag starts, since the library clears the name.
    plan.on('waypointdragstart', function(e) {
      var wp = plan.getWaypoints()[e.index];
      if (!wp) return;
      var o = wp.options = wp.options || {};
      if (!o.label && wp.name) o.label = wp.name;
      if (!o.anchor && (o.label || o.stop)) {
        o.anchor = o.stop && o.stop.lat != null ? L.latLng(o.stop.lat, o.stop.lng) : L.latLng(wp.latLng);
      }
    });
    plan.on('waypointdragend', function(e) {
      var wp = plan.getWaypoints()[e.index];
      var o = wp && wp.options;
      if (!o || !o.anchor) return;
      if (wp.latLng.distanceTo(o.anchor) > STOP_KEEP_M) {
        delete o.label; delete o.stop; delete o.anchor;
        wp.name = '';
      } else {
        wp.name = waypointLabel(wp);
      }
    });
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
    applyOrmOverlay();
    control.on('routesfound routingerror', function (e) {
      routeFiltersNoRoute = e.type === 'routingerror' && routeFiltersActive();
      document.querySelectorAll('.route-filters-box.loading').forEach(function (el) { el.classList.remove('loading'); });
      document.querySelectorAll('.route-filters-error').forEach(function (el) {
        el.style.display = routeFiltersNoRoute ? '' : 'none';
      });
    });
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