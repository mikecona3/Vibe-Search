import time
from math import atan2, cos, radians, sin, sqrt

import requests

_USER_AGENT = "JobScraperGUI/1.0 (personal desktop tool)"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_MIN_REQUEST_INTERVAL = 1.1  # Nominatim's usage policy asks for ~1 request/second
_EARTH_RADIUS_MILES = 3958.8

_cache: dict[str, "tuple[float, float] | None"] = {}
_last_request_time = 0.0


def geocode(location: str) -> "tuple[float, float] | None":
    """Resolve free-text `location` to (lat, lon) via Nominatim, or None if
    it can't be resolved. Results are cached in-memory since the same
    location text is looked up repeatedly across many job postings.
    """
    key = location.strip().lower()
    if not key:
        return None
    if key in _cache:
        return _cache[key]

    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        time.sleep(_MIN_REQUEST_INTERVAL - elapsed)

    try:
        resp = requests.get(
            _NOMINATIM_URL,
            params={"q": location, "format": "json", "limit": 1},
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
        )
        _last_request_time = time.monotonic()
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError):
        _last_request_time = time.monotonic()
        _cache[key] = None
        return None

    if not results:
        _cache[key] = None
        return None

    coords = (float(results[0]["lat"]), float(results[0]["lon"]))
    _cache[key] = coords
    return coords


def haversine_miles(a: "tuple[float, float]", b: "tuple[float, float]") -> float:
    """Great-circle distance between two (lat, lon) points, in miles."""
    lat1, lon1 = radians(a[0]), radians(a[1])
    lat2, lon2 = radians(b[0]), radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return _EARTH_RADIUS_MILES * 2 * atan2(sqrt(h), sqrt(1 - h))
