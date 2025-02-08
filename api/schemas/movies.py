from pydantic import BaseModel
from typing import Optional

class MovieResult(BaseModel):
    title: str
    letterboxd_url: str
    poster_url: Optional[str] = None