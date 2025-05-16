from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from typing import List, Optional, Dict, Any
from schemas.movies import (
    MoviesSearch, 
    MovieResult
)

from services.movies import (
    get_movies,
    backfill_movies
)
    
router = APIRouter()


@router.post("/search", response_model=List[MovieResult])
def search_movies(search: MoviesSearch, limit: Optional[int] = Query(None, description="Limit number of movies returned")):
    """
    Get movies for a user.
    Set limit=0 or omit to get all movies.
    Set limit=n to get the n most recent movies.
    """
    movies = get_movies(search)
    
    # If limit is specified and not 0, return only that many movies
    if limit and limit > 0:
        return movies[:limit]
    
    return movies


@router.post("/backfill", response_model=Dict[str, Any])
def backfill_movies_route(username: str, force: bool = False, background_tasks: BackgroundTasks = None):
    """
    Backfill all movies for a Letterboxd user.
    
    Parameters:
    - username: Letterboxd username to backfill
    - force: If True, delete existing data before backfilling
    - background_tasks: If provided, run backfill in background
    """
    if background_tasks:
        # Run backfill in background
        background_tasks.add_task(backfill_movies, username, force)
        return {
            "success": True,
            "message": f"Backfill started for user {username} in background",
            "count": 0,
            "background": True
        }
    else:
        # Run backfill synchronously
        return backfill_movies(username, force)



