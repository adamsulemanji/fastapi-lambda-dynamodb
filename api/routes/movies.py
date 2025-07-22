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


@router.get("/search", response_model=List[MovieResult])
def search_movies(
    username: str = Query(..., description="Letterboxd username to fetch movies for"),
    limit: int = Query(10, description="Limit number of movies returned. Use 0 for all movies."),
    fast: bool = Query(False, description="Return cached data only, don't scrape new movies")
):
    """
    Get movies for a user.
    
    - username: Letterboxd username (required)
    - limit: Maximum number of movies to return
      - limit=0: Return all movies
      - limit=n: Return at most n most recent movies
      - If limit > available movies, returns all available movies  
    - fast: Return cached data only (faster, no new scraping)
    """
    # Create search object
    search = MoviesSearch(username=username, fast_mode=fast)
    
    # Get all movies
    movies = get_movies(search)
    
    # Handle limit parameter
    if limit == 0 or limit >= len(movies):
        return movies
    else:
        return movies[:limit]


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



