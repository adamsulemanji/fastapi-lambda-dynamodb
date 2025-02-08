from fastapi import APIRouter, HTTPException
from typing import List
from app.schemas.meals import MealInfo
from app.services.meals import (
    get_meal,
    create_meal,
    update_meal,
    delete_meal,
    get_meals,
    delete_all_meals
)

router = APIRouter()

@router.get("/", response_model=List[MealInfo])
def get_all_items():
    return get_meals()

@router.get("/{mealID}")
def get_item(mealID: str):
    item = get_meal(mealID)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@router.post("/")
def create_item(item: MealInfo):
    return create_meal(item)

@router.put("/{mealID}")
def update_item(mealID: str, item: MealInfo):
    return update_meal(mealID, item)

@router.delete("/{mealID}")
def delete_item(mealID: str):
    return delete_meal(mealID)

@router.delete("/")
def delete_all_items():
    return delete_all_meals()
