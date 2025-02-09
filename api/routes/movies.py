from fastapi import APIRouter, HTTPException
from typing import List
from schemas.movies import (
    MoviesSearch, 
    MovieResult
)

from services.movies import (
    get_movies,
)
    
router = APIRouter()


@router.post("/search", response_model=List[MovieResult])
def search_movies(search: MoviesSearch):
    return get_movies(search)



