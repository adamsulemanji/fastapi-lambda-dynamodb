from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class MealInfo(BaseModel):
    mealID: Optional[str] = None
    mealName: str
    mealType: str
    eatingOut: bool
    date: datetime
    note: str

    class Config:
        json_encoders = {datetime: lambda dt: dt.isoformat()}