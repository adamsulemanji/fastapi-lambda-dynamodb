from pydantic import BaseModel
from typing import List, Optional

class MoviesSearch(BaseModel):
    username: str
    fast_mode: bool = False

class MovieResult(BaseModel):
    title: str
    letterboxd_url: str
    poster_url: Optional[str] = None
    rating: Optional[str] = None
    director: List[str] = []
    review: Optional[str] = None
    release_year: Optional[str] = None
    review_date: Optional[str] = None
    review_url: Optional[str] = None

class MoviesResult(BaseModel):
    movies: List[MovieResult]
