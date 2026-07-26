function createMap(translations=null, center = [50, 10]) {
    // Creating map options
    const mapOptions = {
        center: center,
        zoom: 5,
        zoomSnap: 0.25,
        zoomDelta: 0.01,
        wheelPxPerZoomLevel: 90,
        attributionControl: true,
    };

    // Initializing the map
    const map = L.map('map', mapOptions);

    // Handling tile server selection
    const params = new URLSearchParams(window.location.search);
    const serverType = params.get('tileserver');
    const allowed_styles = [
        'jawg-streets',
        'jawg-lagoon',
        'jawg-sunny',
        'jawg-light',
        'jawg-terrain',
        'jawg-dark',
        'de',
        'fr',
        'none',
    ];

    let tileserverUrl = null;
    let attribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

    if (serverType === 'none') {
        // No background tiles
    } else if (serverType === 'de') {
        tileserverUrl = 'https://tile.openstreetmap.de/{z}/{x}/{y}.png';
    } else if (serverType === 'fr') {
        tileserverUrl = 'https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png';
        attribution = '&copy; OpenStreetMap France | ' + attribution;
    } else if (serverType && serverType.startsWith('jawg-') && allowed_styles.includes(serverType)) {
        tileserverUrl = `https://tiles.trainlog.me/tile/${serverType}/{x}/{y}/{z}/{r}`;
        attribution = '<a href="https://jawg.io" title="Tiles Courtesy of Jawg Maps" target="_blank">&copy; <b>Jawg</b>Maps</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
    } else if (serverType === 'orm') {
        tileserverUrl = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
        attribution = 'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | Map style: &copy; <a href="https://www.OpenRailwayMap.org">OpenRailwayMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)';
    } else {
        tileserverUrl = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
    }

    if (tileserverUrl) {
        L.tileLayer(tileserverUrl, { maxZoom: 19, attribution }).addTo(map);
    }

    if (serverType === 'orm') {
        L.tileLayer('https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
    }


    // Disable double click zoom
    map.doubleClickZoom.disable();

    /*** Adding Info Button and Legend as Leaflet Controls ***/

    if (translations){
        // Define the Legend Control
        const legendControl = L.control({ position: 'bottomleft' });

        legendControl.onAdd = function (map) {
            const div = L.DomUtil.create('div', 'leaflet-control leaflet-bar legend-control');

            div.innerHTML = `
            <div class="legend-toggle">
                    <a href="#" title="map_legend" role="button" aria-label="map_legend">
                        <i class="fas fa-info-circle"></i>
                    </a>
                </div>
                <div class="legend-content" style="display: none;">
                    <div class="legend-header">
                        <h4>${translations['map_legend']}</h4>
                        <a href="#" class="legend-close" aria-label="close_legend">&times;</a>
                    </div>
                    <ul>
                        <li><span class="legend-icon" style="background-color: var(--train);"></span> <i class="fa-solid fa-train"></i>&nbsp;${translations['train']}</li>
                        <li><span class="legend-icon" style="background-color: var(--tram);"></span> <i class="fa-solid fa-train-tram"></i>&nbsp;${translations['tram']}</li>
                        <li><span class="legend-icon" style="background-color: var(--metro);"></span> <i class="fa-solid fa-train-subway"></i>&nbsp;${translations['metro']}</li>
                        <li><span class="legend-icon" style="background-color: var(--car);"></span> <i class="fa-solid fa-car-side"></i>&nbsp;${translations['car']}</li>
                        <li><span class="legend-icon" style="background-color: var(--walk);"></span> <i class="fa-solid fa-person-hiking"></i>&nbsp;${translations['walk']}</li>
                        <li><span class="legend-icon" style="background-color: var(--cycle);"></span> <i class="fa-solid fa-bicycle"></i>&nbsp;${translations['cycle']}</li>
                        <li><span class="legend-icon" style="background-color: var(--air);"></span> <i class="fa-solid fa-plane-up"></i>&nbsp;${translations['air']}</li>
                        <li><span class="legend-icon" style="background-color: var(--bus);"></span> <i class="fa-solid fa-bus"></i>&nbsp;${translations['bus']}</li>
                        <li><span class="legend-icon" style="background-color: var(--ferry);"></span> <i class="fa-solid fa-ship"></i>&nbsp;${translations['ferry']}</li>
                        <li><span class="legend-icon" style="background-color: var(--aerialway);"></span> <i class="fa-solid fa-cable-car"></i>&nbsp;${translations['aerialway']}</li>
                        <li><span class="legend-icon" style="background-color: var(--scooter);"></span> <i class="bi bi-scooter"></i>&nbsp;${translations['scooter']}</li>
                        <li><span class="legend-icon" style="background-color: var(--funicular);"></span> <i class="fa-solid fa-mountain"></i>&nbsp;${translations['funicular']}</li>
                        <li><span class="legend-icon" style="background-color: var(--rail);"></span> <i class="fa-solid fa-dumbbell"></i>&nbsp;${translations['rail']}</li>
                        <li><span class="legend-icon" style="background-color: var(--ski);"></span> <i class="fa-solid fa-person-skiing"></i>&nbsp;${translations['ski']}</li>
                    </ul>
                    <hr style="margin: 10px 0; border-top: 1px solid #ccc;" />
                    <ul>
                        <li><span class="legend-icon striped" style="background-color: var(--train);"></span> ${translations['future']}</li>
                        <li><span class="legend-icon" style="background-color: rgba(255, 255, 255, 0.5);"></span> ${translations['project']}</li>
                    </ul>
                </div>
            `;

            // Prevent map interactions when interacting with the legend
            L.DomEvent.disableClickPropagation(div);
            L.DomEvent.disableScrollPropagation(div);

            return div;
        };

        legendControl.addTo(map);

        // Toggle legend visibility on button click
        const legendToggle = document.querySelector('.legend-toggle');
        const legendContent = document.querySelector('.legend-content');
        const legendClose = document.querySelector('.legend-close');

        legendToggle.addEventListener('click', function (e) {
            e.preventDefault();
            legendContent.style.display = 'block';
            legendToggle.style.display = 'none';
        });

        legendClose.addEventListener('click', function (e) {
            e.preventDefault();
            legendContent.style.display = 'none';
            legendToggle.style.display = 'block';
        });

    }

    const showArcticCircle = params.get('polar') === 'true';
    
    if (showArcticCircle) {
        L.polyline(
            [
                [66.56, -5000], // Starting at the western edge
                [66.56, 5000]   // Ending at the eastern edge
            ],
            {
                color: '#006994',    
                weight: 2,
                opacity: 1
            }
        ).addTo(map);

        L.polyline(
            [
                [-66.56, -5000], // Starting at the western edge
                [-66.56, 5000]   // Ending at the eastern edge
            ],
            {
                color: '#006994',    
                weight: 2,
                opacity: 1
            }
        ).addTo(map);
    }

    return map;
}
