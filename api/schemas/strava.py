from typing import List

from pydantic import BaseModel


class StravaRun(BaseModel):
    id: int
    name: str
    sport_type: str
    start_date: str
    start_date_local: str
    distance_m: float
    moving_time_s: int
    elapsed_time_s: int
    elevation_gain_m: float
    # [[lat, lng], ...] with the first/last few hundred metres trimmed for privacy.
    route: List[List[float]]
    strava_url: str
