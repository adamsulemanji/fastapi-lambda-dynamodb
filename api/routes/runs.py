import logging
from typing import List

from fastapi import APIRouter, HTTPException, Query, Response

from schemas.runs import Run
from services.runs import GarminNotConnected, get_recent_runs

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=List[Run])
@router.get("/", response_model=List[Run], include_in_schema=False)
def recent_runs(
    response: Response,
    limit: int = Query(3, ge=1, le=10, description="Number of most recent runs to return"),
):
    """
    Most recent outdoor runs with a GPS route, newest first, from Garmin Connect.
    Served from a DynamoDB cache that refreshes every 30 minutes.
    """
    try:
        runs, cache_state = get_recent_runs(limit)
    except GarminNotConnected as exc:
        logger.warning("Garmin not connected: %s", exc)
        raise HTTPException(status_code=503, detail="Run tracking is not connected")
    except Exception:
        logger.exception("Failed to load runs")
        raise HTTPException(status_code=502, detail="Unable to load runs")

    response.headers["X-Runs-Cache-State"] = cache_state
    response.headers["Cache-Control"] = "public, max-age=300"
    return runs
