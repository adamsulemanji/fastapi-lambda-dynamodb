from pydantic import BaseModel
from typing import List, Optional

class MoviesSearch(BaseModel):
    username: str

class MovieResult(BaseModel):
    title: str
    letterboxd_url: str
    poster_url: Optional[str] = None
    rating: Optional[str] = None
    director: List[str] = [] 

class MoviesResult(BaseModel):
    movies: List[MovieResult]
