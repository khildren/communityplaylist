import math
import requests
from django.core.cache import cache

# Portland, OR
_PDX_LAT  = 45.5231
_PDX_LON  = -122.6765
_MAX_MILES = 150   # Mt. Hood to Tillamook headroom baked in


def _haversine(lat1, lon1, lat2, lon2):
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def geocode_neighborhood(name, max_miles=75):
    """
    Try to geocode '{name}, Portland, OR' via Nominatim.
    Returns (lat, lon) if found within max_miles of Portland, else (None, None).
    max_miles=75 covers Mt. Hood to Tillamook.
    """
    try:
        r = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': f'{name}, Portland, OR', 'format': 'json', 'limit': 1},
            timeout=4,
            headers={'User-Agent': 'communityplaylist.com/neighborhood-geo'},
        )
        data = r.json()
        if not data:
            return None, None
        lat = float(data[0]['lat'])
        lon = float(data[0]['lon'])
        if _haversine(lat, lon, _PDX_LAT, _PDX_LON) <= max_miles:
            return lat, lon
        return None, None
    except Exception:
        return None, None


def ip_near_portland(ip, max_miles=_MAX_MILES):
    """True if IP is within max_miles of Portland. Fails open on any error."""
    if not ip or ip in ('127.0.0.1', '::1', ''):
        return True
    cache_key = f'geo_ip_{ip}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        r = requests.get(
            f'https://ipapi.co/{ip}/json/',
            timeout=3,
            headers={'User-Agent': 'communityplaylist.com/geo-check'},
        )
        data = r.json()
        lat = data.get('latitude')
        lon = data.get('longitude')
        if lat is None or lon is None:
            cache.set(cache_key, True, 3600)
            return True
        miles = _haversine(float(lat), float(lon), _PDX_LAT, _PDX_LON)
        result = miles <= max_miles
        cache.set(cache_key, result, 86400)
        return result
    except Exception:
        cache.set(cache_key, True, 3600)
        return True  # fail open — never block a legit post on API downtime
