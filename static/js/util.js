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


function secondsToDhm(seconds, locale) {
  if (typeof(seconds) == "number"){
    seconds = Number(seconds);
    var d = Math.floor(seconds / (3600*24));
    var h = Math.floor(seconds % (3600*24) / 3600);
    var m = Math.floor(seconds % 3600 / 60);
    var dDisplay = d > 0 ? d + (locale=="fr" ? "j " : "d ") : "";
    var hDisplay = h > 0 ? h + "h " : "";
    var mDisplay = m > 0 ? m + "min " : "";
    return dDisplay + hDisplay + mDisplay;
  }
  else{
    return seconds;
  }
 
  }

function getGetParams(){
  queryString = window.location.search;
  return new URLSearchParams(queryString);
}

function mToKm(m) {
  var km = m / 1000;
  if (km > 1) {
    return Math.round(km); // Round to nearest integer
  } else {
    return Number(km.toFixed(2)); // Round to two decimal places
  }
}
function isNumber(num){
  if (!isNaN(num) && num != "" && num != " ")
  {
    return true;
  }
  else
  {
    return false;
  }
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
        <span class="flag" data-toggle="tooltip" style="cursor:context-menu;" data-placement="top" title="${CountryName}">
          ${newflag}
        </span>
          ${stationName}
      </span>
    `;
    return content
  }
  else{
    return ''
  }
    
}

function getFlagEmojiListNew(countriesString){
  var flagList = "\u00A0";
  var countriesDict = JSON.parse(countriesString);
  var countriesList = Object.keys(countriesDict);

    
  if (countriesList.indexOf("UN") !== -1) {countriesList.splice(countriesList.indexOf("UN"), 1); countriesList.push("UN");}

  flagList = [];
  countriesList.forEach( 
    function(countryCode){
      var flag = getFlagEmoji(countryCode);
      var CountryName = regionNames.of(countryCode);
      if (countriesList.length > 1 && !(countriesList.length == 2 && countriesDict[countriesList[0]] == countriesDict[countriesList[1]])) {
        title = `${CountryName} - ${mToKm(countriesDict[countryCode])}km`;
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

      )
    ){
    // Generate unique ID 
    var id = performance.now().toString(36)+Math.random().toString(36).replace(/\./g,"");
    // Store data locally with ID
    sessionStorage.setItem(id, JSON.stringify(newTrip));
    // Redirect with ID as param
    console.log(newTrip);
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
  $(autoClass).autocomplete({
    source: function (request, response) {
      var inputElement = this.element;

      // Show spinner
      inputElement.addClass("spinning loading");

      var manStationList = $.ui.autocomplete.filter(manualStationsList, request.term);

      $.ajax({
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
        error: function () {
          // Hide spinner on error (optional)
          inputElement.removeClass("spinning");
          inputElement.addClass("error")
        }
      });
    },
  }).each(function() {
    $(this).data("ui-autocomplete")._renderItem = function(ul, item) {
      if ('manual' in item) {
        return $("<li>")
          .addClass("manualStationSelect")
          .attr("text", manual)
          .append("<div>" + item.label + "</div>")
          .appendTo(ul);
      } else {
        var disambiguation = "";
        if (item.disambiguation) {
          disambiguation = " <span class='disambiguation'>" + item.disambiguation + "</span>"
        }
        return $("<li>")
          .append("<div>" + item.label + disambiguation + "</div>")
          .appendTo(ul);
      }
    };
  });
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
        var searchTerm = request.term.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, "").trim(); 
        var matches = $.map(Object.keys(manAndOps.operators), function (item) {
            // Normalize the comparison (for instance, replace 'č' with 'c')
            var normalizedItem = item.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, "").trim();
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
    "MX-BCN": "Baja California", "MX-CAM": "Campeche", "MX-CHH": "Chihuahua", "MX-CHP": "Chiapas", "MX-CMX": "Mexico City", "MX-JAL": "Jalisco", "MX-MEX": "State of Mexico", "MX-OAX": "Oaxaca", "MX-ROO": "Quintana Roo", "MX-SIN": "Sinaloa", "MX-TAB": "Tabasco", "MX-VER": "Veracruz", "MX-YUC": "Yucatán"
  };
  return regions[region_code]
}

function formatCurrency(locale, value, currency) {
  // Determine the minimum and maximum number of decimal places based on the value
  let minDecimals = value % 1 === 0 ? 0 : 2; // No decimals for whole numbers, two decimals otherwise
  let maxDecimals = 2; // Default maximum decimal places

  // Increase decimal precision for values less than one cent
  if (Math.abs(value) < 0.1 && Math.abs(value) > 0.0) {
    minDecimals = maxDecimals = 4; // Increase both min and max decimals for very small values
  }

  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: currency,
    minimumFractionDigits: minDecimals,
    maximumFractionDigits: maxDecimals
  }).format(value);
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
      console.log(ticketSearchInput);

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
                <span>${ticket.name}</span>
                <span class="ms-auto">${ticket.price} ${ticket.currency} (${ticket.purchasing_date})</span>
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

function setupTagAutocomplete(url, new_trip) {
  let tags = [];

  fetch(url)
    .then(response => response.json())
    .then(data => {
        tags = data.tags;
    })
    .catch(error => console.error('Failed to fetch tags:', error));
    $("#tagSearchInput").autocomplete({
        source: function(request, response) {
            const results = $.ui.autocomplete.filter(tags.map(tag => tag.name), request.term);
            response(results);
        },
        select: function(event, ui) {
            const selectedTag = tags.find(tag => tag.name === ui.item.value);
            if (selectedTag) {
              const span = $('<span>')
                .addClass('tag-blob')
                .css('background-color', selectedTag.colour)
                .css('color', getTextColor(selectedTag.colour))
                .html(`${selectedTag.name}`)
                .on('click', function(event) {
                    event.stopPropagation();
                    $(this).parent().remove();
                });
              $('#tagList').append(span);
          }
            return false; 
        }
    });
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

function renderOperators(data, type, row) {
    if (type === 'display') {
        // If there is a logo URL, display the logo, otherwise, display the operator name
        if (row.logo_url) {
            return `<img title="" data-toggle="tooltip" data-placement="top" class="operatorLogo" src="/static/${row.logo_url}" data-bs-original-title="${row.operator}" aria-label="${row.operator}">`
        } else {
            return row.operator; // Fallback to operator name if no logo is available
        }
    }
    return data;
}
