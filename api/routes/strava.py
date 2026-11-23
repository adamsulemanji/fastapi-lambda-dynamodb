import logging
import os
from typing import List

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from schemas.strava import StravaRun
from services.strava import (
    StravaNotConnected,
    build_authorize_url,
    complete_authorization,
    get_recent_runs,
)

logger = logging.getLogger(__name__)
router = APIRouter()

REDIRECT_URI = os.environ.get(
    "STRAVA_REDIRECT_URI", "https://api.fast.adamsulemanji.com/strava/callback"
)


@router.get("/runs", response_model=List[StravaRun])
def recent_runs(
    response: Response,
    limit: int = Query(3, ge=1, le=10, description="Number of most recent runs to return"),
):
    """
    Most recent public runs (Run / TrailRun with a GPS route), newest first.
    Served from a DynamoDB cache that refreshes from Strava every 30 minutes.
    """
    try:
        runs, cache_state = get_recent_runs(limit)
    except StravaNotConnected as exc:
        logger.warning("Strava not connected: %s", exc)
        raise HTTPException(status_code=503, detail="Strava is not connected")
    except Exception:
        logger.exception("Failed to load Strava runs")
        raise HTTPException(status_code=502, detail="Unable to load runs from Strava")

    response.headers["X-Strava-Cache-State"] = cache_state
    response.headers["Cache-Control"] = "public, max-age=300"
    return runs


@router.get("/auth", include_in_schema=False)
def start_auth():
    """One-time setup: redirect to Strava's consent screen."""
    try:
        return RedirectResponse(build_authorize_url(REDIRECT_URI))
    except StravaNotConnected as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/callback", include_in_schema=False, response_class=HTMLResponse)
def auth_callback(code: str = "", scope: str = "", error: str = ""):
    if error or not code:
        raise HTTPException(status_code=400, detail=error or "Missing authorization code")
    try:
        complete_authorization(code=code, scope=scope)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except StravaNotConnected as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return "<p>Strava connected. You can close this tab.</p>"
