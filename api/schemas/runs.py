from typing import List, Optional

from pydantic import BaseModel


class Run(BaseModel):
    id: int
    name: str
    sport_type: str
    start_date_local: Optional[str] = None
    distance_m: float
    moving_time_s: int
    elapsed_time_s: int
    elevation_gain_m: float
    # [[lat, lng], ...] with the first/last few hundred metres trimmed for privacy.
    route: List[List[float]]
