import requests
import re
import time

NOMINATIM_HEADERS = {'User-Agent': 'CommunityPlaylist/1.0 (andrew.jubinsky@proton.me)'}

# Portland metro bounding box — generous enough to include Vancouver WA,
# Beaverton, Gresham, Lake Oswego, Hillsboro, Canby, Scappoose.
# Anything that geocodes OUTSIDE this box is not a Portland-area event.
PDX_BOUNDS = {
    'lat_min': 45.15,
    'lat_max': 45.80,
    'lng_min': -123.25,
    'lng_max': -122.20,
}


def is_in_pdx_area(lat, lng):
    """Return True if coordinates are within the Portland metro bounding box."""
    if lat is None or lng is None:
        return True  # no coords = don't auto-reject
    b = PDX_BOUNDS
    return b['lat_min'] <= lat <= b['lat_max'] and b['lng_min'] <= lng <= b['lng_max']

# PDX-area neighborhoods returned by Nominatim reverse geocode
# Maps Nominatim suburb/neighbourhood/city_district values to clean display names
NEIGHBORHOOD_ALIASES = {
    'northwest portland': 'NW Portland',
    'nw portland': 'NW Portland',
    'pearl district': 'Pearl District',
    'old town': 'Old Town',
    'old town chinatown': 'Old Town',
    'downtown': 'Downtown',
    'southwest portland': 'SW Portland',
    'sw portland': 'SW Portland',
    'southeast portland': 'SE Portland',
    'se portland': 'SE Portland',
    'northeast portland': 'NE Portland',
    'ne portland': 'NE Portland',
    'north portland': 'N Portland',
    'n portland': 'N Portland',
    'alberta arts district': 'Alberta Arts',
    'alberta': 'Alberta Arts',
    'mississippi': 'Mississippi Ave',
    'williams': 'Williams Ave',
    'division': 'Division',
    'hawthorne': 'Hawthorne',
    'belmont': 'Belmont',
    'richmond': 'Richmond',
    'buckman': 'Buckman',
    'kerns': 'Kerns',
    'lloyd district': 'Lloyd District',
    'lloyd': 'Lloyd District',
    'irvington': 'Irvington',
    'grant park': 'Grant Park',
    'beaumont': 'Beaumont',
    'hollywood': 'Hollywood',
    'montavilla': 'Montavilla',
    'mt tabor': 'Mt Tabor',
    'mount tabor': 'Mt Tabor',
    'sellwood': 'Sellwood',
    'moreland': 'Sellwood-Moreland',
    'woodstock': 'Woodstock',
    'brooklyn': 'Brooklyn',
    'foster': 'Foster-Powell',
    'foster-powell': 'Foster-Powell',
    'lents': 'Lents',
    'st johns': 'St Johns',
    'saint johns': 'St Johns',
    'cathedral park': 'St Johns',
    'arbor lodge': 'Arbor Lodge',
    'kenton': 'Kenton',
    'overlook': 'Overlook',
    'boise': 'Boise',
    'eliot': 'Eliot',
    'sullivan\'s gulch': 'Sullivan\'s Gulch',
    'portland': '',  # too generic — skip
}


def _extract_address(location_string):
    """
    Pull the best geocodable string from a location field.
    Handles patterns like:
      'Venue Name, 123 SE Main St, Portland, OR 97214, USA'
      '123 SE Main St, Portland, OR'
      'NW Broadway & NW Morrison, Portland, OR'  -> uses first street only
      'The Goodfoot'  (venue-only, no street)
    """
    s = location_string.strip()
    # Remove trailing ', USA' or ', United States'
    s = re.sub(r',?\s*(USA|United States)\s*$', '', s, flags=re.I).strip()

    # Corner/intersection addresses: "Street A & Street B[, City...]"
    # Nominatim doesn't reliably resolve these — use only the first street.
    amp = re.search(r'\s*&\s*', s)
    if amp:
        # Keep everything before the '&', then any trailing ", City, ST zip"
        before = s[:amp.start()].strip()
        # Grab city/state/zip suffix that follows the second street name
        after_cross = s[amp.end():]
        suffix_match = re.search(r',.*$', after_cross)
        suffix = suffix_match.group(0) if suffix_match else ''
        s = before + suffix

    # If it already contains a street number pattern, try to start from there
    # e.g. "Venue Name, 123 SE Main St, Portland, OR 97214"
    street_match = re.search(r'\b\d+\s+[NSEW]?[A-Za-z]', s)
    if street_match:
        return s[street_match.start():]

    return s


class NominatimRateLimited(Exception):
    pass


def geocode_location(location_string):
    """Forward geocode. Returns (lat, lng) or (None, None).
    Raises NominatimRateLimited on HTTP 429 so callers can abort early."""
    if not location_string or location_string.startswith(('http://', 'https://', 'www.')):
        return None, None
    try:
        q = _extract_address(location_string)
        # Only append Portland OR if no state/zip already present
        if not re.search(r'\b[A-Z]{2}\b|\b\d{5}\b', q):
            q = q + ', Portland, OR'
        r = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': q, 'format': 'json', 'limit': 1},
            headers=NOMINATIM_HEADERS,
            timeout=5,
        )
        if r.status_code == 429:
            raise NominatimRateLimited('Nominatim rate limit hit (429)')
        data = r.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except NominatimRateLimited:
        raise
    except Exception:
        pass
    return None, None


def reverse_geocode_neighborhood(lat, lng):
    """Reverse geocode lat/lng to a PDX neighborhood string. Returns '' if unknown."""
    if lat is None or lng is None:
        return ''
    try:
        time.sleep(0.5)  # Nominatim rate limit courtesy
        r = requests.get(
            'https://nominatim.openstreetmap.org/reverse',
            params={'lat': lat, 'lon': lng, 'format': 'json', 'zoom': 15},
            headers=NOMINATIM_HEADERS,
            timeout=5,
        )
        data = r.json()
        addr = data.get('address', {})
        # Try fields in order of specificity
        for field in ('neighbourhood', 'suburb', 'city_district', 'quarter'):
            raw = addr.get(field, '').strip().lower()
            if raw:
                # Check aliases first
                if raw in NEIGHBORHOOD_ALIASES:
                    return NEIGHBORHOOD_ALIASES[raw]
                # Fallback: title-case it if it's a Portland neighbourhood
                city = addr.get('city', '').lower()
                if 'portland' in city or 'portland' in raw:
                    return raw.title()
                return raw.title()
    except Exception:
        pass
    return ''
