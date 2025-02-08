from pydantic import BaseModel
from typing import Optional, List


class MoviesSearch(BaseModel):
    username: str

class MovieResult(BaseModel):
    title: str
    letterboxd_url: str
    poster_url: Optional[str] = None


class MoviesResult(BaseModel):
    movies: List[MovieResult]


