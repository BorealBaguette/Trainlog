function getDistanceFromLocations({ lat: x1, lng: y1 }, { lat: x2, lng: y2 }) {
  function toRadians(value) {
      return value * Math.PI / 180
  }

  var R = 6371071
  var rlat1 = toRadians(x1) // Convert degrees to radians
  var rlat2 = toRadians(x2) // Convert degrees to radians
  var difflat = rlat2 - rlat1 // Radian difference (latitudes)
  var difflon = toRadians(y2 - y1) // Radian difference (longitudes)
  return 2 * R * Math.asin(Math.sqrt(Math.sin(difflat / 2) * Math.sin(difflat / 2) + Math.cos(rlat1) * Math.cos(rlat2) * Math.sin(difflon / 2) * Math.sin(difflon / 2)))
}

function getTotalDistanceFromPath(path) {
  if (!Array.isArray(path) || path.length < 2) return 0;

  let total = 0;

  for (let i = 0; i < path.length - 1; i++) {
    const point1 = { lat: path[i][0], lng: path[i][1] };
    const point2 = { lat: path[i + 1][0], lng: path[i + 1][1] };
    total += getDistanceFromLocations(point1, point2);
  }

  return total;
}

function getCharFromSurrogate(surrogates){
  var r = 16;
  var hi = surrogates[0].codePointAt(0);
  var lo = surrogates[1].codePointAt(0);

  if (hi >= 0xD800 && hi <= 0xDBFF && lo >= 0xDC00 && lo <= 0xDFFF) {
    var s = ((hi - 0xD800) * 0x400) + (lo - 0xDC00) + 0x10000;
 }
 return String.fromCharCode(hi, lo)

}

function getCharFromRegionalIndicator(val){
  var r = 10;
  if (val != " ")
    val = val.replace(/^\s+/g, "");
  var s;
  var m = "";
  var n = "";
  s = val.charCodeAt(0);
  if ((s >= 0xD800) && (s <= 0xDBFF) && (val.length > 1)) {
    m = s;
    n = val.charCodeAt(1);
    if ((n >= 0xDC00) && (n <= 0xDFFF))
      s = ((m - 0xD800) * 0x400) + (n - 0xDC00) + 0x10000;
    m = m.toString(r);
    n = n.toString(r);
    if (r == 16) {
      m = padString(m.toUpperCase(), 4);
      n = padString(n.toUpperCase(), 4);
  } }
  
  s = s.toString(r);
  
  return String.fromCodePoint(s - 127397)
}
  

function getCCfromEmoji(emoji){
  var splitted = emoji.split('')
  var regionalChars = [getCharFromSurrogate([splitted[0], splitted[1]]),getCharFromSurrogate([splitted[2], splitted[3]])];
  return getCharFromRegionalIndicator(regionalChars[0])+getCharFromRegionalIndicator(regionalChars[1])
}

function getFlagEmoji(countryCode) {
  if(countryCode == "en"){countryCode = "GB";}
    const codePoints = countryCode
      .toUpperCase()
      .split('')
      .map(char =>  127397 + char.charCodeAt());
    return String.fromCodePoint(...codePoints);
  }

function getTooltipSVG(countryCode, gb=false) {
  if (gb){
    var imagePath = `/static/images/flags/${gb.toLowerCase()}.svg`;
  }
  else{
    var imagePath = `/static/images/flags/${countryCode.toLowerCase()}.svg`;
  }
  var CountryName = regionNames.of(countryCode);
  return `<img class="flagPNG" src="${imagePath}" alt="${CountryName} flag"/>`
}


function getTooltipSprite(countryCode, positions, gb = false) {
  var CountryName = regionNames.of(countryCode);
  if (gb) {
      countryCode = gb;
  }

  const position = positions[countryCode.toLowerCase()];

  if (!position) {
      return `<div class="flagPNG" style="background: red;" alt="${CountryName} flag">Flag not found</div>`;
  }

  const imagePath = '/static/images/flags/sprite/sprite.png';
  const backgroundPosition = `-${position.x}px -${position.y}px`;
  return `<div class="flagPNG" style="background-image: url(${imagePath}); background-position: ${backgroundPosition}; width: 30px; height: 20px;" alt="${CountryName} flag"></div>`;
}

function getTooltipNew(countryCode){
  var flag = getFlagEmoji(countryCode);
  var CountryName = regionNames.of(countryCode);
  return `
  <span data-toggle="tooltip" class="flag" style="cursor:context-menu;" data-placement="top" title="${CountryName}">
    ${flag}
  </span>
  `
}

function getLangTooltip(lang, text=false){

  if (lang == "en"){countryCode = "gb";}
  else if (lang == "sv"){countryCode = "se";}
  else if (lang == "sv-FI"){countryCode = "ax";}
  else if (lang == "cs"){countryCode = "cz";}
  else if (lang == "zh"){countryCode = "cn";}
  else if (lang == "da"){countryCode = "dk";}
  else if (lang == "ja"){countryCode = "jp";}
  else if (lang == "et"){countryCode = "ee";}
  else if (lang == "uk"){countryCode = "ua";}
  else if (lang == "ko"){countryCode = "kr";}
  else if (lang == "gsw"){countryCode = "ch";}
  else {countryCode = lang}

  if (countryCode.includes("-")) {
    var flag = getFlagEmoji(countryCode.split("-")[1]);
  }
  else{
    var flag = getFlagEmoji(countryCode);
  }
  
  var langNames = new Intl.DisplayNames(['en'], {type: 'language'});
  var langName = langNames.of(lang);
  var result = `
    <span data-toggle="tooltip" class="flag" style="cursor:context-menu;" data-placement="top" title="${langName}">
      ${flag}
    </span>
    `
  if(text){
    result += ` ${langName}`
  }
  return result
}


function secondsToDhm(seconds, locale, style="narrow") {
    if (typeof seconds !== "number" || isNaN(seconds)) {
      return seconds;
    }
    
    const totalSeconds = Math.floor(seconds);
    
    // Calculate time units (using approximate values)
    const years = Math.floor(totalSeconds / 31536000); // 365 days
    const months = Math.floor((totalSeconds % 31536000) / 2592000); // 30 days
    const days = Math.floor((totalSeconds % 2592000) / 86400);
    const hours = Math.floor((totalSeconds % 86400) / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const secs = totalSeconds % 60;
    
    // Build structured duration
    const duration = {};
    if (years > 0) duration.years = years;
    if (months > 0) duration.months = months;
    if (days > 0) duration.days = days;
    if (hours > 0) duration.hours = hours;
    if (minutes > 0) duration.minutes = minutes;
    
    // Show seconds if:
    // - less than 1 minute total, OR
    // - we already have minutes and some leftover seconds (but no larger units)
    if (totalSeconds < 60 || (minutes > 0 && secs > 0 && !days && !hours && !months && !years)) {
      duration.seconds = secs;
    }
    
    return new Intl.DurationFormat(locale || "en", {
      style: style
    }).format(duration);
  }

function formatDurationAxis(seconds, lang) {
    
    // Define time units in seconds
    const YEAR = 365.25 * 24 * 3600;
    const MONTH = 30.44 * 24 * 3600;
    const DAY = 24 * 3600;
    const HOUR = 3600;
    const MINUTE = 60;
    
    let value, unit;
    
    // Determine the most appropriate unit
    if (seconds >= YEAR) {
        value = seconds / YEAR;
        unit = 'year';
    } else if (seconds >= MONTH) {
        value = seconds / MONTH;
        unit = 'month';
    } else if (seconds >= DAY) {
        value = seconds / DAY;
        unit = 'day';
    } else if (seconds >= HOUR) {
        value = seconds / HOUR;
        unit = 'hour';
    } else {
        value = seconds / MINUTE;
        unit = 'minute';
    }
    
    // Round the value appropriately
    const roundedValue = value >= 10 ? Math.round(value) : Math.round(value * 10) / 10;
    
    // For axis labels, use short format
    try {
        const formatter = new Intl.NumberFormat(lang, {
            style: 'unit',
            unit: unit,
            unitDisplay: 'short',
            maximumFractionDigits: roundedValue >= 10 ? 0 : 1
        });
        return formatter.format(roundedValue);
    } catch (e) {
        // Fallback if unit formatting not supported
        const unitShort = {
            'year': 'y',
            'month': 'mo',
            'day': 'd',
            'hour': 'h',
            'minute': 'm'
        };
        return roundedValue + unitShort[unit];
    }
}

function getGetParams(){
  queryString = window.location.search;
  return new URLSearchParams(queryString);
}

function mToKm(m) {
    var km = m / 1000;
    var value, suffix;

    if (km >= 1000000) {
        value = km / 1000000;
        suffix = 'M';
        return new Intl.NumberFormat(undefined, {
            minimumFractionDigits: 0,
            maximumFractionDigits: 1
        }).format(value) + suffix;
    }

    if (km >= 10) {
        return new Intl.NumberFormat(undefined, {
            minimumFractionDigits: 0,
            maximumFractionDigits: 0
        }).format(km);
    }

    if (km >= 1) {
        return new Intl.NumberFormat(undefined, {
            minimumFractionDigits: 0,
            maximumFractionDigits: 1
        }).format(km);
    }

    return new Intl.NumberFormat(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2
    }).format(km);
}

function isNumber(num){
  if (num === null || num === undefined) return false;
  if (typeof num === 'string' && num.trim() === '') return false;
  return !isNaN(num);
}

function strToArray (str, limit) {
  str = str.replace("-", " ");
  const words = str.split(' ')
  let aux = []
  let concat = []

  for (let i = 0; i < words.length; i++) {
      concat.push(words[i])
      let join = concat.join(' ')
      if (join.length > limit) {
          aux.push(join)
          concat = []
      }
  }

  if (concat.length) {
      aux.push(concat.join(' ').trim())
  }

  return aux
}

function wrapRoutes(routes, limit){
  tot = []
  routes.forEach( route => {
    strToArray(route, limit).forEach(word => {
      tot.push(word);
    })
  })
  return tot
}

function ConfirmDialog(message) {
  $('<div></div>').appendTo('body')
    .html('<div><h6>' + message + '?</h6></div>')
    .dialog({
      modal: true,
      title: 'Delete message',
      zIndex: 10000,
      autoOpen: true,
      width: 'auto',
      resizable: false,
      buttons: {
        Yes: function() {
          // $(obj).removeAttr('onclick');                                
          // $(obj).parents('.Parent').remove();

          $('body').append('<h1>Confirm Dialog Result: <i>Yes</i></h1>');

          $(this).dialog("close");
        },
        No: function() {
          $('body').append('<h1>Confirm Dialog Result: <i>No</i></h1>');

          $(this).dialog("close");
        }
      },
      close: function(event, ui) {
        $(this).remove();
      }
    });
};

function processFormDates(form){
  if (isNumber(form["manDurationHours"]) || isNumber(form["manDurationMinutes"]))
    {
        if(!isNumber(form["manDurationHours"])){form["manDurationHours"] = 0;} 
        if(!isNumber(form["manDurationMinutes"])){form["manDurationMinutes"] = 0;}
        var hours = Number(form["manDurationHours"]) * 3600;
        var minutes = Number(form["manDurationMinutes"]) * 60;
        form["onlyDateDuration"] = hours + minutes
    }
  else
    {
      form["onlyDateDuration"]="";
    }

  form["newTripEnd"] = form["newTripEndDate"] + "T" + form["newTripEndTime"];
  form["newTripStart"] = form["newTripStartDate"] + "T" + form["newTripStartTime"];

  if (isNumber(form["departureDelayMinutes"]) && form["departureDelayMinutes"] !== "") {
    form["departure_delay"] = Number(form["departureDelayMinutes"]) * 60;
  } else {
    form["departure_delay"] = "";
  }
  if (isNumber(form["arrivalDelayMinutes"]) && form["arrivalDelayMinutes"] !== "") {
    form["arrival_delay"] = Number(form["arrivalDelayMinutes"]) * 60;
  } else {
    form["arrival_delay"] = "";
  }

  return form
}

function getCurrentDatetime(){
  const date = new Date();
  const userTimezoneOffset = date.getTimezoneOffset() * 60000;
  const d = new Date(date.getTime() - userTimezoneOffset);
  return d;
}

function getCurrentDate(){
  return getCurrentDatetime().toJSON().slice(0,10);
}

function getCurrentTime(){
  return getCurrentDatetime().toJSON().slice(11,16);
}

function timeDifference(start, end){
  start = new Date(start);
  end = new Date(end);
  
  return (end - start)/1000;
}

function capitalizeFirstLetter(string) {
  return string.charAt(0).toUpperCase() + string.slice(1);
}

function truncate(input, length) {
  if (input.length > length) {
     return input.substring(0, length) + '...';
  }
  return input;
};

function normalizeForSearch(string) {
  // Normalize the comparison (for instance, replace 'č' with 'c'), so "Ceske"
  // finds "Ceskě" and "Rugensche" finds "Rügensche". Spaces are kept, so
  // multi-word searches still work.
  return String(string == null ? '' : string)
    .toLowerCase()
    // NFD splits a letter from its accent, which the range below then drops.
    .normalize('NFD').replace(/[\u0300-\u036f]/g, "")
    // These have no decomposition, so NFD leaves them untouched: without this,
    // "Oresundstag" would never find "Øresundståg".
    .replace(/ø/g, 'o').replace(/æ/g, 'ae').replace(/[đð]/g, 'd')
    .replace(/ł/g, 'l').replace(/þ/g, 'th').replace(/ß/g, 'ss')
    .trim()
}

function getTooltipFromStationNew(station){
  if (station != ""){
    flag = station.substring(0, 4);
    countryCode = getCCfromEmoji(flag);
    stationName = station.substring(4);
    truncated = truncate(stationName, 15);
    var CountryName = regionNames.of(countryCode);
    var newflag = getFlagEmoji(countryCode)
    
    content =  `
      <span class="stationName">
        <span class="flag" data-toggle="tooltip" style="cursor:context-menu;" data-placement="top" title="${sanitize(CountryName)}">
          ${newflag}
        </span>
          ${sanitize(stationName)}
      </span>
    `;
    return content
  }
  else{
    return ''
  }
    
}

function getFlagEmojiListNew(countriesString, tripType, powerType){
  var flagList = "\u00A0";
  var countriesDict = JSON.parse(countriesString);
  var countriesList = Object.keys(countriesDict);
   
  if (countriesList.indexOf("UN") !== -1) {countriesList.splice(countriesList.indexOf("UN"), 1); countriesList.push("UN");}

  // Power icon driven by the stored power_type so the display stays consistent
  // with the trip's recorded power (rather than being re-derived from the
  // countries elec/nonelec split). 'auto'/unknown falls back to that split.
  // metro/tram/aerialway/funicular have no power_type and are always electric
  // (matching force_electric=True in carbon.py), so force ⚡ for them.
  var alwaysElectric = ['metro', 'tram', 'aerialway', 'funicular'].includes(tripType);
  var powerIcon = (powerType === 'electric' || alwaysElectric) ? '⚡'
                : powerType === 'manual'   ? '🦵'
                : powerType === 'thermic'  ? '🛢️'
                : null;
  function totalMeters(d) {
    if (typeof d === 'number') return d;
    if (typeof d === 'object' && d !== null) return (d.elec || 0) + (d.nonelec || 0);
    return 0;
  }
  flagList = [];
  countriesList.forEach(
    function(countryCode){
      var flag = getFlagEmoji(countryCode);
      var CountryName = regionNames.of(countryCode);
      var countryData = countriesDict[countryCode];
      
      var title;
      if (!(countriesList.length == 2 && JSON.stringify(countriesDict[countriesList[0]]) == JSON.stringify(countriesDict[countriesList[1]]))) {
        if (powerIcon) {
          // Explicit power type: one icon for the whole country distance.
          title = `${CountryName} - ${powerIcon}${mToKm(totalMeters(countryData))}km`;
        } else if (typeof countryData === 'number') {
          // Simple distance format: {"FR": 100}
          title = `${CountryName} - ${mToKm(countryData)}km`;
        } else if (typeof countryData === 'object' && countryData !== null) {
          // Complex format: {"FR": {elec: 50, nonelec: 45}}
          var parts = [];
          if (countryData.elec) {
            parts.push(`⚡${mToKm(countryData.elec)}km`);
          }
          if (countryData.nonelec) {
            var nonelecIcon = (tripType === 'cycle' || tripType === 'scooter') ? '🦵' : '🛢️';
            parts.push(`${nonelecIcon}${mToKm(countryData.nonelec)}km`);
          }
          title = `${CountryName} - ${parts.join(' ')}`;
        }
      } else {
        title = `${CountryName}`;
      }
      flagList.push(`<span class="flag" data-toggle="tooltip" style="cursor:context-menu;" data-placement="top" title="${title}">${flag}</span>`);
    }
  )
  flagList=flagList.join(' ')
 
  return flagList;
}

function toRouting(data, routingUrl, type){
  var newTrip = Object.assign({}, ...data.map((x) => ({[x.name]: x.value})));

  newTrip = processFormDates(newTrip);

  if ("originManualToggle" in  newTrip && newTrip["originManualName"].trim()!=""){
    label = newTrip["originManualName"];
    newTrip["originStation"] = [[Number(newTrip["originManualLat"]), Number(newTrip["originManualLng"])], label];

  }
  else{
    newTrip["originStation"] = globalStationDict[newTrip["originStation"]];
  }

  if ("destinationManualToggle" in  newTrip && newTrip["destinationManualName"].trim()!=""){
    label = newTrip["destinationManualName"];
    newTrip["destinationStation"] = [[Number(newTrip["destinationManualLat"]), Number(newTrip["destinationManualLng"])], label];
  }
  else{
    newTrip["destinationStation"] = globalStationDict[newTrip["destinationStation"]];
  }
  if (["accommodation", "restaurant", "poi"].includes(type)){
    newTrip["destinationStation"] = newTrip["originStation"]
  }
  // Collect intermediate "via" waypoints (resolved label -> [coord, label])
  newTrip["viaStations"] = [];
  $(".viaStation").each(function(){
    var val = $(this).val();
    if (val && globalStationDict[val]){
      newTrip["viaStations"].push(globalStationDict[val]);
    }
  });
  // Carry FR24-imported flight data through (set by the FR24 import on the air form)
  if (typeof window.FR24 !== "undefined" && window.FR24){
    newTrip["fr24_id"] = window.FR24["fr24_id"];
    newTrip["fr24_duration"] = window.FR24["fr24_duration"];
  }
  // Collect selected tag IDs from the tag chip list
  newTrip["tag_ids"] = Array.from(document.querySelectorAll('#tagList [data-tag-id]'))
    .map(el => el.dataset.tagId);
  if(
      newTrip["destinationStation"]
      && newTrip["originStation"]
      && newTrip["destinationStation"][1].length > 0
      && newTrip["originStation"][1].length > 0
      && (
        (
          newTrip["precision"] == "unknown" 
          && newTrip["unknownType"]
        )
        ||(
          newTrip["precision"] == "onlyDate"
          && newTrip["onlyDate"]
        )
        ||(
          newTrip["precision"] == "preciseDates"
          && newTrip["newTripStart"].length == 16
          && newTrip["newTripEnd"].length == 16
        )
        ||(
          // Plan-trip relative timing: day offsets required, times optional.
          newTrip["precision"] == "relative"
          && newTrip["planStartDay"]
          && newTrip["planEndDay"]
        )

      )
    ){
    // Generate unique ID 
    var id = performance.now().toString(36)+Math.random().toString(36).replace(/\./g,"");
    // Store data locally with ID
    sessionStorage.setItem(id, JSON.stringify(newTrip));
    // Redirect with ID as param
    location.href = `${routingUrl}?id=${id}&type=${type}`;
  } 
}

function getCountriesCodeList(lang = 'en') {
  const A = 65
  const Z = 90
  const countryName = new Intl.DisplayNames([lang], { type: 'region' });
  const countries = [];
  for(let i=A; i<=Z; ++i) {
      for(let j=A; j<=Z; ++j) {
          let code = String.fromCharCode(i) + String.fromCharCode(j)
          let name = countryName.of(code)
          if (code !== name) {
              countries.push(code)
          }
      }
  }
  return countries
}

// Function to update country names in the dropdown options
function updateCountryNames(selectElement, formatFlagEmoji = false) {
  const options = selectElement.options;
  for (let i = 0; i < options.length; i++) {
      const code = options[i].getAttribute('data-code');
      const countryName = regionNames.of(code); // Get the country name from Intl.DisplayNames
      options[i].textContent = (formatFlagEmoji ? getFlagEmoji(options[i].value) : options[i].value) + '\xa0\xa0' + countryName; // Update option text
  }
}

function levenshteinDistance(a, b) {
  const matrix = [];

  for (let i = 0; i <= b.length; i++) {
    matrix[i] = [i];
  }

  for (let j = 0; j <= a.length; j++) {
    matrix[0][j] = j;
  }

  for (let i = 1; i <= b.length; i++) {
    for (let j = 1; j <= a.length; j++) {
      if (b.charAt(i - 1) == a.charAt(j - 1)) {
        matrix[i][j] = matrix[i - 1][j - 1];
      } else {
        matrix[i][j] = Math.min(
          matrix[i - 1][j - 1] + 1, // substitution
          Math.min(
            matrix[i][j - 1] + 1, // insertion
            matrix[i - 1][j] + 1 // deletion
          )
        );
      }
    }
  }

  return matrix[b.length][a.length];
}

function getSimilarity(a, b) {
  const distance = levenshteinDistance(a.toLowerCase(), b.toLowerCase());
  const maxLength = Math.max(a.length, b.length);
  return (maxLength - distance) / maxLength; // Normalized similarity score
}

function getPositionScore(term, title) {
  const position = title.toLowerCase().indexOf(term.toLowerCase());
  return position === -1 ? 0 : 1 / (position + 1); // Higher score for earlier position
}

function getCompositeScore(station, term) {
  const similarityScore = getSimilarity(term, station.label);
  const positionScore = getPositionScore(term, station.label);
  const occurrenceScore = station.occurrences;
  // Adjust weights as needed
  return (occurrenceScore * 0.3) + (similarityScore * 0.5) + (positionScore * 0.8);
}

function stationSearchAutocomplete(autoClass, visitedStations, url, manual) {
  var currentXhr = null;
  
  $(autoClass).autocomplete({
    minLength: 2,
    delay: 300,
    source: function (request, response) {
      var inputElement = this.element;
      
      // Abort previous request
      if (currentXhr) {
        currentXhr.abort();
      }
      
      // Show spinner
      inputElement.addClass("spinning loading");
      var manStationList = $.ui.autocomplete.filter(manualStationsList, request.term);
      currentXhr = $.ajax({
        url: url,
        dataType: "json",
        data: {
          q: request.term
        },
        success: function (data) {
          var stationList = [];
          data.features.forEach(function (item) {
            flag = getFlagEmoji(item.properties.countrycode);
            label = `${flag} ${item.properties.name}`;
            disambiguation = item.properties.homonymy_order ? [item.properties.street, item.properties.locality, item.properties.district, item.properties.city].filter(e => (e)).join(", ") : null;
            displayLabel = label + (item.properties.homonymy_order ? item.properties.homonymy_order : "");
            stationList.push({ "label": displayLabel, "value": displayLabel, "disambiguation": disambiguation });
            globalStationDict[displayLabel] = [item.geometry.coordinates.reverse(), label];
          });
          // Combine manual stations and fetched stations
          var combinedList = manStationList.concat(stationList);
          // Add occurrences, similarity, and position scores to station objects
          combinedList.forEach(function (station) {
            station.occurrences = visitedStations[station.label] || 0;
            station.similarity = getSimilarity(request.term, station.label);
            station.positionScore = getPositionScore(request.term, station.label);
            station.compositeScore = getCompositeScore(station, request.term);
          });
          // Sort the list by composite score
          combinedList.sort(function (a, b) {
            return b.compositeScore - a.compositeScore;
          });
          // Limit the results to a maximum of 20 elements
          var limitedList = combinedList.slice(0, 20);
          // Hide spinner
          inputElement.removeClass("spinning");
          response(limitedList);
        },
        error: function (xhr, status) {
          // Hide spinner on error (optional)
          inputElement.removeClass("spinning");
          if (status !== 'abort') {
            inputElement.addClass("error");
          }
        }
      });
    },
  }).each(function() {
    $(this).data("ui-autocomplete")._renderItem = function(ul, item) {
      if ('manual' in item) {
        return $("<li>")
          .addClass("manualStationSelect")
          .attr("text", manual)
          .append("<div>" + sanitize(item.label) + "</div>")
          .appendTo(ul);
      } else {
        var disambiguation = "";
        if (item.disambiguation) {
          disambiguation = " <span class='disambiguation'>" + sanitize(item.disambiguation) + "</span>"
        }
        return $("<li>")
          .append("<div>" + sanitize(item.label) + disambiguation + "</div>")
          .appendTo(ul);
      }
    };
  });
}

function sanitize(string) {
  const map = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#x27;',
  };
  const reg = /[&<>"']/ig;
  return string.replace(reg, (match)=>(map[match]));
}

function manualCopyHandler(){
  ['originManualLat', 'destinationManualLat'].forEach(function(inputId) {
    var lastValue = document.getElementById(inputId).value;
    document.getElementById(inputId).addEventListener('paste', function (e) {
        var pastedData = (e.clipboardData || window.clipboardData).getData('text');

        if (pastedData !== lastValue) {
          // 1. Strip out parentheses and extra whitespace
          var strippedData = pastedData.replace(/[()]/g, '').trim();
          
          // Existing regex patterns
          var englishFormat = /^(\-?\d+(\.\d+)?)[,\/\s\u00A0]*(\-?\d+(\.\d+)?)$/;
          var internationalFormat = /^(\-?\d+(,\d+)?)[;\s\u00A0]*(\-?\d+(,\d+)?)$/;

        // 2. Check if it matches your existing formats
        if (englishFormat.test(strippedData)) {
            // Split by comma or slash
            var coordinates = strippedData.split(/[,/]/).map(Number);
            e.preventDefault();
            document.getElementById(inputId).value = coordinates[0];
            document.getElementById(inputId.replace('Lat', 'Lng')).value = coordinates[1];
        } else if (internationalFormat.test(strippedData)) {
            // Split by semicolon
            var coordinates = strippedData.split(';').map(function(item) {
                return Number(item.replace(',', '.'));
            });
            e.preventDefault();
            document.getElementById(inputId).value = coordinates[0];
            document.getElementById(inputId.replace('Lat', 'Lng')).value = coordinates[1];
        }
        lastValue = document.getElementById(inputId).value;
      }
    });
  });
}
 
function computeTimeStatus(data) {
  // Plan legs carry a fixed status from the server (they are hypothetical, never
  // "past"/"current"); don't re-derive it from the anchor-relative dates vs now.
  if (data.lockTime) return data;
  let trip = data.trip;
  if (trip.utc_filtered_start_datetime === 1 && trip.utc_filtered_end_datetime === 1) {
    // Datetimes are both 1
    data.time = 'future';
} else if (trip.utc_filtered_start_datetime === -1 && trip.utc_filtered_end_datetime === -1) {
    // Datetimes are both -1
    data.time = 'past';
} else {
    // Parse the datetimes into moment objects
    let start = typeof trip.utc_filtered_start_datetime === 'string' ? moment.utc(trip.utc_filtered_start_datetime) : null;
    let end = typeof trip.utc_filtered_end_datetime === 'string' ? moment.utc(trip.utc_filtered_end_datetime) : null;
    let now = moment.utc();  // Current UTC time

    if (start && end && now.isBetween(start, end, undefined, '[]')) {
        // Current date is between start and end (inclusive)
        data.time = 'current';
    } else if (start && now.isBefore(start)) {
        // Start and end are in the future
        data.time = 'plannedFuture';
    } else {
        // All other cases, the trip is in the past
        data.time = 'past';
    }
}
return data;
}

function getReadableAge(creationDate) {
  const now = new Date();
  const created = new Date(creationDate);
  const seconds = Math.floor((now - created) / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const monthsTotal = Math.floor(days / 30.44);  // Using the average number of days in a month
  const years = Math.floor(monthsTotal / 12);
  const months = monthsTotal % 12;

  if (seconds < 60) {
      return 'Just now';
  } else if (minutes < 60) {
      return minutes === 1 ? '1 minute' : `${minutes} minutes`;
  } else if (hours < 24) {
      return hours === 1 ? '1 hour' : `${hours} hours`;
  } else if (days < 30.44) {
      return days === 1 ? '1 day' : `${days} days`;
  } else if (monthsTotal < 12) {
      return monthsTotal === 1 ? '1 month' : `${monthsTotal} months`;
  } else if (months === 0) {
      return years === 1 ? '1 year' : `${years} years`;
  } else {
      return years === 1 ? `1 year ${months} months` : `${years} years ${months} months`;
  }
}

function operatorAutocomplete(select, manAndOps, logos_url, no_logo_url, type) {
  select.autocomplete({
    source: function (request, response) {
        // Normalize the search term
        var searchTerm = normalizeForSearch(request.term);
        var matches = $.map(Object.keys(manAndOps.operators), function (item) {
            var normalizedItem = normalizeForSearch(item);
            var index = normalizedItem.indexOf(searchTerm);
            if (index !== -1) {
                return {
                    value: item,
                    index: index
                };
            }
            return null;
        });

        // Sort matches based on the position of the search term (index)
        matches.sort(function (a, b) {
            return a.index - b.index;
        });

        // Extract the sorted operator names
        var sortedOperatorNames = $.map(matches, function (match) {
            return match.value;
        });

        response(sortedOperatorNames);
    },
    select: function (event, ui) {
        var selectedOperator = ui.item.value;
        var logoUrl = manAndOps.operators[selectedOperator] 
                      ? logos_url + manAndOps.operators[selectedOperator] 
                      : no_logo_url;

        $.ajax(logoUrl)
            .done(function () {
                $(".operatorLogo").attr("src", logoUrl);
            })
    }
  });
}

/**
 * Operator field as removable pills, each showing the operator's logo when one is
 * known and the typed name otherwise.
 *
 * A trip's operator is stored as free comma-separated text, and that stays true:
 * `hiddenInput` (the field the form serialises) is kept as "A, B". The pills are
 * only a nicer way to see and edit that list — anything can still be typed, whether
 * or not it matches a known operator.
 *
 * manAndOps.operators maps every known spelling, including aliases, to a logo path
 * or null, so typing CFF or FFS finds the SBB logo just as SBB does.
 */
// Client-side twin of the SQL operator_normalize(): fold case and diacritics, then
// drop everything that is not a letter or digit. Two spellings with the same result
// are the same operator, so "C.F.F", "cff" and "CFF" all resolve alike here exactly
// as they do server-side.
//
// \p{L}\p{N} rather than a-z0-9 for the same reason the SQL uses [:alnum:]: Cyrillic,
// Greek and CJK operator names must survive normalisation instead of collapsing to
// the empty string.
function normalizeOperatorName(value) {
  // Builds on normalizeForSearch (case + accent folding) and additionally drops
  // everything that is not a letter or digit, so "C.F.F", "cff" and "CFF" all come
  // out alike — the same rule as the SQL operator_normalize().
  //
  // \p{L}\p{N} rather than a-z0-9 for the same reason the SQL uses [:alnum:]:
  // Cyrillic, Greek and CJK operator names must survive instead of collapsing to
  // the empty string.
  return normalizeForSearch(value).replace(/[^\p{L}\p{N}]+/gu, '');
}

// `opts.plainToggle` adds a button that swaps the pills for the raw comma-separated
// value, so a whole list of operators can be pasted in or copied out at once —
// something the pills alone made impossible.
function operatorPillsInput(hiddenInput, searchInput, pillContainer, manAndOps, logosUrl, type, opts) {
  var names = [];
  opts = opts || {};

  // Safe to call again on the same elements: the mass-edit form rebuilds the field
  // each time it opens, because the suggestion pool depends on the selected trips.
  // Handlers are namespaced so a rebuild replaces them instead of stacking a second
  // copy that would commit every typed name twice.
  if (searchInput.data('ui-autocomplete')) searchInput.autocomplete('destroy');
  searchInput.off('.opPills');
  pillContainer.off('.opPills');
  // A rebuild starts from the pills, so drop any controls a previous call added.
  pillContainer.find('.op-plain, .op-mode-toggle').remove();
  pillContainer.find('.op-pill-placeholder').show();
  // Built once: every known spelling in its normalised form, so a pill can find its
  // logo without rescanning thousands of names on each render.
  var logosByNormalized = {};
  Object.keys(manAndOps.operators).forEach(function (name) {
    var key = normalizeOperatorName(name);
    if (key && !(key in logosByNormalized)) logosByNormalized[key] = manAndOps.operators[name];
  });

  function logoFor(name) {
    // Exact spelling first, then the normalised form — the user may have typed a
    // punctuated or accented variant of a name we know.
    if (Object.prototype.hasOwnProperty.call(manAndOps.operators, name)) {
      return manAndOps.operators[name];
    }
    var key = normalizeOperatorName(name);
    return key && key in logosByNormalized ? logosByNormalized[key] : null;
  }

  // `silent` skips the change event. Seeding the field from existing data is not a
  // user edit, and callers like the mass-edit form treat a change event as "the user
  // touched this, submit it" — firing it on populate would rewrite the operator of
  // every selected trip.
  function sync(silent) {
    hiddenInput.val(names.join(', '));
    // A native event rather than jQuery's .trigger(): jQuery's only reaches handlers
    // it bound itself, and the mass-edit form tracks edits with addEventListener.
    // Dispatching natively reaches both, since jQuery binds through addEventListener.
    if (!silent) hiddenInput[0].dispatchEvent(new Event('change', { bubbles: true }));
    pillContainer.find('.op-pill').remove();
    names.forEach(function (name, index) {
      var logo = logoFor(name);
      var $pill = $('<span class="op-pill"></span>').attr('title', name);
      if (logo) {
        $pill.addClass('op-pill-logo').append(
          $('<img>').attr('src', logosUrl + logo).attr('alt', name)
        );
      } else {
        // Unknown operator: still a pill, just with its name instead of a logo.
        $pill.append($('<span class="op-pill-text"></span>').text(name));
      }
      $pill.append(
        $('<button type="button" class="op-pill-x" aria-label="Remove">&times;</button>')
          .on('click', function () { names.splice(index, 1); sync(); })
      );
      pillContainer.find('.op-pill-placeholder').before($pill);
    });
    // The placeholder only makes sense while nothing is selected.
    searchInput.attr('placeholder', names.length ? '' : searchInput.data('placeholder'));
    // Pills rebuilt while plain mode is showing (a programmatic set) must stay out
    // of sight until the user switches back.
    if (plainInput && plainInput.is(':visible')) pillContainer.find('.op-pill').hide();
  }

  function add(name) {
    name = (name || '').trim();
    if (!name) return;
    // Case-insensitive duplicate guard, keeping whichever spelling came first.
    var exists = names.some(function (n) { return normalizeForSearch(n) === normalizeForSearch(name); });
    if (!exists) names.push(name);
    searchInput.val('');
    sync();
  }

  function setFromText(text) {
    names = String(text || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    sync(true);
  }

  // Remember the placeholder before the first sync(), which is what hides it.
  searchInput.data('placeholder', searchInput.attr('placeholder') || '');
  // Seed from whatever the field already holds (FR24 import, query params).
  setFromText(hiddenInput.val());

  searchInput.autocomplete({
    minLength: 1,
    source: function (request, response) {
      var term = normalizeForSearch(request.term);
      // Punctuation-free form as well, so "C.F.F" finds CFF. Kept alongside the
      // loose form rather than replacing it: stripping spaces would stop a
      // multi-word term like "sncf nouvelle" matching "SNCF TER Nouvelle-Aquitaine".
      var strictTerm = normalizeOperatorName(request.term);
      var canonicalMap = manAndOps.operatorCanonical || {};

      // The suggestion for a match is the operator's canonical (short) name, not the
      // spelling that matched — so typing "Eth" collapses ETHIAD and "Ethiad airways"
      // into the single "Etihad Airways" rather than listing every alias. Matching
      // still happens against every spelling, so an alias-only term ("ETHIAD") still
      // finds its operator. An unresolved free-text spelling is its own canonical.
      function canonical(name) { return canonicalMap[name] || name; }

      var chosen = names.map(function (n) { return normalizeOperatorName(canonical(n)); });

      // Best match per canonical operator, keyed by its normalised name.
      var best = {};
      function offer(canonName, rank, len) {
        var key = normalizeOperatorName(canonName);
        if (chosen.indexOf(key) !== -1) return;            // operator already a pill
        var prev = best[key];
        if (!prev || rank < prev.rank || (rank === prev.rank && len < prev.len)) {
          best[key] = { label: canonName, value: canonName, rank: rank, len: len };
        }
      }

      Object.keys(manAndOps.operators).forEach(function (name) {
        var canonName = canonical(name);
        var canonLen = normalizeForSearch(canonName).length;
        var norm = normalizeForSearch(name);
        var at = norm.indexOf(term);
        if (at === -1) {
          // No loose match: fall back to the punctuation-free comparison.
          var strictName = normalizeOperatorName(name);
          if (!strictTerm || strictName.indexOf(strictTerm) === -1) return;
          offer(canonName, strictName === strictTerm ? 0 : 3, canonLen);
          return;
        }
        // Rank: exact, then prefix, then start-of-word, then anywhere. Within a
        // rank prefer the shorter operator name, so "SNCF" beats "SNCF Intercités".
        var rank;
        if (norm === term) rank = 0;
        else if (at === 0) rank = 1;
        else if (norm[at - 1] === ' ' || norm[at - 1] === '-') rank = 2;
        else rank = 3;
        offer(canonName, rank, canonLen);
      });

      var matches = Object.keys(best).map(function (k) { return best[k]; });
      matches.sort(function (a, b) { return a.rank - b.rank || a.len - b.len || a.label.localeCompare(b.label); });
      // The operator list runs to thousands of names; an uncapped menu is unusable.
      response(matches.slice(0, 10));
    },
    select: function (event, ui) {
      add(ui.item.value);
      return false;   // don't write the raw value back into the search box
    },
    focus: function () { return false; }
  });

  var instance = searchInput.data('ui-autocomplete');
  if (instance) {
    // Show the logo next to each suggestion, so the right operator is pickable by
    // sight when several spellings look alike.
    instance._renderItem = function (ul, item) {
      var logo = logoFor(item.value);
      var $li = $('<li class="op-suggestion"></li>');
      var $div = $('<div></div>');
      if (logo) {
        $div.append($('<img class="op-suggestion-logo">').attr('src', logosUrl + logo).attr('alt', ''));
      } else {
        $div.append($('<span class="op-suggestion-logo op-suggestion-nologo"></span>'));
      }
      $div.append($('<span></span>').text(item.label));
      // The operator's other name, so a spelling says what it maps to: "SBB" is
      // followed by Schweizerische Bundesbahnen, an alias by the operator it
      // resolves to. Absent for names that would only repeat themselves.
      var hint = (manAndOps.operatorHints || {})[item.value];
      if (hint) $div.append($('<span class="op-suggestion-hint"></span>').text(hint));
      return $li.append($div).appendTo(ul);
    };
  }

  searchInput.on('keydown.opPills', function (e) {
    if (e.key === ',' ) {
      // Comma is the separator in the stored value, so it commits instead of typing.
      e.preventDefault();
      add(searchInput.val());
      searchInput.autocomplete('close');
    } else if (e.key === 'Enter') {
      // Only swallow Enter when there is text to commit, so an empty field still
      // submits the form as before.
      if (searchInput.val().trim()) {
        e.preventDefault();
        add(searchInput.val());
        searchInput.autocomplete('close');
      }
    } else if (e.key === 'Backspace' && !searchInput.val() && names.length) {
      names.pop();
      sync();
    }
  });

  // Plain-text mode: the same value, shown as the comma-separated text that is
  // actually submitted, so it can be pasted in or copied out in one go. It lives
  // inside the pills field and replaces its contents, leaving the field's own
  // chrome — and the mode button — where they are.
  var plainInput = null;
  if (opts.plainToggle) {
    plainInput = $('<input type="text" class="op-plain" autocomplete="off">')
      .attr('placeholder', searchInput.data('placeholder'))
      .hide();
    var modeBtn = $('<button type="button" class="op-mode-toggle"></button>')
      .attr('title', opts.plainLabel || 'Plain text')
      .append($('<i class="fas fa-keyboard"></i>'));
    pillContainer.append(plainInput, modeBtn);

    modeBtn.on('click.opPills', function () {
      if (plainInput.is(':visible')) {
        // Back to pills: the text is the source of truth, so rebuild from it.
        setFromText(plainInput.val());
        plainInput.hide();
        pillContainer.find('.op-pill, .op-pill-placeholder').show();
        modeBtn.attr('title', opts.plainLabel || 'Plain text')
               .find('i').attr('class', 'fas fa-keyboard');
      } else {
        plainInput.val(hiddenInput.val());
        pillContainer.find('.op-pill, .op-pill-placeholder').hide();
        plainInput.show().focus();
        modeBtn.attr('title', opts.pillsLabel || 'Operator list')
               .find('i').attr('class', 'fas fa-tags');
      }
    });

    // Typing in plain mode edits the submitted value directly — the field can be
    // left in this mode and submitted without ever switching back.
    plainInput.on('input.opPills', function () { hiddenInput.val(plainInput.val()); });
  }

  // Clicking anywhere in the field focuses the text input, like a real input would.
  pillContainer.on('click.opPills', function (e) {
    if ($(e.target).closest('.op-pill, .op-plain, .op-mode-toggle').length) return;
    (plainInput && plainInput.is(':visible') ? plainInput : searchInput).focus();
  });

  // A name typed but never committed would otherwise be silently dropped. Commit on
  // blur rather than on form submit: the submit button serialises the form from its
  // own click handler, which runs before the form's submit event. Blur fires first,
  // as focus leaves the field on that click. Clicking an autocomplete suggestion does
  // not blur the input (jQuery UI suppresses it), so this cannot double-add.
  searchInput.on('blur.opPills', function () {
    if (searchInput.val().trim()) add(searchInput.val());
  });

  // Let callers that set the operator programmatically (FR24 import, boarding-pass
  // query params) refresh the pills.
  return {
    set: function (text) {
      setFromText(text);
      if (plainInput && plainInput.is(':visible')) plainInput.val(hiddenInput.val());
    },
    add: add
  };
}

function materialTypeAutocomplete(select, manAndOps, type) {
  select.autocomplete({
    source: function (request, response) {
        // Normalize the search term
        var searchTerm = normalizeForSearch(request.term);
        var matches = $.map(Object.keys(manAndOps.materialTypes), function (item) {
            // Normalize the comparison (for instance, replace 'č' with 'c')
            var normalizedItem = normalizeForSearch(item);
            var index = normalizedItem.indexOf(searchTerm);
            if (index !== -1) {
                return {
                    value: item,
                    index: index
                };
            }
            return null;
        });

        // Sort matches based on the position of the search term (index)
        matches.sort(function (a, b) {
            return a.index - b.index;
        });

        // Extract the sorted operator names
        var sortedMaterialTypes = $.map(matches, function (match) {
            return match.value;
        });

        response(sortedMaterialTypes);
    }
  });
}


function getRegionFromCode(region_code){
  regions = {
    'FR-ARA': 'Auvergne-Rhône-Alpes','FR-BFC': 'Bourgogne-Franche-Comté','FR-BRE': 'Bretagne','FR-CVL': 'Centre-Val de Loire','FR-20R': 'Corse','FR-GES': 'Grand-Est','FR-HDF': 'Hauts-de-France','FR-IDF': 'Île-de-France','FR-NOR': 'Normandie','FR-NAQ': 'Nouvelle-Aquitaine','FR-OCC': 'Occitanie','FR-PDL': 'Pays-de-la-Loire','FR-PAC': 'Provence-Alpes-Côte d’Azur','FR-NCL': 'Nouvelle-Calédonie',
    'DE-BW': 'Baden-Württemberg','DE-BY': 'Bayern','DE-BE': 'Berlin','DE-BB': 'Brandenburg','DE-HB': 'Bremen','DE-HH': 'Hamburg','DE-HE': 'Hessen','DE-MV': 'Mecklenburg-Vorpommern','DE-NI': 'Niedersachsen','DE-NW': 'Nordrhein-Westfalen','DE-RP': 'Rheinland-Pfalz','DE-SL': 'Saarland','DE-SN': 'Sachsen','DE-ST': 'Sachsen-Anhalt','DE-SH': 'Schleswig-Holstein','DE-TH': 'Thüringen',
    'IT-65': 'Abruzzo','IT-77': 'Basilicata','IT-78': 'Calabria','IT-72': 'Campania','IT-45': 'Emilia-Romagna','IT-36': 'Friuli Venezia Giulia','IT-62': 'Lazio','IT-42': 'Liguria','IT-25': 'Lombardia','IT-57': 'Marche','IT-67': 'Molise','IT-21': 'Piemonte','IT-75': 'Puglia','IT-88': 'Sardegna','IT-82': 'Sicilia','IT-52': 'Toscana','IT-32': 'Trentino-Alto Adige','IT-55': 'Umbria','IT-23': "Valle d'Aosta",'IT-34': 'Veneto',
    'TR-01': 'Adana','TR-10': 'Balıkesir','TR-20': 'Denizli','TR-27': 'Gaziantep','TR-38': 'Kayseri','TR-44': 'Malatya','TR-80': 'Osmaniye','TR-62': 'Tunceli','TR-35': 'İzmir','TR-02': 'Adıyaman','TR-72': 'Batman','TR-21': 'Diyarbakır','TR-31': 'Hatay','TR-41': 'Kocaeli','TR-45': 'Manisa','TR-54': 'Sakarya','TR-64': 'Uşak','TR-63': 'Şanlıurfa','TR-03': 'Afyonkarahisar','TR-11': 'Bilecik','TR-22': 'Edirne','TR-32': 'Isparta','TR-42': 'Konya','TR-47': 'Mardin','TR-55': 'Samsun','TR-65': 'Van','TR-05': 'Amasya','TR-12': 'Bingöl','TR-23': 'Elazığ','TR-46': 'Kahramanmaraş','TR-43': 'Kütahya','TR-33': 'Mersin','TR-56': 'Siirt','TR-66': 'Yozgat','TR-06': 'Ankara','TR-13': 'Bitlis','TR-24': 'Erzincan','TR-78': 'Karabük','TR-39': 'Kırklareli','TR-49': 'Muş','TR-58': 'Sivas','TR-67': 'Zonguldak','TR-75': 'Ardahan','TR-15': 'Burdur','TR-25': 'Erzurum','TR-70': 'Karaman','TR-71': 'Kırıkkale','TR-50': 'Nevşehir','TR-59': 'Tekirdağ','TR-18': 'Çankırı','TR-09': 'Aydın','TR-16': 'Bursa','TR-26': 'Eskişehir','TR-36': 'Kars','TR-40': 'Kırşehir','TR-51': 'Niğde','TR-60': 'Tokat','TR-34': 'İstanbul',
    'CH-LU': 'Luzern', 'CH-NE': 'Neuchâtel', 'CH-NW': 'Nidwalden', 'CH-OW': 'Obwalden', 'CH-SG': 'Sankt Gallen', 'CH-SH': 'Schaffhausen', 'CH-SO': 'Solothurn', 'CH-SZ': 'Schwyz', 'CH-TG': 'Thurgau', 'CH-TI': 'Ticino', 'CH-UR': 'Uri', 'CH-AG': 'Aargau', 'CH-VD': 'Vaud', 'CH-AI': 'Appenzell Innerrhoden', 'CH-VS': 'Valais', 'CH-AR': 'Appenzell Ausserrhoden', 'CH-ZG': 'Zug', 'CH-BE': 'Bern', 'CH-ZH': 'Zürich', 'CH-BL': 'Basel-Landschaft', 'CH-BS': 'Basel-Stadt', 'CH-FR': 'Freiburg', 'CH-GE': 'Genève', 'CH-GL': 'Glarus', 'CH-GR': 'Graubünden', 'CH-JU': 'Jura',
    'US-AL': 'Alabama', 'US-AK': 'Alaska', 'US-AZ': 'Arizona', 'US-AR': 'Arkansas', 'US-CA': 'California', 'US-CO': 'Colorado', 'US-CT': 'Connecticut', 'US-DE': 'Delaware', 'US-DC': 'District of Columbia', 'US-FL': 'Florida', 'US-GA': 'Georgia', 'US-HI': 'Hawaii', 'US-ID': 'Idaho', 'US-IL': 'Illinois', 'US-IN': 'Indiana', 'US-IA': 'Iowa', 'US-KS': 'Kansas', 'US-KY': 'Kentucky', 'US-LA': 'Louisiana', 'US-ME': 'Maine', 'US-MD': 'Maryland', 'US-MA': 'Massachusetts', 'US-MI': 'Michigan', 'US-MN': 'Minnesota', 'US-MS': 'Mississippi', 'US-MO': 'Missouri', 'US-MT': 'Montana', 'US-NE': 'Nebraska', 'US-NV': 'Nevada', 'US-NH': 'New Hampshire', 'US-NJ': 'New Jersey', 'US-NM': 'New Mexico', 'US-NY': 'New York', 'US-NC': 'North Carolina', 'US-ND': 'North Dakota', 'US-OH': 'Ohio', 'US-OK': 'Oklahoma', 'US-OR': 'Oregon', 'US-PA': 'Pennsylvania', 'US-RI': 'Rhode Island', 'US-SC': 'South Carolina', 'US-SD': 'South Dakota', 'US-TN': 'Tennessee', 'US-TX': 'Texas', 'US-UT': 'Utah', 'US-VT': 'Vermont', 'US-VA': 'Virginia', 'US-WA': 'Washington', 'US-WV': 'West Virginia', 'US-WI': 'Wisconsin', 'US-WY': 'Wyoming', 'US-AS': 'American Samoa', 'US-GU': 'Guam', 'US-MP': 'Northern Mariana Islands', 'US-PR': 'Puerto Rico', 'US-UM': 'United States Minor Outlying Islands', 'US-VI': 'Virgin Islands, U.S.',
    'CN-BJ': 'Beijing', 'CN-TJ': 'Tianjin', 'CN-HE': 'Hebei', 'CN-SX': 'Shanxi', 'CN-NM': 'Inner Mongolia', 'CN-LN': 'Liaoning', 'CN-JL': 'Jilin', 'CN-HL': 'Heilongjiang', 'CN-SH': 'Shanghai', 'CN-JS': 'Jiangsu', 'CN-ZJ': 'Zhejiang', 'CN-AH': 'Anhui', 'CN-FJ': 'Fujian', 'CN-JX': 'Jiangxi', 'CN-SD': 'Shandong', 'CN-HA': 'Henan', 'CN-HB': 'Hubei', 'CN-HN': 'Hunan', 'CN-GD': 'Guangdong', 'CN-GX': 'Guangxi', 'CN-HI': 'Hainan', 'CN-CQ': 'Chongqing', 'CN-SC': 'Sichuan', 'CN-GZ': 'Guizhou', 'CN-YN': 'Yunnan', 'CN-XZ': 'Tibet', 'CN-SN': 'Shaanxi', 'CN-GS': 'Gansu', 'CN-QH': 'Qinghai', 'CN-NX': 'Ningxia', 'CN-XJ': 'Xinjiang', 'CN-HK': 'Hong Kong', 'CN-MO': 'Macao',
    'AT-2': 'Kärnten', 'AT-4': 'Oberösterreich', 'AT-6': 'Steiermark', 'AT-8': 'Vorarlberg', 'AT-5': 'Salzburg', 'AT-1': 'Burgenland', 'AT-3': 'Niederösterreich', 'AT-7': 'Tirol', 'AT-9': 'Wien',
    "CZ-41": "Karlovarský kraj", "CZ-42": "Ústecký kraj", "CZ-51": "Liberecký kraj", "CZ-52": "Královéhradecký kraj", "CZ-53": "Pardubický kraj", "CZ-63": "Kraj Vysočina", "CZ-64": "Jihomoravský kraj", "CZ-71": "Olomoucký kraj", "CZ-72": "Zlínský kraj", "CZ-80": "Moravskoslezský kraj", "CZ-10": "Praha, Hlavní město", "CZ-20": "Středočeský kraj", "CZ-31": "Jihočeský kraj", "CZ-32": "Plzeňský kraj",
    "GB-NIR": "Northern Ireland", "GB-SCT": "Scotland", "GB-WLS": "Wales" ,"GB-ENG": "England",
    "IE-L": "Leinster", "IE-M": "Munster", "IE-C": "Connacht", "IE-U": "Ulster",
    "SE-K": "Blekinge", "SE-W": "Dalarna", "SE-I": "Gotland", "SE-X": "Gävleborg", "SE-N": "Halland", "SE-Z": "Jämtland", "SE-F": "Jönköping", "SE-H": "Kalmar", "SE-G": "Kronoberg", "SE-BD": "Norrbotten", "SE-M": "Skåne", "SE-AB": "Stockholm", "SE-D": "Södermanland", "SE-C": "Uppsala", "SE-S": "Värmland", "SE-AC": "Västerbotten", "SE-Y": "Västernorrland", "SE-U": "Västmanland", "SE-O": "Västra Götaland", "SE-T": "Örebro", "SE-E": "Östergötland",
    "FI-02": "Etelä-Karjala", "FI-03": "Etelä-Pohjanmaa", "FI-04": "Etelä-Savo", "FI-05": "Kainuu", "FI-06": "Kanta-Häme", "FI-07": "Keski-Pohjanmaa", "FI-08": "Keski-Suomi", "FI-09": "Kymenlaakso", "FI-10": "Lappi", "FI-11": "Pirkanmaa", "FI-12": "Österbotten", "FI-13": "Pohjois-Karjala", "FI-14": "Pohjois-Pohjanmaa", "FI-15": "Pohjois-Savo", "FI-16": "Päijät-Häme", "FI-17": "Satakunta", "FI-18": "Uusimaa", "FI-19": "Varsinais-Suomi",
    "BE-BRU" : "Bruxelles-Capitale", "BE-VLG" : "Vlaanderen", "BE-WAL": "Wallonie",
    "NL-DR": "Drenthe", "NL-FL": "Flevoland", "NL-FR": "Friesland", "NL-GE": "Gelderland", "NL-GR": "Groningen", "NL-LI": "Limburg", "NL-NB": "Noord-Brabant", "NL-NH": "Noord-Holland", "NL-OV": "Overijssel", "NL-UT": "Utrecht", "NL-ZE": "Zeeland", "NL-ZH": "Zuid-Holland",
    "NO-03": "Oslo", "NO-11": "Rogaland", "NO-15": "Møre og Romsdal", "NO-18": "Nordland", "NO-30": "Viken", "NO-34": "Innlandet", "NO-38": "Vestfold og Telemark", "NO-42": "Agder", "NO-46": "Vestland", "NO-50": "Trøndelag",
    "PL-02": "Dolnośląskie", "PL-04": "Kujawsko-Pomorskie", "PL-06": "Lubelskie", "PL-08": "Lubuskie", "PL-10": "Łódzkie", "PL-12": "Małopolskie", "PL-14": "Mazowieckie", "PL-16": "Opolskie", "PL-18": "Podkarpackie", "PL-20": "Podlaskie", "PL-22": "Pomorskie", "PL-24": "Śląskie", "PL-26": "Świętokrzyskie", "PL-28": "Warmińsko-Mazurskie", "PL-30": "Wielkopolskie", "PL-32": "Zachodniopomorskie",
    "JP-01": "Hokkaido", "JP-02": "Aomori", "JP-03": "Iwate", "JP-04": "Miyagi", "JP-05": "Akita", "JP-06": "Yamagata", "JP-07": "Fukushima", "JP-08": "Ibaraki", "JP-09": "Tochigi", "JP-10": "Gunma", "JP-11": "Saitama", "JP-12": "Chiba", "JP-13": "Tokyo", "JP-14": "Kanagawa", "JP-15": "Niigata", "JP-16": "Toyama", "JP-17": "Ishikawa", "JP-18": "Fukui", "JP-19": "Yamanashi", "JP-20": "Nagano", "JP-21": "Gifu", "JP-22": "Shizuoka", "JP-23": "Aichi", "JP-24": "Mie", "JP-25": "Shiga", "JP-26": "Kyoto", "JP-27": "Osaka", "JP-28": "Hyogo", "JP-29": "Nara", "JP-30": "Wakayama", "JP-31": "Tottori", "JP-32": "Shimane", "JP-33": "Okayama", "JP-34": "Hiroshima", "JP-35": "Yamaguchi", "JP-36": "Tokushima", "JP-37": "Kagawa", "JP-38": "Ehime", "JP-39": "Kochi", "JP-40": "Fukuoka", "JP-41": "Saga", "JP-42": "Nagasaki", "JP-43": "Kumamoto", "JP-44": "Oita", "JP-45": "Miyazaki", "JP-46": "Kagoshima", "JP-47": "Okinawa",
    "CA-AB": "Alberta", "CA-BC": "British Columbia", "CA-MB": "Manitoba", "CA-NB": "New Brunswick", "CA-NL": "Newfoundland and Labrador", "CA-NS": "Nova Scotia", "CA-ON": "Ontario", "CA-PE": "Prince Edward Island", "CA-QC": "Quebec", "CA-SK": "Saskatchewan",
    "MX-BCN": "Baja California", "MX-CAM": "Campeche", "MX-CHH": "Chihuahua", "MX-CHP": "Chiapas", "MX-CMX": "Mexico City", "MX-JAL": "Jalisco", "MX-MEX": "State of Mexico", "MX-OAX": "Oaxaca", "MX-ROO": "Quintana Roo", "MX-SIN": "Sinaloa", "MX-TAB": "Tabasco", "MX-VER": "Veracruz", "MX-YUC": "Yucatán",
    "AU-ACT": "Australian Capital Territory", "AU-NSW": "New South Wales", "AU-NT": "Northern Territory", "AU-QLD": "Queensland", "AU-SA": "South Australia", "AU-TAS": "Tasmania", "AU-VIC": "Victoria", "AU-WA": "Western Australia",
    "ES-AN": "Andalucía", "ES-AR": "Aragón", "ES-AS": "Asturias", "ES-CB": "Cantabria", "ES-CE": "Ceuta", "ES-CL": "Castilla y León", "ES-CM": "Castilla-La Mancha", "ES-CN": "Canarias", "ES-CT": "Cataluña", "ES-EX": "Extremadura", "ES-GA": "Galicia", "ES-IB": "Islas Baleares", "ES-MC": "Región de Murcia", "ES-MD": "Comunidad de Madrid", "ES-ML": "Melilla", "ES-NC": "Navarra", "ES-PV": "País Vasco", "ES-RI": "La Rioja", "ES-VC": "Comunidad Valenciana",
    "KR-11": "Seoul", "KR-26": "Busan", "KR-27": "Daegu", "KR-28": "Incheon", "KR-29": "Gwangju", "KR-30": "Daejeon", "KR-31": "Ulsan", "KR-41": "Gyeonggi", "KR-42": "Gangwon", "KR-43": "Chungcheongbuk", "KR-44": "Chungcheongnam", "KR-45": "Jeollabuk", "KR-46": "Jeollanam", "KR-47": "Gyeongsangbuk", "KR-48": "Gyeongsangnam", "KR-49": "Jeju", "KR-50": "Sejong",
    "IN-AP": "Andhra Pradesh", "IN-AR": "Arunachal Pradesh", "IN-AS": "Assam", "IN-BR": "Bihar", "IN-CG": "Chhattisgarh", "IN-CH": "Chandigarh", "IN-DL": "Delhi", "IN-GA": "Goa", "IN-GJ": "Gujarat", "IN-HP": "Himachal Pradesh", "IN-HR": "Haryana", "IN-JH": "Jharkhand", "IN-JK": "Jammu and Kashmir", "IN-KA": "Karnataka", "IN-KL": "Kerala", "IN-MH": "Maharashtra", "IN-ML": "Meghalaya", "IN-MN": "Manipur", "IN-MP": "Madhya Pradesh", "IN-MZ": "Mizoram", "IN-NL": "Nagaland", "IN-OD": "Odisha", "IN-PB": "Punjab", "IN-PY": "Puducherry", "IN-RJ": "Rajasthan", "IN-TN": "Tamil Nadu", "IN-TR": "Tripura", "IN-TS": "Telangana", "IN-UK": "Uttarakhand", "IN-UP": "Uttar Pradesh", "IN-WB": "West Bengal",
    "RU-AD": "Respublika Adygeya", "RU-ALT": "Altayskiy kray", "RU-AMU": "Amurskaya oblast", "RU-ARK": "Arkhangelskaya oblast", "RU-AST": "Astrakhanskaya oblast", "RU-BA": "Respublika Bashkortostan", "RU-BEL": "Belgorodskaya oblast", "RU-BRY": "Bryanskaya oblast", "RU-BU": "Respublika Buryatiya", "RU-CE": "Chechenskaya Respublika", "RU-CHE": "Chelyabinskaya oblast", "RU-CHU": "Chukotskiy avtonomnyy okrug", "RU-CU": "Chuvashskaya Respublika", "RU-DA": "Respublika Dagestan", "RU-IN": "Respublika Ingushetiya", "RU-IRK": "Irkutskaya oblast", "RU-IVA": "Ivanovskaya oblast", "RU-KAM": "Kamchatskiy kray", "RU-KB": "Kabardino-Balkarskaya Respublika", "RU-KC": "Karachayevo-Cherkesskaya Respublika", "RU-KDA": "Krasnodarskiy kray", "RU-KEM": "Kemerovskaya oblast", "RU-KGD": "Kaliningradskaya oblast", "RU-KGN": "Kurganskaya oblast", "RU-KHA": "Khabarovskiy kray", "RU-KHM": "Khanty-Mansiyskiy avtonomnyy okrug", "RU-KIR": "Kirovskaya oblast", "RU-KK": "Respublika Khakasiya", "RU-KL": "Respublika Kalmykiya", "RU-KLU": "Kaluzhskaya oblast", "RU-KO": "Respublika Komi", "RU-KOS": "Kostromskaya oblast", "RU-KR": "Respublika Kareliya", "RU-KRS": "Kurskaya oblast", "RU-KYA": "Krasnoyarskiy kray", "RU-LEN": "Leningradskaya oblast", "RU-LIP": "Lipetskaya oblast", "RU-ME": "Respublika Mariy El", "RU-MO": "Respublika Mordoviya", "RU-MOS": "Moskovskaya oblast", "RU-MOW": "Moskva", "RU-MUR": "Murmanskaya oblast", "RU-NGR": "Novgorodskaya oblast", "RU-NIZ": "Nizhegorodskaya oblast", "RU-NVS": "Novosibirskaya oblast", "RU-OMS": "Omskaya oblast", "RU-ORE": "Orenburgskaya oblast", "RU-ORL": "Orlovskaya oblast", "RU-PER": "Permskiy kray", "RU-PRI": "Primorskiy kray", "RU-PSK": "Pskovskaya oblast", "RU-ROS": "Rostovskaya oblast", "RU-RYA": "Ryazanskaya oblast", "RU-SA": "Respublika Sakha", "RU-SAK": "Sakhalinskaya oblast", "RU-SAM": "Samarskaya oblast", "RU-SAR": "Saratovskaya oblast", "RU-SE": "Respublika Severnaya Osetiya", "RU-SMO": "Smolenskaya oblast", "RU-SPE": "Sankt-Peterburg", "RU-STA": "Stavropolskiy kray", "RU-SVE": "Sverdlovskaya oblast", "RU-TA": "Respublika Tatarstan", "RU-TAM": "Tambovskaya oblast", "RU-TOM": "Tomskaya oblast", "RU-TUL": "Tulskaya oblast", "RU-TVE": "Tverskaya oblast", "RU-TY": "Respublika Tyva", "RU-TYU": "Tyumenskaya oblast", "RU-UD": "Udmurtskaya Respublika", "RU-ULY": "Ulyanovskaya oblast", "RU-VGG": "Volgogradskaya oblast", "RU-VLA": "Vladimirskaya oblast", "RU-VLG": "Vologodskaya oblast", "RU-VOR": "Voronezhskaya oblast", "RU-YAN": "Yamalo-Nenetskiy avtonomnyy okrug", "RU-YAR": "Yaroslavskaya oblast", "RU-YEV": "Evreyskaya avtonomnaya oblast", "RU-ZAB": "Zabaykalskiy kray"
  };
  return regions[region_code]
}

function formatCurrency(locale, value, currency) {
  try {
    let minDecimals = value % 1 === 0 ? 0 : 2;
    let maxDecimals = 2;
    if (Math.abs(value) < 0.1 && Math.abs(value) > 0.0) {
      minDecimals = maxDecimals = 4;
    }
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: currency,
      minimumFractionDigits: minDecimals,
      maximumFractionDigits: maxDecimals
    }).format(value);
  } catch {
    return String(value);
  }
}
function formatNumber(locale, value) {
  // Determine the minimum and maximum number of decimal places based on the value
  let minDecimals = value % 1 === 0 ? 0 : 2; // No decimals for whole numbers, two decimals otherwise
  let maxDecimals = 2; // Default maximum decimal places

  // Increase decimal precision for values less than one cent
  if (Math.abs(value) < 0.1 && Math.abs(value) > 0.0) {
    minDecimals = maxDecimals = 4; // Increase both min and max decimals for very small values
  }
  if (locale == "en"){locale = 'en-IE'}
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: minDecimals,
    maximumFractionDigits: maxDecimals
  }).format(value);
}

function fetchTickets(url, none_text, ticket_id=null) {
  fetch(url)
    .then(response => response.json())
    .then(data => {
      const ticketSearchInput = document.getElementById('ticketSearchInput');
      ticketSearchInput.innerHTML = '';

      // Create a "None" option
      const noneOption = document.createElement('option');
      noneOption.textContent = none_text; // Set text from function parameter
      noneOption.value = ""; 
      ticketSearchInput.add(noneOption);

      // Create an option element for each ticket
      data.tickets.forEach(ticket => {
        if (ticket.active || ticket.uid == ticket_id)
          {
            const option = document.createElement('option');
            option.value = ticket.uid;
            option.setAttribute('data-price', ticket.price);
            option.setAttribute('data-currency', ticket.currency);
            option.setAttribute('data-purchasing-date', ticket.purchasing_date);
            if (ticket.uid == ticket_id){
              option.selected = true; 
            }
    
            // Construct complex HTML for the option's inner HTML
            const optionText = `
              <span class="d-flex align-items-center">
                <i class="bi bi-ticket-perforated me-2"></i>
                <span>${sanitize(String(ticket.name || ''))}</span>
                <span class="ms-auto">${sanitize(String(ticket.price || ''))} ${sanitize(String(ticket.currency || ''))} (${sanitize(String(ticket.purchasing_date || ''))})</span>
              </span>
            `;
            option.innerHTML = optionText;
    
            ticketSearchInput.add(option);
          }
      });
    })
    .catch(error => console.error('Error:', error));
}

function getTextColor(bgColor) {
  // Remove the hash at the start if it's there
  bgColor = bgColor.replace('#', '');

  // Parse the hex color
  const r = parseInt(bgColor.substring(0, 2), 16);
  const g = parseInt(bgColor.substring(2, 4), 16);
  const b = parseInt(bgColor.substring(4, 6), 16);

  // Calculate the luminance of the background color
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;

  // Return black or white based on the luminance
  return luminance > 0.5 ? '#000000' : '#FFFFFF';
}

function makeTagChip(tag) {
  return $('<span>')
    .addClass('tag-blob')
    .attr('data-tag-id', tag.uid)
    .css('background-color', tag.colour)
    .css('color', getTextColor(tag.colour))
    .text(tag.name)
    .on('click', function(event) {
      event.stopPropagation();
      $(this).remove();
    });
}

// setupTagAutocomplete(url, tripId=null)
// tripId: when editing, pre-populate chips for tags already attached to this trip.
// Returns a function getOriginalTagIds() so callers can diff for edit-mode sync.
function setupTagAutocomplete(url, tripId) {
  let tags = [];
  let originalTagIds = new Set();

  fetch(url)
    .then(response => response.json())
    .then(data => {
      tags = data.tags;
      if (tripId != null) {
        tags.filter(t => {
          const ids = t.trip_ids ? t.trip_ids.split(',') : [];
          return ids.includes(String(tripId));
        }).forEach(t => {
          $('#tagList').append(makeTagChip(t));
          originalTagIds.add(String(t.uid));
        });
      }
    })
    .catch(error => console.error('Failed to fetch tags:', error));

  $("#tagSearchInput").autocomplete({
    source: function(request, response) {
      const alreadyIn = new Set(
        Array.from(document.querySelectorAll('#tagList [data-tag-id]')).map(el => el.dataset.tagId)
      );
      const results = $.ui.autocomplete.filter(
        tags.filter(t => !alreadyIn.has(String(t.uid))).map(t => t.name),
        request.term
      );
      response(results);
    },
    select: function(event, ui) {
      const selectedTag = tags.find(t => t.name === ui.item.value);
      if (selectedTag) $('#tagList').append(makeTagChip(selectedTag));
      return false;
    }
  });

  return { getOriginalTagIds: () => originalTagIds };
}


function processCountryCode(cc, positions = null, mode = "auto") {
  var flag, name;
  const useSprite = positions !== null;

  if (cc.includes("-")) {
    var firstHyphenIndex = cc.indexOf("-");
    var parts = cc.split("-");

    const isSpecial = ["CN", "TR"].includes(cc.substring(0, firstHyphenIndex));

    if (mode === "region_only") {
       if (isSpecial) {
        flag = "";
       }
       else{
        flag = useSprite ? getTooltipSprite(parts[0], positions, gb=cc) : getTooltipSVG(parts[0], gb=cc);
       }
       name = getRegionFromCode(cc);
    } else if (mode === "country_only") {
      flag = useSprite ? getTooltipSprite(parts[0], positions) : getTooltipSVG(parts[0]);
      name = regionNames.of(parts[0].toUpperCase());
    } else {
      // 'auto' mode (default): show country + region flags unless it's a special case
      if (!isSpecial) {
        flag = useSprite
          ? getTooltipSprite(parts[0], positions) + " " + getTooltipSprite(parts[0], positions, gb=cc)
          : getTooltipSVG(parts[0]) + " " + getTooltipSVG(parts[0], gb=cc);
      } else {
        flag = useSprite ? getTooltipSprite(parts[0], positions) : getTooltipSVG(parts[0]);
      }
      name = getRegionFromCode(cc);
    }
  } else {
    flag = useSprite ? getTooltipSprite(cc, positions) : getTooltipSVG(cc);
    name = regionNames.of(cc.toUpperCase());
  }

  return { flag, name };
}

// Height a single operator logo occupies in a trip-list cell, matching
// `td .operatorLogo` in style2.css. The multi-operator grid below divides this
// budget between its rows so that the table row never grows taller, whether it
// shows one operator or four.
const OPERATOR_CELL_HEIGHT = 38;
const OPERATOR_CELL_WIDTH = 100;
const OPERATOR_GRID_GAP = 2;
// How many operators the compact cell can show (a 2x2 grid). Beyond this the cell
// cannot represent the trip, so the column is pushed into the responsive detail row
// where every operator is listed — see processHiddenData in dynamic_trips.html.
const OPERATOR_GRID_MAX = 4;

// True when the compact cell would silently drop operators.
function operatorsOverflow(row) {
    return Array.isArray(row.operator_logos) && row.operator_logos.length > OPERATOR_GRID_MAX;
}

// Lay a trip's operators out in a grid that always fits OPERATOR_CELL_HEIGHT:
//   1 -> A        2 -> A B        3 -> A B      4 -> A B
//                                      C             C D
// Anything past the fourth is dropped (the query already caps at 4).
function renderOperatorGrid(operators) {
    const shown = operators.slice(0, OPERATOR_GRID_MAX);
    const cols = shown.length > 1 ? 2 : 1;
    const rows = Math.ceil(shown.length / cols);
    // Split the fixed budget across the rows, leaving room for the gaps, so two
    // stacked rows get roughly half-height logos instead of doubling the row.
    const cellH = (OPERATOR_CELL_HEIGHT - OPERATOR_GRID_GAP * (rows - 1)) / rows;
    const cellW = (OPERATOR_CELL_WIDTH - OPERATOR_GRID_GAP * (cols - 1)) / cols;

    const items = shown.map(op => {
        const name = sanitize(op.name || '');
        if (op.logo) {
            return `<img class="operatorGridLogo" src="/static/${sanitize(op.logo)}"`
                 + ` style="max-height:${cellH}px;max-width:${cellW}px"`
                 + ` data-toggle="tooltip" data-placement="top" title=""`
                 + ` data-bs-original-title="${name}" aria-label="${name}">`;
        }
        // No logo: a text stand-in, scaled down when stacked so it stays on one
        // line and keeps the same height as a logo would.
        const fontSize = rows > 1 ? 0.62 : 0.8;
        return `<span class="operatorGridText" style="max-width:${cellW}px;`
             + `line-height:${cellH}px;font-size:${fontSize}rem" title="${name}">${name}</span>`;
    }).join('');

    return `<div class="operatorGrid" style="grid-template-columns:repeat(${cols},auto);`
         + `gap:${OPERATOR_GRID_GAP}px;height:${OPERATOR_CELL_HEIGHT}px">${items}</div>`;
}

// Expanded form for the responsive detail row, where there is room: every operator on
// its own line at a readable size, rather than the compact cell's four-item grid.
//
// Each line is the operator's logo, or its name when it has none. Operator logos are
// nearly always wordmarks, so printing the name beside one just echoes it; the name
// earns its place only when there is no logo to read. That is the same rule the
// compact cell and the single-operator rendering already follow.
//
// Returns null when there is nothing to expand (one operator, or none), leaving the
// default cell rendering in place.
function renderOperatorList(row) {
    const operators = Array.isArray(row.operator_logos) ? row.operator_logos : null;
    if (!operators || operators.length < 2) return null;

    const items = operators.map(op => {
        const name = sanitize(op.name || '');
        // The logo keeps a tooltip carrying the name — reachable by tap on the touch
        // devices this view is for. resetObjects() re-initialises tooltips whenever a
        // detail row opens, so these are live.
        const content = op.logo
            ? `<img class="operatorListLogo" src="/static/${sanitize(op.logo)}" alt="${name}"`
              + ` data-toggle="tooltip" data-placement="top" title=""`
              + ` data-bs-original-title="${name}" aria-label="${name}">`
            : `<span class="operatorListName">${name}</span>`;
        return `<div class="operatorListItem">${content}</div>`;
    }).join('');

    return `<div class="operatorList">${items}</div>`;
}

function renderOperators(data, type, row) {
    if (type !== 'display') return data;

    // Map transport types to icons and translations
    const iconMap = {
        walk: { icon: "fa-shoe-prints", label: TRANSLATIONS.walk },
        cycle: { icon: "fa-bicycle", label: TRANSLATIONS.cycle },
        car: { icon: "fa-car", label: TRANSLATIONS.car }
    };

    // If type is in iconMap and there's no logo, show icon
    if (iconMap[row.type] && !row.operator) {
        const { icon, label } = iconMap[row.type];
        return `<i class="fas ${icon}" title="${label}" data-toggle="tooltip" data-placement="top" aria-label="${label}"></i>`;
    }

    // Multi-operator trips: show every operator, logo or name, in one cell.
    if (Array.isArray(row.operator_logos) && row.operator_logos.length > 1) {
        return renderOperatorGrid(row.operator_logos);
    }

    // Show logo if available
    if (row.logo_url) {
        return `<img title="" data-toggle="tooltip" data-placement="top" class="operatorLogo" src="/static/${sanitize(row.logo_url)}" data-bs-original-title="${sanitize(row.operator)}" aria-label="${sanitize(row.operator)}">`;
    }

    // Fallback to operator name
    return sanitize(row.operator || '');
}

// Function to calculate CO2 per kilometer
function calculateCO2PerKm(carbonFootprint, tripLength) {
  if (!carbonFootprint || !tripLength || tripLength <= 0) {
    return 0;
  }
  return carbonFootprint / (tripLength / 1000); // Convert meters to kilometers
}

// Function to interpolate between two colors
function interpolateColor(color1, color2, factor) {
  const rgb1 = hexToRgb(color1);
  const rgb2 = hexToRgb(color2);
  
  const r = Math.round(rgb1.r + (rgb2.r - rgb1.r) * factor);
  const g = Math.round(rgb1.g + (rgb2.g - rgb1.g) * factor);
  const b = Math.round(rgb1.b + (rgb2.b - rgb1.b) * factor);
  
  return `rgb(${r}, ${g}, ${b})`;
}

// Helper function to convert hex to RGB
function hexToRgb(hex) {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  return result ? {
    r: parseInt(result[1], 16),
    g: parseInt(result[2], 16),
    b: parseInt(result[3], 16)
  } : null;
}

// Function to get carbon color based on CO2/km
function getCarbonColor(co2PerKm) {
  // Define color stops: Green -> Yellow -> Orange -> Red -> Black
  const colors = ['#28a745', '#ffc107', '#fd7e14', '#dc3545', '#000000'];
  const thresholds = [0, 0.05, 0.15, 0.25, 0.4]; // kg CO2/km thresholds
  
  // Handle edge cases
  if (co2PerKm <= thresholds[0]) return colors[0]; // Green
  if (co2PerKm >= thresholds[thresholds.length - 1]) return colors[colors.length - 1]; // Black
  
  // Find which segment the value falls into
  for (let i = 0; i < thresholds.length - 1; i++) {
    if (co2PerKm >= thresholds[i] && co2PerKm <= thresholds[i + 1]) {
      // Calculate interpolation factor (0-1)
      const factor = (co2PerKm - thresholds[i]) / (thresholds[i + 1] - thresholds[i]);
      return interpolateColor(colors[i], colors[i + 1], factor);
    }
  }
  
  return colors[colors.length - 1]; // Default to black for very high values
}

function formatCarbonValue(value, unit = '') {
  if (value < 1) {
    const grams = value * 1000;
    if (grams < 1) {
      return `< 1 g CO₂eq${unit}`;
    }
    return `${grams.toFixed(0)} g CO₂eq${unit}`;
  }
  if (value < 10) {
    return `${value.toFixed(1)} kg CO₂eq${unit}`;
  }
  if (value < 1000) {
    return `${value.toFixed(0)} kg CO₂eq${unit}`;
  }
  if (value < 1000000) {
    const tons = value / 1000;
    if (tons < 10) {
      return `${tons.toFixed(1)} t CO₂eq${unit}`;
    }
    return `${tons.toFixed(0)} t CO₂eq${unit}`;
  }
  const kilotons = value / 1000000;
  if (kilotons < 10) {
    return `${kilotons.toFixed(1)} kt CO₂eq${unit}`;
  }
  return `${kilotons.toFixed(0)} kt CO₂eq${unit}`;
}

// Function to format CO2/km for display
function formatCO2PerKm(co2PerKm, langId) {
  return formatCarbonValue(co2PerKm, '/km');
}

// Function to format total carbon footprint
function formatCarbonFootprint(carbon, langId) {
  return formatCarbonValue(carbon);
}

function showLoading() {
    document.getElementById('loadingScreen').style.display = 'flex';
}

function hideLoading() {
    document.getElementById('loadingScreen').style.display = 'none';
}

// date.toISOString() returns the date in UTC, we want to return it in local time but in the same format
Date.prototype.toTimezonedISOString = function() {
  return new Date(this.getTime() - (this.getTimezoneOffset() * 60000)).toISOString()
}

// Pick the grammatically correct plural form for the page's locale (read from
// <html lang>). `forms` is a {category: string} map keyed by CLDR plural
// categories (one/few/many/other/…) as returned by Intl.PluralRules, so Slavic
// languages get proper few/many agreement, not just singular/plural. Any category
// a language omits falls back to `other`. A plain string is accepted too (legacy).
function pluralize(forms, n) {
  if (typeof forms === 'string') return forms.replace('{n}', n);
  if (!forms) return '';
  var locale = document.documentElement.lang || 'en';
  var category;
  try {
    category = new Intl.PluralRules(locale).select(n);
  } catch (e) {
    category = new Intl.PluralRules('en').select(n);
  }
  var form = forms[category] || forms.other || forms.one || '';
  return form.replace('{n}', n);
}
window.pluralize = pluralize;
