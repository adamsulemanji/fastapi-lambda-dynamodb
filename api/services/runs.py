"""
Recent runs (with route maps) for the personal site, sourced from Garmin Connect.

Strava's API needs a paid subscription since mid-2026, so runs come straight
from the watch's own service via python-garminconnect (unofficial, but the
de-facto standard client).

Auth: `scripts/garmin_login.py` is run once locally; it logs in (password and
MFA typed there, never stored) and writes the resulting session tokens as JSON
into a Secrets Manager secret. This module loads them, lets the library refresh
them as needed, and writes them back whenever they change.

Runs are cached in DynamoDB so page views never hit Garmin directly.
"""

import json
import logging
import math
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import boto3

logger = logging.getLogger(__name__)

SECRET_ID = os.environ.get("GARMIN_SECRET_ID", "garmin/tokens")
TABLE_NAME = os.environ.get("RUNS_TABLE_NAME", "runs")
CACHE_KEY = "garmin"
CACHE_TTL_SECONDS = int(os.environ.get("RUNS_CACHE_TTL_SECONDS", "1800"))
# Trim each end of the route so the map doesn't publish where runs start and
# finish (e.g. home). Garmin returns the full track.
PRIVACY_TRIM_METERS = float(os.environ.get("RUNS_PRIVACY_TRIM_METERS", "300"))
MAX_CACHED_RUNS = 5
ACTIVITY_PAGE_SIZE = 40
MAX_ROUTE_POINTS = 600
MAX_SERIES_POINTS = 160
# Outdoor running types; treadmill/indoor/virtual have no GPS track.
RUN_TYPES = {"running", "trail_running", "track_running", "street_running", "ultra_run"}

_secrets = boto3.client("secretsmanager")
_table = boto3.resource("dynamodb").Table(TABLE_NAME)
_refresh_lock = threading.Lock()


class GarminNotConnected(RuntimeError):
    """Tokens missing or rejected: rerun scripts/garmin_login.py."""


# --- Garmin client -------------------------------------------------------------

def _read_tokens() -> str:
    try:
        raw = _secrets.get_secret_value(SecretId=SECRET_ID).get("SecretString") or ""
    except _secrets.exceptions.ResourceNotFoundException:
        raw = ""
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict) or not (data.get("di_token") or data.get("di_refresh_token")):
        raise GarminNotConnected("No Garmin tokens stored; run scripts/garmin_login.py")
    return raw


def _client():
    # Imported lazily so the rest of the API still starts if the dependency breaks.
    from garminconnect import Garmin, GarminConnectAuthenticationError

    tokens = _read_tokens()
    garmin = Garmin()
    try:
        garmin.login(tokenstore=tokens)
    except GarminConnectAuthenticationError as exc:
        raise GarminNotConnected(f"Garmin rejected the stored tokens: {exc}") from exc
    return garmin, tokens


def _save_tokens_if_changed(garmin: Any, previous: str) -> None:
    current = garmin.client.dumps()
    if json.loads(current) != json.loads(previous):
        _secrets.put_secret_value(SecretId=SECRET_ID, SecretString=current)


# --- Route helpers ----------------------------------------------------------------

def decode_polyline(encoded: str) -> List[Tuple[float, float]]:
    """Decode a Google encoded polyline (precision 5) into (lat, lng) pairs."""
    coords: List[Tuple[float, float]] = []
    index = lat = lng = 0
    length = len(encoded)
    while index < length:
        for is_lng in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lng:
                lng += delta
            else:
                lat += delta
        coords.append((lat / 1e5, lng / 1e5))
    return coords


def _haversine_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _trim_start(points: List[Tuple[float, float]], meters: float) -> List[Tuple[float, float]]:
    travelled = 0.0
    for i in range(1, len(points)):
        travelled += _haversine_m(points[i - 1], points[i])
        if travelled >= meters:
            return points[i:]
    return []


def trim_route(points: List[Tuple[float, float]], meters: float) -> List[Tuple[float, float]]:
    if meters <= 0 or len(points) < 2:
        return points
    trimmed = _trim_start(points, meters)
    return _trim_start(trimmed[::-1], meters)[::-1]


def _downsample(points: List[Tuple[float, float]], limit: int) -> List[Tuple[float, float]]:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


def _extract_route(details: Dict[str, Any]) -> List[Tuple[float, float]]:
    """Pull (lat, lng) points out of an activity-details or polyline response."""
    geo = (details or {}).get("geoPolylineDTO") or {}
    points = [
        (float(p["lat"]), float(p["lon"]))
        for p in geo.get("polyline") or []
        if p.get("lat") is not None and p.get("lon") is not None
    ]
    if points:
        return points
    encoded = (details or {}).get("gPolyline") or (details or {}).get("polyline")
    return decode_polyline(encoded) if isinstance(encoded, str) and encoded else []


def _series(details: Dict[str, Any]) -> Dict[str, List[Optional[float]]]:
    """
    Distance-indexed elevation / heart-rate / speed streams for the detail charts.
    Lat/lng are deliberately not included.
    """
    index = {m.get("key"): m.get("metricsIndex") for m in (details or {}).get("metricDescriptors") or []}
    wanted = {
        "distance_m": "sumDistance",
        "elevation_m": "directElevation",
        "heart_rate": "directHeartRate",
        "speed_mps": "directSpeed",
    }
    if index.get("sumDistance") is None:
        return {}

    rows = []
    for row in (details or {}).get("activityDetailMetrics") or []:
        metrics = row.get("metrics") or []

        def pick(key: str) -> Optional[float]:
            i = index.get(key)
            if i is None or i >= len(metrics) or metrics[i] is None:
                return None
            return float(metrics[i])

        values = {name: pick(key) for name, key in wanted.items()}
        if values["distance_m"] is not None:
            rows.append(values)
    rows.sort(key=lambda r: r["distance_m"])

    # GPS speed is jumpy; a centred moving average reads as pace, not noise.
    speeds = [r["speed_mps"] for r in rows]
    for i, r in enumerate(rows):
        window = [v for v in speeds[max(0, i - 3): i + 4] if v]
        r["speed_mps"] = sum(window) / len(window) if window else None

    rows = _downsample(rows, MAX_SERIES_POINTS)
    return {
        name: [None if r[name] is None else round(r[name], 2) for r in rows]
        for name in wanted
    }


def _splits(garmin: Any, activity_id: int) -> List[Dict[str, Any]]:
    try:
        laps = (garmin.get_activity_splits(activity_id) or {}).get("lapDTOs") or []
    except Exception:
        logger.info("No splits for %s", activity_id)
        return []
    return [
        {
            "distance_m": round(float(lap.get("distance") or 0), 1),
            "moving_time_s": round(float(lap.get("movingDuration") or lap.get("duration") or 0), 1),
            "avg_hr": lap.get("averageHR"),
            "elevation_gain_m": lap.get("elevationGain"),
            "elevation_loss_m": lap.get("elevationLoss"),
        }
        for lap in laps
        if (lap.get("distance") or 0) > 50
    ]


def _num(value: Any, digits: int = 1) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def _summary_details(activity: Dict[str, Any]) -> Dict[str, Any]:
    zones = [activity.get(f"hrTimeInZone_{z}") for z in range(1, 6)]
    return {
        "location_name": activity.get("locationName"),
        "avg_hr": _num(activity.get("averageHR"), 0),
        "max_hr": _num(activity.get("maxHR"), 0),
        "calories": _num(activity.get("calories"), 0),
        "avg_cadence_spm": _num(activity.get("averageRunningCadenceInStepsPerMinute"), 0),
        "avg_stride_cm": _num(activity.get("avgStrideLength")),
        "avg_power_w": _num(activity.get("avgPower"), 0),
        "max_speed_mps": _num(activity.get("maxSpeed"), 2),
        "elevation_loss_m": _num(activity.get("elevationLoss")),
        "vo2max": _num(activity.get("vO2MaxValue"), 0),
        "aerobic_training_effect": _num(activity.get("aerobicTrainingEffect")),
        "training_effect_label": activity.get("trainingEffectLabel"),
        "training_load": _num(activity.get("activityTrainingLoad"), 0),
        "fastest_mile_s": _num(activity.get("fastestSplit_1609"), 0),
        "steps": activity.get("steps"),
        "hr_zones_s": [_num(z, 0) or 0 for z in zones] if any(zones) else [],
    }


# --- Activities -------------------------------------------------------------------

def _local_iso(value: Optional[str]) -> Optional[str]:
    """'2026-11-21 08:02:00' (watch-local wall time) -> '2026-11-21T08:02:00Z'.

    The Z keeps the wall-clock date stable when the frontend formats it in UTC.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return value


def _to_run(garmin: Any, activity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    type_key = (activity.get("activityType") or {}).get("typeKey")
    if type_key not in RUN_TYPES or activity.get("hasPolyline") is False:
        return None

    activity_id = activity["activityId"]
    details = garmin.get_activity_details(str(activity_id), maxchart=1000, maxpoly=2000)
    points = _extract_route(details)
    if not points:
        try:
            points = _extract_route(garmin.get_activity_polyline(activity_id))
        except Exception:
            logger.info("No polyline endpoint data for %s", activity_id)
    route = _downsample(trim_route(points, PRIVACY_TRIM_METERS), MAX_ROUTE_POINTS)
    if len(route) < 2:
        return None

    moving = activity.get("movingDuration") or activity.get("duration") or 0
    return {
        "id": int(activity["activityId"]),
        "name": activity.get("activityName") or "Run",
        "sport_type": type_key,
        "start_date_local": _local_iso(activity.get("startTimeLocal")),
        "distance_m": float(activity.get("distance") or 0),
        "moving_time_s": int(round(moving)),
        "elapsed_time_s": int(round(activity.get("elapsedDuration") or activity.get("duration") or moving)),
        "elevation_gain_m": float(activity.get("elevationGain") or 0),
        "route": [[round(lat, 5), round(lng, 5)] for lat, lng in route],
        "details": {
            **_summary_details(activity),
            "splits": _splits(garmin, activity_id),
            "series": _series(details),
        },
    }


def _fetch_runs_from_garmin(limit: int = MAX_CACHED_RUNS) -> List[Dict[str, Any]]:
    with _refresh_lock:
        garmin, tokens = _client()
        try:
            runs: List[Dict[str, Any]] = []
            for activity in garmin.get_activities(0, ACTIVITY_PAGE_SIZE) or []:
                run = _to_run(garmin, activity)
                if run:
                    runs.append(run)
                if len(runs) >= limit:
                    break
            return runs
        finally:
            _save_tokens_if_changed(garmin, tokens)


def _read_cache() -> Optional[Dict[str, Any]]:
    item = _table.get_item(Key={"source": CACHE_KEY}).get("Item")
    if not item or "runs" not in item:
        return None
    return {"runs": json.loads(item["runs"]), "fetched_at": int(item.get("fetched_at", 0))}


def _write_cache(runs: List[Dict[str, Any]]) -> None:
    _table.put_item(Item={"source": CACHE_KEY, "runs": json.dumps(runs), "fetched_at": int(time.time())})


def get_recent_runs(limit: int) -> Tuple[List[Dict[str, Any]], str]:
    """
    Return (runs, cache_state). Serves the DynamoDB cache while fresh; when it is
    stale, refreshes from Garmin, falling back to the stale copy on failure.
    """
    cached = _read_cache()
    if cached and time.time() - cached["fetched_at"] < CACHE_TTL_SECONDS:
        return cached["runs"][:limit], "fresh"

    try:
        runs = _fetch_runs_from_garmin()
    except Exception:
        if cached:
            logger.exception("Garmin refresh failed; serving stale cache")
            return cached["runs"][:limit], "stale"
        raise

    _write_cache(runs)
    return runs[:limit], "refreshed"
