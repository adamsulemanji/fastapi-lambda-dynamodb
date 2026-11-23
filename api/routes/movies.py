from fastapi import APIRouter, HTTPException, Query, Response, status
from typing import List
from schemas.movies import (
    MoviesSearch, 
    MovieResult
)
from schemas.movie_jobs import MoviesJobRequest, MoviesJobResponse
from schemas.movie_poster_jobs import MoviePosterJobRequest, MoviePosterJobResponse

from services.movies import (
    get_movies,
    get_cached_movies_state
)
from services.movie_jobs import create_movies_job, get_movies_job
from services.movie_poster_jobs import create_movie_poster_job, get_movie_poster_job
    
router = APIRouter()


def _apply_limit(movies: list, limit: int) -> list:
    if limit <= 0 or limit >= len(movies):
        return movies
    return movies[:limit]

def _create_job_or_raise(username: str, force: bool) -> dict:
    try:
        return create_movies_job(username=username, force=force)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Unable to create movies scrape job")


def _create_poster_job_or_raise(username: str, movie_count: int) -> dict:
    try:
        return create_movie_poster_job(username=username, movie_count=movie_count)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Unable to create movie poster job")


def _queue_movies_job_with_headers(
    response: Response,
    username: str,
    cache_state: str,
    raise_non_503: bool,
) -> None:
    try:
        job = _create_job_or_raise(username=username, force=False)
        response.headers["X-Movies-Job-Id"] = job["job_id"]
        response.headers["X-Movies-Job-Status"] = job["status"]
    except HTTPException as exc:
        if raise_non_503 and exc.status_code != 503:
            raise
        response.headers["X-Movies-Job-Status"] = "UNAVAILABLE"
    response.headers["X-Movies-Cache-State"] = cache_state


@router.get("/search", response_model=List[MovieResult])
def search_movies(
    response: Response,
    username: str = Query(..., description="Letterboxd username to fetch movies for"),
    limit: int = Query(10, description="Limit number of movies returned. Use 0 for all movies."),
    fast: bool = Query(False, description="Return cached data only, don't scrape new movies"),
    async_refresh: bool = Query(
        True,
        description="When fast=false, trigger async refresh if cache is stale/missing instead of waiting synchronously.",
    ),
):
    """
    Get movies for a user from cache.
    
    - username: Letterboxd username (required)
    - limit: Maximum number of movies to return
      - limit=0: Return all movies
      - limit=n: Return at most n most recent movies
      - If limit > available movies, returns all available movies
    - fast=true: Return cached data only
    - fast=false, async_refresh=true: Return cache immediately and queue refresh when stale/missing
    - fast=false, async_refresh=false: Execute synchronous refresh (may be slow)
    """
    if not fast and not async_refresh:
        movies = get_movies(MoviesSearch(username=username, fast_mode=False))
        return _apply_limit(movies, limit)

    cache_state = get_cached_movies_state(username)
    # Apply the limit before validation so we only build the models we return.
    cached_movies = [MovieResult(**movie) for movie in _apply_limit(cache_state.get("movies", []), limit)]
    cache_exists = bool(cache_state.get("exists", False))
    cache_is_stale = bool(cache_state.get("is_stale", True))

    if fast:
        if not cache_exists:
            _queue_movies_job_with_headers(
                response=response,
                username=username,
                cache_state="stale_or_missing",
                raise_non_503=False,
            )
        elif cache_is_stale:
            response.headers["X-Movies-Cache-State"] = "stale_or_missing"
        else:
            response.headers["X-Movies-Cache-State"] = "fresh"
        return cached_movies

    if not cache_exists:
        _queue_movies_job_with_headers(
            response=response,
            username=username,
            cache_state="stale_or_missing",
            raise_non_503=False,
        )
    elif cache_is_stale:
        _queue_movies_job_with_headers(
            response=response,
            username=username,
            cache_state="stale_or_missing",
            raise_non_503=True,
        )
    else:
        response.headers["X-Movies-Cache-State"] = "fresh"

    return cached_movies


@router.post("/jobs", response_model=MoviesJobResponse, status_code=status.HTTP_202_ACCEPTED)
def create_movies_job_route(job: MoviesJobRequest):
    """
    Create an async movies scrape job.
    """
    return _create_job_or_raise(username=job.username, force=job.force)


@router.get("/jobs/{job_id}", response_model=MoviesJobResponse)
def get_movies_job_route(job_id: str):
    """
    Get status for a movies scrape job.
    """
    job = get_movies_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@router.post("/backfill", response_model=MoviesJobResponse, status_code=status.HTTP_202_ACCEPTED)
def backfill_movies_route(
    username: str = Query(..., description="Letterboxd username to backfill"),
    force: bool = Query(False, description="If true, clear cached record before full refresh"),
):
    """
    Backward-compatible alias for creating an async scrape job.
    """
    return _create_job_or_raise(username=username, force=force)


@router.post("/poster-jobs", response_model=MoviePosterJobResponse, status_code=status.HTTP_202_ACCEPTED)
def create_movie_poster_job_route(response: Response, job: MoviePosterJobRequest):
    """
    Create an async movie poster generation job.
    """
    cache_state = get_cached_movies_state(job.username, include_movies=False)
    if not cache_state.get("exists", False):
        _queue_movies_job_with_headers(
            response=response,
            username=job.username,
            cache_state="stale_or_missing",
            raise_non_503=False,
        )
    return _create_poster_job_or_raise(username=job.username, movie_count=job.movie_count)


@router.get("/poster-jobs/{job_id}", response_model=MoviePosterJobResponse)
def get_movie_poster_job_route(job_id: str):
    """
    Get status for a movie poster generation job.
    """
    job = get_movie_poster_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Poster job {job_id} not found")
    return job
