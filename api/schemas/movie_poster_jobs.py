from typing import Literal, Optional
from pydantic import BaseModel, Field


PosterJobStatus = Literal["QUEUED", "IN_PROGRESS", "SUCCEEDED", "FAILED"]


class MoviePosterJobRequest(BaseModel):
    username: str = Field(..., description="Letterboxd username")
    movie_count: int = Field(
        0,
        ge=0,
        le=5000,
        description="0 means all logged movies, otherwise use this many movies",
    )


class MoviePosterJobResponse(BaseModel):
    job_id: str
    username: str
    status: PosterJobStatus
    movie_count: int = 0
    existing_job: bool = False
    created_at: int
    updated_at: int
    started_at: Optional[int] = None
    completed_at: Optional[int] = None
    processed_items: Optional[int] = None
    total_items: Optional[int] = None
    blank_tiles: Optional[int] = None
    grid_columns: Optional[int] = None
    grid_rows: Optional[int] = None
    result_count: Optional[int] = None
    output_key: Optional[str] = None
    output_url: Optional[str] = None
    progress_phase: Optional[str] = None
    progress_message: Optional[str] = None
    error_message: Optional[str] = None
