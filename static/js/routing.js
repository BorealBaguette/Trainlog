var markerIconStart = L.icon({
	iconUrl: '/static/images/icons/marker-icon-2x-green.png',
	iconRetinaUrl: '/static/images/icons/marker-icon-2x-green.png',
	iconSize:    [25, 41],
	iconAnchor:  [12, 41],
	popupAnchor: [1, -34],
	tooltipAnchor: [16, -28],
});

var markerIconEnd = L.icon({
	iconUrl: '/static/images/icons/marker-icon-2x-red.png',
	iconRetinaUrl: '/static/images/icons/marker-icon-2x-red.png',
	iconSize:    [25, 41],
	iconAnchor:  [12, 41],
	popupAnchor: [1, -34],
	tooltipAnchor: [16, -28],
});

var urlParams = new URLSearchParams(window.location.search);
var gpx = urlParams.get('gpx');
var geojson = urlParams.get('geojson');
var useAntPath = urlParams.get('antpath') === 'true' ? true : false
// ADD THIS LINE FOR GRAPHHOPPER SUPPORT
var useGraphhopper = urlParams.get('gh') === 'true' ? true : false

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

var markergroup = new L.featureGroup(markerIconStart, markerIconEnd);

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
    control.options.router.options.requestParameters = {exclude: excludelist.join(',')};
    } else {
    delete control.options.router.options.requestParameters;
    }
    
    // PRESERVE GRAPHHOPPER PARAMETER WHEN RECOMPUTING
    if (useGraphhopper && ["train", "tram", "metro"].includes(type)) {
        if (!control.options.router.options.requestParameters) {
            control.options.router.options.requestParameters = {};
        }
        control.options.router.options.requestParameters.router = 'graphhopper';
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

function routing(map, showSidebar=true, type){

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

        if (i > 0 && i < n - 1) {
          let pressTimer = null;
          let dragged = false;

          // Desktop: right-click to remove
          marker.on('contextmenu', function () {
            plan.spliceWaypoints(i, 1);
          });

          // Mobile: long-press to remove
          marker.on('mousedown touchstart', function () {
            dragged = false;
            pressTimer = setTimeout(function () {
              if (!dragged) {
                plan.spliceWaypoints(i, 1);
              }
            }, 500); // 500ms threshold
          });

          // Cancel on movement
          marker.on('dragstart', function () {
            dragged = true;
            clearTimeout(pressTimer);
          });

          marker.on('mouseup mouseleave touchend touchcancel', function () {
            clearTimeout(pressTimer);
          });
        }

        return marker;
      },
      waypointMode: 'snap',
    });

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

    // BUILD REQUEST PARAMETERS FOR GRAPHHOPPER
    var requestParams = {};
    if (useGraphhopper && ["train", "tram", "metro"].includes(type)) {
        requestParams.router = 'graphhopper';
    }

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
        ]
      },
      router: L.Routing.osrmv1({
        serviceUrl: routerurl, 
        profile: profile, 
        useHints: false,
        // ADD GRAPHHOPPER PARAMETER IF NEEDED
        requestParameters: Object.keys(requestParams).length > 0 ? requestParams : undefined
      })
    }).on('routeselected', function(){
      var content = `<h4>${texts.routeTitle.replace("{origLabel}", origLabel).replace("{destLabel}", destLabel)}</h4>`;
      
      if(["train", "tram", "metro"].includes(type)){
        content += `<p><small>${texts.fineTuneNote}</small></p>`;
      }
      
      var km = mToKm(this._selectedRoute.summary.totalDistance);
      var m = Math.floor(this._selectedRoute.summary.totalDistance);
      var time = secondsToDhm(this._selectedRoute.summary.totalTime, "en");
      
      content += `<p><i>${texts.distanceTime.replace("{km}", km).replace("{time}", time)}</i></p>`;
    
      if(geojson){
        content += `<p><button id="downloadGeoJSON" type="button" onclick="downloadCurrentRouteAsGeoJSON(${m})">${texts.downloadGeoJSONButton}</button></p>`;
      } else {
        content += `<p><button id="saveTrip" type="button" onclick="saveTrip()">${texts.saveTripButton}</button></p>`;
        if(newTrip.precision == "preciseDates"){
          content += `<button id="saveTripContinue" type="button"  onclick="saveTrip(true)">${texts.saveTripContinueButton}</button>`;
        }
      }
       
      sidebar.setContent(content);

      currentRoute = this._selectedRoute.coordinates;
      newTrip["trip_length"] = this._selectedRoute.summary.totalDistance;
      newTrip["estimated_trip_duration"] = this._selectedRoute.summary.totalTime;
      const waypoints = this._selectedRoute.waypoints;

      if(waypoints.length > 2) {
          const latLngs = waypoints.slice(1, -1).map(point => point.latLng);
          newTrip["waypoints"] = JSON.stringify(latLngs);
      }
    }).on('routingerror', function(){
      sidebar.setContent(errorContent);
    }).addTo(map);
    
    // STORE CONTROL GLOBALLY FOR recomputeRoute()
    window.control = control;
  }

  if (showSidebar){
    setTimeout(function () {
      sidebar.show();
    }, 500);
  }
}