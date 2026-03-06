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

var markergroup = new L.featureGroup(markerIconStart, markerIconEnd);

var routeDetails = null;
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
      if (self.readyState === 4 && self.status === 200) {
        // Check if this is an OSRM routing request
        if (self._requestUrl && self._requestUrl.includes('/route/')) {
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
          gpxContent += `<p><button id="saveTrip" type="button" class="btn btn-switch" aria-current="page" onclick="saveTrip()"> Submit </button></p>`;
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
  } else {
    delete currentParams.use_new_router;
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

function routing(map, showSidebar=true, type){
  flutterBridge.loading(true);

  sidebar = L.control.sidebar('sidebar', {
      closeButton: true,
      position: 'right',
      autoPan: autoPan
  }).addTo(map);

  sidebar.setContent(spinnerContent);

  L.Control.MyControl = L.Control.extend({
    onAdd: function(map) {
      var el = L.DomUtil.create('div', 'leaflet-bar');
      if (showSidebar){
        el.innerHTML += '<button class="button" onclick="sidebar.show()">⬅️</button>';
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
    content += `<p><button id="saveTrip" type="button" class="btn btn-switch" aria-current="page" onclick="saveTrip()"> Submit </button></p>`;        
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
      
      // Add router selector for train, tram, metro
      if(["train", "tram", "metro"].includes(type)){
        content += `
          <div style="margin: 10px 0; padding: 10px; background-color: #f0f0f0; border-radius: 4px;">
            <label style="display: flex; align-items: center; cursor: pointer;">
              <input 
                type="checkbox" 
                id="newRouterToggle" 
                onchange="switchRouter()"
                style="margin-right: 8px;"
                ${useNewRouter ? 'checked' : ''}
              >
              <span>${texts.useNewRouter} ᵦ</span>
            </label>
          </div>
        `;
        content += `<p><small>${texts.fineTuneNote}</small></p>`;
      }
      
      // Add note about freehand segments if any exist
      if (freehandSegments.size > 0) {
        content += `<p><small>⚠️ Route includes ${freehandSegments.size} freehand segment(s) shown as orange dashed lines</small></p>`;
      }

      var distanceM = this._selectedRoute.summary.totalDistance;
      var durationS = this._selectedRoute.summary.totalTime;
      var km = mToKm(distanceM);
      var m = Math.floor(distanceM);
      var time = secondsToDhm(durationS, "en");
      
      var formattedData = `${texts.distanceTime.replace("{km}", km).replace("{time}", time)}`;
      content += `<p><i>${formattedData}</i></p>`;

      flutterBridge.routeInfo(formattedData, distanceM, durationS);
      flutterBridge.loading(false);
    
      if(geojson){
        content += `<p><button id="downloadGeoJSON" type="button" class="btn btn-switch" aria-current="page" onclick="downloadCurrentRouteAsGeoJSON(${m})">${texts.downloadGeoJSONButton}</button></p>`;
      } else {
        content += `<p><button id="saveTrip" type="button" class="btn btn-switch" aria-current="page" onclick="saveTrip()">${texts.saveTripButton}</button></p>`;
        if(newTrip.precision == "preciseDates"){
          content += `<button id="saveTripContinue" type="button" class="btn btn-switch" aria-current="page" onclick="saveTrip(true)">${texts.saveTripContinueButton}</button>`;
        }
      }
       
      sidebar.setContent(content);

      currentRoute = this._selectedRoute.coordinates;
      newTrip["trip_length"] = this._selectedRoute.summary.totalDistance;
      newTrip["estimated_trip_duration"] = this._selectedRoute.summary.totalTime;
      
      if(routeDetails) {
        routeDetails["powerType"] = newTrip["powerType"]
        newTrip["details"] = routeDetails;
      }
      
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
        errorContentWithToggle = `
          <div style="margin: 10px 0; padding: 10px; background-color: #f0f0f0; border-radius: 4px;">
            <label style="display: flex; align-items: center; cursor: pointer;">
              <input 
                type="checkbox" 
                id="newRouterToggle" 
                onchange="switchRouter()"
                style="margin-right: 8px;"
                ${useNewRouter ? 'checked' : ''}
              >
              <span>${texts.useNewRouter} ᵦ</span>
            </label>
          </div>
        ` + errorContent;
        flutterBridge.routingError('Routing failed');
        flutterBridge.loading(false);
      }
      
      sidebar.setContent(errorContentWithToggle);
    }).addTo(map);

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