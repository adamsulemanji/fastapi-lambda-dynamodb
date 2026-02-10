from typing import Literal, Optional
from pydantic import BaseModel, Field


JobStatus = Literal["PENDING", "IN_PROGRESS", "SUCCEEDED", "FAILED"]


class MoviesJobRequest(BaseModel):
    username: str = Field(..., description="Letterboxd username to scrape")
    force: bool = Field(
        False,
        description="If true, clears cached movies before backfill",
    )


class MoviesJobResponse(BaseModel):
    job_id: str
    username: str
    status: JobStatus
    force: bool = False
    existing_job: bool = False
    created_at: int
    updated_at: int
    started_at: Optional[int] = None
    completed_at: Optional[int] = None
    processed_movies: Optional[int] = None
    total_movies: Optional[int] = None
    result_count: Optional[int] = None
    progress_phase: Optional[str] = None
    progress_message: Optional[str] = None
    error_message: Optional[str] = None
