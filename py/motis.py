import json
from datetime import datetime
from zoneinfo import ZoneInfo

import polyline
import requests
from flask import request, session, redirect, url_for, render_template, Response


def convert_motis_to_trip(itinerary, username):
    """Convert MOTIS itinerary to internal trip format"""
    
    # Extract basic info
    start_time = itinerary['startTime']
    end_time = itinerary['endTime']
    duration = itinerary['duration']
    
    # Get first and last legs for origin/destination
    first_leg = itinerary['legs'][0]
    last_leg = itinerary['legs'][-1]
    
    # Determine primary transport mode
    transit_legs = [leg for leg in itinerary['legs'] if leg['mode'] != 'WALK']
    primary_mode = 'walk'
    operator = ''
    line_name = ''
    
    if transit_legs:
        primary_leg = transit_legs[0]
        mode_mapping = {
            'RAIL': 'train',
            'SUBWAY': 'metro', 
            'METRO': 'metro',
            'TRAM': 'tram',
            'BUS': 'bus',
            'FERRY': 'ferry',
            'AIRPLANE': 'air'
        }
        primary_mode = mode_mapping.get(primary_leg['mode'], 'train')
        operator = primary_leg.get('agencyName', '')
        line_name = primary_leg.get('routeShortName', '')
    
    # Extract coordinates and create path using polyline library
    path_coordinates = []
    
    for leg in itinerary['legs']:
        # Add start point
        path_coordinates.append({
            'lat': leg['from']['lat'],
            'lng': leg['from']['lon']
        })
        
        # Add intermediate points from geometry if available
        if 'legGeometry' in leg and leg['legGeometry'].get('points'):
            try:
                # Use polyline library to decode the geometry with proper precision
                # MOTIS typically uses precision 5, but check if specified
                precision = leg['legGeometry'].get('precision', 5)
                
                decoded_points = polyline.decode(
                    leg['legGeometry']['points'], 
                    precision=precision
                )
                
                # Convert to our coordinate format, skip first/last to avoid duplicates
                for point in decoded_points[1:-1]:
                    path_coordinates.append({
                        'lat': point[0],
                        'lng': point[1]
                    })
                    
                print(f"Decoded {len(decoded_points)} points with precision {precision} for {leg['mode']} leg")
                
            except Exception as e:
                print(f"Error decoding polyline for leg {leg['mode']}: {e}")
                print(f"Polyline data: {leg['legGeometry']['points'][:50]}...")
                # Fall back to just start/end points
    
    # Add final destination
    path_coordinates.append({
        'lat': last_leg['to']['lat'],
        'lng': last_leg['to']['lon']
    })
    
    # Calculate total distance
    total_distance = sum(leg.get('distance', 0) for leg in itinerary['legs'])
    
    # Build trip data
    trip_data = {
        'type': primary_mode,
        'originStation': [
            [first_leg['from']['lat'], first_leg['from']['lon']],
            first_leg['from']['name']
        ],
        'destinationStation': [
            [last_leg['to']['lat'], last_leg['to']['lon']],
            last_leg['to']['name']
        ],
        'newTripStart': start_time,
        'newTripEnd': end_time,
        'operator': operator,
        'lineName': line_name,
        'waypoints': '[]',  # Empty for now
        'price': '',
        'purchasing_date': datetime.now().strftime('%Y-%m-%d'),
        'currency': 'EUR',
        'destinationManualLat': '',
        'destinationManualLng': '',
        'destinationManualName': '',
        'estimated_trip_duration': duration,
        'manDurationHours': '',
        'manDurationMinutes': '',
        'material_type': '',
        'onlyDate': '',
        'onlyDateDuration': '',
        'originManualLat': '',
        'originManualLng': '',
        'originManualName': '',
        'reg': '',
        'seat': '',
        'ticket_id': '',
        'trip_length': total_distance,
        'precision': 'preciseDates',
        'notes': f'Imported from MOTIS routing - {itinerary["transfers"]} transfers'
    }
    
    return {
        'trip': trip_data,
        'path': path_coordinates
    }

def call_motis_api(forwardRouting=None):
    """Call MOTIS API with current request parameters - returns waypoints only"""
    try:
        # Build MOTIS parameters (same as before)
        params = {}
        
        # Required parameters
        from_place = request.args.get('fromPlace')
        to_place = request.args.get('toPlace')
        
        if not from_place or not to_place:
            return {'error': 'Missing origin or destination'}
            
        params['fromPlace'] = from_place
        params['toPlace'] = to_place
        from_tz = request.args.get('fromTz')
        to_tz = request.args.get('toTz')
        if not from_tz and to_tz:
            from_tz = to_tz
        elif not to_tz and from_tz:
            to_tz = from_tz

        # Boolean parameters - fix string conversion
        arrive_by = request.args.get('arriveBy', 'false')
        if arrive_by and arrive_by.lower() not in ['none', '']:
            params['arriveBy'] = arrive_by.lower() == 'true'
        else:
            params['arriveBy'] = False

        tz_str = to_tz if params['arriveBy'] else from_tz
        tz = ZoneInfo(tz_str) if tz_str else None

        # Time parameter - fix the "None" issue
        time_param = request.args.get('time')
        if time_param and time_param != 'None' and not time_param.startswith('NoneT'):
            try:
                # Validate and clean the time parameter
                if 'T' in time_param:
                    # Remove any "None" prefix
                    clean_time = time_param.replace('NoneT', '').replace('None', '')
                    if clean_time:
                        # Validate the datetime format
                        params['time'] = datetime.fromisoformat(clean_time.replace('Z', '+00:00')).replace(tzinfo=tz).isoformat()
                else:
                    # If no 'T', assume it's a date and add current time
                    clean_time = time_param.replace('None', '')
                    if clean_time and len(clean_time) == 10:  # YYYY-MM-DD format
                        params['time'] = datetime.fromisoformat(f"{clean_time}T12:00:00").replace(tzinfo=tz).isoformat()
            except (ValueError, AttributeError):
                # If time parsing fails, don't include time parameter (will use current time)
                pass
            
        params['detailedTransfers'] = True
        params['timetableView'] = True
        
        # Numeric parameters with validation
        num_itineraries = request.args.get('numItineraries', '5')
        try:
            params['numItineraries'] = max(1, min(20, int(num_itineraries)))
        except (ValueError, TypeError):
            params['numItineraries'] = 5
        
        # Optional numeric parameters
        max_transfers = request.args.get('maxTransfers')
        if max_transfers and max_transfers.lower() not in ['none', '', 'null']:
            try:
                params['maxTransfers'] = max(0, int(max_transfers))
            except (ValueError, TypeError):
                pass
        
        max_travel_time = request.args.get('maxTravelTime')
        if max_travel_time and max_travel_time.lower() not in ['none', '', 'null']:
            try:
                params['maxTravelTime'] = max(1, int(max_travel_time))
            except (ValueError, TypeError):
                pass
        
        search_window = request.args.get('searchWindow', '7200')
        try:
            params['searchWindow'] = max(300, int(search_window))  # Min 5 minutes
        except (ValueError, TypeError):
            params['searchWindow'] = 7200
        
        # String parameters with validation
        pedestrian_profile = request.args.get('pedestrianProfile', 'FOOT')
        if pedestrian_profile in ['FOOT', 'WHEELCHAIR']:
            params['pedestrianProfile'] = pedestrian_profile
        else:
            params['pedestrianProfile'] = 'FOOT'
            
        elevation_costs = request.args.get('elevationCosts', 'NONE')
        if elevation_costs in ['NONE', 'LOW', 'HIGH']:
            params['elevationCosts'] = elevation_costs
        else:
            params['elevationCosts'] = 'NONE'
        
        # Boolean parameters
        use_routed_transfers = request.args.get('useRoutedTransfers', 'false')
        params['useRoutedTransfers'] = use_routed_transfers.lower() == 'true'
        
        require_bike = request.args.get('requireBikeTransport', 'false')
        params['requireBikeTransport'] = require_bike.lower() == 'true'
        
        require_car = request.args.get('requireCarTransport', 'false')
        params['requireCarTransport'] = require_car.lower() == 'true'
        
        # Mode parameters - handle arrays properly
        valid_transit_modes = {
            'TRANSIT', 'RAIL', 'SUBWAY', 'METRO', 'TRAM', 'BUS', 'FERRY', 
            'AIRPLANE', 'HIGHSPEED_RAIL', 'LONG_DISTANCE', 'NIGHT_RAIL', 
            'REGIONAL_FAST_RAIL', 'REGIONAL_RAIL', 'CABLE_CAR', 'FUNICULAR', 'AREAL_LIFT'
        }
        
        valid_direct_modes = {'WALK', 'BIKE', 'CAR', 'RENTAL'}

        # Transit modes
        transit_modes_param = request.args.get('transitModes', '')
        if transit_modes_param and transit_modes_param.lower() not in ['none', '']:
            transit_modes = [mode.strip().upper() for mode in transit_modes_param.split(',')]
            valid_modes = [mode for mode in transit_modes if mode in valid_transit_modes]
            if valid_modes:
                params['transitModes'] = ','.join(valid_modes)
            else:
                params['transitModes'] = 'TRANSIT'  # Default fallback
        else:
            params['transitModes'] = 'TRANSIT'

        # Direct modes  
        direct_modes_param = request.args.get('directModes', '')
        if direct_modes_param and direct_modes_param.lower() not in ['none', '']:
            direct_modes = [mode.strip().upper() for mode in direct_modes_param.split(',')]
            valid_modes = [mode for mode in direct_modes if mode in valid_direct_modes]
            if valid_modes:
                params['directModes'] = ','.join(valid_modes)
            else:
                params['directModes'] = 'WALK'  # Default fallback
        else:
            params['directModes'] = 'WALK'

        # Pre/post transit modes (copy from direct modes if not specified)
        params['preTransitModes'] = params['directModes']
        params['postTransitModes'] = params['directModes']

        # Pagination cursor
        page_cursor = request.args.get('pageCursor')
        if page_cursor and page_cursor.lower() not in ['none', '', 'null']:
            params['pageCursor'] = page_cursor
        
        # Language
        params['language'] = session.get("userinfo", {}).get("lang", "en")
        
        # Log the cleaned parameters for debugging
        print(f"MOTIS API call with params: {params}")
        
        # Make API call
        response = requests.get(
            "https://api.transitous.org/api/v3/plan",
            params=params,
            timeout=30
        )
        
        print(f"MOTIS API URL: {response.url}")
        
        if response.status_code == 200:
            data = response.json()
            
            # Extract waypoints from all itineraries instead of routing
            if 'itineraries' in data:
                for itinerary in data['itineraries']:
                    for leg in itinerary.get('legs', []):
                        # Gather all stops for frontend routing: from, intermediateStops, to
                        waypoints = []

                        # Always start with 'from'
                        if 'from' in leg:
                            waypoints.append({
                                'lat': leg['from']['lat'], 
                                'lng': leg['from']['lon'],
                                'name': leg['from'].get('name', ''),
                                'type': 'from'
                            })

                        # Then any intermediate stops
                        for stop in leg.get('intermediateStops', []):
                            waypoints.append({
                                'lat': stop['lat'], 
                                'lng': stop['lon'],
                                'name': stop.get('name', ''),
                                'type': 'intermediate'
                            })

                        # Always end with 'to'
                        if 'to' in leg:
                            waypoints.append({
                                'lat': leg['to']['lat'], 
                                'lng': leg['to']['lon'],
                                'name': leg['to'].get('name', ''),
                                'type': 'to'
                            })

                        # Store waypoints and routing info for frontend
                        leg['waypoints'] = waypoints
                        leg['routingMode'] = leg.get('mode', '').upper()
                        
                        # Remove the old geometry since frontend will generate it
                        if 'legGeometry' in leg:
                            del leg['legGeometry']
        
            # Add some metadata
            data['search_params'] = dict(request.args)
            data['search_time'] = datetime.now().isoformat()
            return data
        else:
            print(f"MOTIS API error: {response.status_code} - {response.text}")
            return {'error': f'MOTIS API error: {response.status_code}'}
            
    except requests.RequestException as e:
        print(f"Network error: {str(e)}")
        return {'error': f'Network error: {str(e)}'}
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return {'error': f'Unexpected error: {str(e)}'}

def handle_search_form(username):
    """Handle form submission from search page"""
    try:
        # Get form data and validate
        from_place = request.form.get('fromPlace', '').strip()
        to_place = request.form.get('toPlace', '').strip()
        date = request.form.get('date', '').strip()
        time = request.form.get('time', '').strip()
        
        if not from_place or not to_place:
            return "Missing origin or destination", 400
        
        # Build URL parameters for redirect
        params = {
            'fromPlace': from_place,
            'toPlace': to_place,
        }
        
        # Add place names if available
        from_name = request.form.get('fromName', '').strip()
        to_name = request.form.get('toName', '').strip()
        if from_name:
            params['fromName'] = from_name
        if to_name:
            params['toName'] = to_name
        
        # Handle time - ensure we don't send "None"
        if date and time and date != 'None' and time != 'None':
            params['time'] = f"{date}T{time}"
        elif time and time != 'None':
            params['time'] = time

        # Handle boolean parameters
        arrive_by = request.form.get('arriveBy', 'false') != 'false'
        params['arriveBy'] = str(arrive_by).lower()
        
        detailed_transfers = request.form.get('detailedTransfers', 'true')
        params['detailedTransfers'] = detailed_transfers
        
        # Add optional parameters only if they have valid values
        optional_params = [
            'maxTransfers', 'numItineraries', 'pedestrianProfile',
            'elevationCosts', 'searchWindow', 'fromTz', 'toTz',
        ]
        for param in optional_params:
            value = request.form.get(param, '').strip()
            if value and value != 'None' and value != 'null':
                params[param] = value
        
        # Handle transport modes - these come as lists
        transit_modes = request.form.getlist('transitMode')
        direct_modes = request.form.getlist('directMode')
        
        # Filter out any "None" values and join
        if transit_modes:
            clean_transit_modes = [mode for mode in transit_modes if mode and mode != 'None']
            if clean_transit_modes:
                params['transitModes'] = ','.join(clean_transit_modes)
        
        if direct_modes:
            clean_direct_modes = [mode for mode in direct_modes if mode and mode != 'None']
            if clean_direct_modes:
                params['directModes'] = ','.join(clean_direct_modes)
        
        # Handle boolean checkboxes - only add if present
        boolean_params = [
            'requireBikeTransport', 'requireCarTransport', 
            'useRoutedTransfers'
        ]
        for param in boolean_params:
            if request.form.get(param, 'false') != 'false':
                params[param] = 'true'
        
        # Clean up params - remove any remaining None values
        clean_params = {}
        for key, value in params.items():
            if value is not None and str(value).strip() and str(value) != 'None':
                clean_params[key] = value
        
        # Redirect to results page with clean parameters
        return redirect(url_for('motis_results', username=username, **clean_params))
        
    except Exception as e:
        print(f"Form handling error: {e}")
        return f"Search error: {e}", 400

def handle_search_params(username, forwardRouting, lang):
    """Handle GET request with URL parameters"""
    try:
        # Get all parameters
        from_place = request.args.get('fromPlace')
        to_place = request.args.get('toPlace')
        
        if not from_place or not to_place:
            # No search params, redirect to search page
            return redirect(url_for('motis_search', username=username))
        
        # Call MOTIS API
        motis_data = call_motis_api(forwardRouting)
        
        if 'error' in motis_data:
            return render_template(
                "motis_results.html",
                error=motis_data['error'],
                username=username,
                **lang[session["userinfo"]["lang"]],
                **session["userinfo"],
            )
        
        # Render results page with data
        return render_template(
            "motis_results.html",  # This is your second artifact
            motis_data=json.dumps(motis_data),
            search_params=dict(request.args),
            username=username,
            **lang[session["userinfo"]["lang"]],
            **session["userinfo"],
        )
        
    except Exception as e:
        return render_template(
            "motis_results.html",
            error=f"Failed to load results: {e}",
            username=username,
            **lang[session["userinfo"]["lang"]],
            **session["userinfo"],
        )