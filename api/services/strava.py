"""
Strava integration: recent public runs (with route maps) for the personal site.

Only the free Strava API is used (no subscription needed). Auth is OAuth2:
  - client_id / client_secret live in a Secrets Manager secret (JSON).
  - /strava/auth -> Strava consent -> /strava/callback stores the refresh token
    in that same secret. Access tokens are refreshed on demand (they last 6h);
    Strava rotates refresh tokens, so the new one is written back each time.

Runs are cached in DynamoDB so page views never hit Strava directly
(default API limits are 200 requests / 15 min, 2,000 / day).
"""

import json
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import boto3
import requests

logger = logging.getLogger(__name__)

STRAVA_OAUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_URL = "https://www.strava.com/api/v3"
REQUEST_TIMEOUT = 10

SECRET_ID = os.environ.get("STRAVA_SECRET_ID", "strava/credentials")
TABLE_NAME = os.environ.get("STRAVA_TABLE_NAME", "strava")
# Only this athlete may connect; stops anyone else overwriting the stored token.
ATHLETE_ID = os.environ.get("STRAVA_ATHLETE_ID", "109469044")
CACHE_TTL_SECONDS = int(os.environ.get("STRAVA_CACHE_TTL_SECONDS", "1800"))
# Strava returns the *full* polyline to the owner, ignoring privacy zones, so
# trim each end to avoid publishing where runs start/finish (e.g. home).
PRIVACY_TRIM_METERS = float(os.environ.get("STRAVA_PRIVACY_TRIM_METERS", "300"))
MAX_CACHED_RUNS = 10
RUN_SPORT_TYPES = {"Run", "TrailRun"}
REFRESH_MARGIN_SECONDS = 600

_secrets = boto3.client("secretsmanager")
_table = boto3.resource("dynamodb").Table(TABLE_NAME)


class StravaNotConnected(RuntimeError):
    """Credentials or refresh token missing: run the /strava/auth flow."""


# --- Credentials -------------------------------------------------------------

def _read_secret() -> Dict[str, Any]:
    raw = _secrets.get_secret_value(SecretId=SECRET_ID).get("SecretString") or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _write_secret(data: Dict[str, Any]) -> None:
    _secrets.put_secret_value(SecretId=SECRET_ID, SecretString=json.dumps(data))


def _client_credentials(secret: Dict[str, Any]) -> Tuple[str, str]:
    client_id = str(secret.get("client_id") or "")
    client_secret = str(secret.get("client_secret") or "")
    if not client_id or not client_secret:
        raise StravaNotConnected("Strava client_id/client_secret not set in secret")
    return client_id, client_secret


def _store_tokens(secret: Dict[str, Any], token_response: Dict[str, Any]) -> Dict[str, Any]:
    updated = {
        **secret,
        "access_token": token_response["access_token"],
        "refresh_token": token_response["refresh_token"],
        "expires_at": int(token_response["expires_at"]),
    }
    _write_secret(updated)
    return updated


def _get_access_token() -> str:
    secret = _read_secret()
    if secret.get("access_token") and int(secret.get("expires_at", 0)) > time.time() + REFRESH_MARGIN_SECONDS:
        return secret["access_token"]

    client_id, client_secret = _client_credentials(secret)
    refresh_token = secret.get("refresh_token")
    if not refresh_token:
        raise StravaNotConnected("No Strava refresh token stored; visit /strava/auth")

    resp = requests.post(
        STRAVA_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code in (400, 401):
        raise StravaNotConnected(f"Strava token refresh rejected ({resp.status_code}); reconnect via /strava/auth")
    resp.raise_for_status()
    return _store_tokens(secret, resp.json())["access_token"]


def build_authorize_url(redirect_uri: str) -> str:
    client_id, _ = _client_credentials(_read_secret())
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": "read,activity:read",
        }
    )
    return f"{STRAVA_OAUTH_URL}?{query}"


def complete_authorization(code: str, scope: str) -> None:
    """Exchange the OAuth code for tokens, refusing any athlete but ours."""
    if "activity:read" not in (scope or "").split(","):
        raise PermissionError("The activity:read scope is required")

    secret = _read_secret()
    client_id, client_secret = _client_credentials(secret)
    resp = requests.post(
        STRAVA_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        raise PermissionError(f"Strava rejected the authorization code ({resp.status_code})")
    data = resp.json()

    athlete_id = str((data.get("athlete") or {}).get("id", ""))
    if athlete_id != ATHLETE_ID:
        raise PermissionError("This Strava account is not allowed to connect")

    _store_tokens(secret, data)
    # Drop any cached runs so the next request fetches with the new token.
    _table.delete_item(Key={"athlete_id": ATHLETE_ID})


# --- Polyline helpers --------------------------------------------------------

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
    trimmed = _trim_start(trimmed[::-1], meters)[::-1]
    return trimmed


# --- Activities ---------------------------------------------------------------

def _to_run(activity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sport = activity.get("sport_type") or activity.get("type")
    if sport not in RUN_SPORT_TYPES:
        return None
    if activity.get("private") or activity.get("visibility", "everyone") != "everyone":
        return None

    polyline = (activity.get("map") or {}).get("summary_polyline") or ""
    route = trim_route(decode_polyline(polyline), PRIVACY_TRIM_METERS) if polyline else []
    if len(route) < 2:
        return None

    return {
        "id": activity["id"],
        "name": activity.get("name") or "Run",
        "sport_type": sport,
        "start_date": activity.get("start_date"),
        "start_date_local": activity.get("start_date_local"),
        "distance_m": float(activity.get("distance") or 0),
        "moving_time_s": int(activity.get("moving_time") or 0),
        "elapsed_time_s": int(activity.get("elapsed_time") or 0),
        "elevation_gain_m": float(activity.get("total_elevation_gain") or 0),
        "route": [[round(lat, 5), round(lng, 5)] for lat, lng in route],
        "strava_url": f"https://www.strava.com/activities/{activity['id']}",
    }


def _fetch_runs_from_strava() -> List[Dict[str, Any]]:
    resp = requests.get(
        f"{STRAVA_API_URL}/athlete/activities",
        headers={"Authorization": f"Bearer {_get_access_token()}"},
        params={"per_page": 50, "page": 1},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    runs = [run for run in (_to_run(a) for a in resp.json()) if run]
    return runs[:MAX_CACHED_RUNS]


def _read_cache() -> Optional[Dict[str, Any]]:
    item = _table.get_item(Key={"athlete_id": ATHLETE_ID}).get("Item")
    if not item or "runs" not in item:
        return None
    return {"runs": json.loads(item["runs"]), "fetched_at": int(item.get("fetched_at", 0))}


def _write_cache(runs: List[Dict[str, Any]]) -> None:
    _table.put_item(
        Item={"athlete_id": ATHLETE_ID, "runs": json.dumps(runs), "fetched_at": int(time.time())}
    )


def get_recent_runs(limit: int) -> Tuple[List[Dict[str, Any]], str]:
    """
    Return (runs, cache_state). Serves the DynamoDB cache while fresh; when it is
    stale, refreshes from Strava, falling back to the stale copy if Strava fails.
    """
    cached = _read_cache()
    if cached and time.time() - cached["fetched_at"] < CACHE_TTL_SECONDS:
        return cached["runs"][:limit], "fresh"

    try:
        runs = _fetch_runs_from_strava()
    except Exception:
        if cached:
            logger.exception("Strava refresh failed; serving stale cache")
            return cached["runs"][:limit], "stale"
        raise

    _write_cache(runs)
    return runs[:limit], "refreshed"
