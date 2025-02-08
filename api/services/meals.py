import uuid
import boto3
from datetime import datetime
from typing import List, Optional
from app.schemas.meals import MealInfo
from fastapi import HTTPException

table_name = os.environ.get("TABLE_NAME", "MyTable")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(table_name)

def get_meal(item_id: str) -> Optional[MealInfo]:
    response = table.get_item(Key={"mealID": item_id})
    item = response.get("Item")
    return item

def create_meal(item: MealInfo):
    if not item.mealID:
        item.mealID = str(uuid.uuid4())
    item_data = item.dict()
    item_data["date"] = item_data["date"].isoformat()
    table.put_item(Item=item_data)
    return {"success": True, "item": item}

def update_meal(item_id: str, item: MealInfo):
    response = table.get_item(Key={"mealID": item_id})
    if "Item" not in response:
        raise HTTPException(status_code=404, detail="Item not found")
    item_data = item.dict()
    item_data["date"] = item_data["date"].isoformat()
    table.update_item(
        Key={"mealID": item_id},
        UpdateExpression="SET mealName = :mealName, mealType = :mealType, eatingOut = :eatingOut, date = :date, note = :note",
        ExpressionAttributeValues={
            ":mealName": item_data["mealName"],
            ":mealType": item_data["mealType"],
            ":eatingOut": item_data["eatingOut"],
            ":date": item_data["date"],
            ":note": item_data["note"]
        },
        ReturnValues="UPDATED_NEW"
    )
    return {"success": True, "item": item}

def delete_meal(item_id: str):
    table.delete_item(Key={"mealID": item_id})
    return {"success": True}

def get_meals() -> List[MealInfo]:
    response = table.scan()
    items = response.get("Items", [])
    meal_infos = []
    for i in items:
        i["date"] = datetime.fromisoformat(i["date"])
        meal_infos.append(MealInfo(**i))
    return meal_infos

def delete_all_meals():
    response = table.scan()
    items = response.get("Items", [])
    if not items:
        return {"success": True, "message": "No items to delete"}
    for it in items:
        table.delete_item(Key={"mealID": it["mealID"]})
    return {"success": True, "message": "All items deleted"}


