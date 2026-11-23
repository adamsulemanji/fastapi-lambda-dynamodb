from typing import List, Optional

from pydantic import BaseModel


class RunSplit(BaseModel):
    distance_m: float
    moving_time_s: float
    avg_hr: Optional[float] = None
    elevation_gain_m: Optional[float] = None
    elevation_loss_m: Optional[float] = None


class RunSeries(BaseModel):
    """Parallel arrays indexed by distance; values may be null where the watch had no reading."""

    distance_m: List[Optional[float]] = []
    elevation_m: List[Optional[float]] = []
    heart_rate: List[Optional[float]] = []
    speed_mps: List[Optional[float]] = []


class RunDetails(BaseModel):
    location_name: Optional[str] = None
    avg_hr: Optional[float] = None
    max_hr: Optional[float] = None
    calories: Optional[float] = None
    avg_cadence_spm: Optional[float] = None
    avg_stride_cm: Optional[float] = None
    avg_power_w: Optional[float] = None
    max_speed_mps: Optional[float] = None
    elevation_loss_m: Optional[float] = None
    vo2max: Optional[float] = None
    aerobic_training_effect: Optional[float] = None
    training_effect_label: Optional[str] = None
    training_load: Optional[float] = None
    fastest_mile_s: Optional[float] = None
    steps: Optional[int] = None
    hr_zones_s: List[float] = []
    splits: List[RunSplit] = []
    series: RunSeries = RunSeries()


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
    details: Optional[RunDetails] = None
