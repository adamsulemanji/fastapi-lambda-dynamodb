import os
import uuid
import boto3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from mangum import Mangum

app = FastAPI()
handler = Mangum(app)


table_name = os.environ.get("TABLE_NAME", "MyTable")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(table_name)

class MealInfo(BaseModel):
    mealID: Optional[str] = None
    mealName: str
    mealType: str
    eatingOut: bool
    date: datetime
    note: str

    class Config:
        json_encoders = { datetime: lambda dt: dt.isoformat() }


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/meals/{mealID}")
def get_item(item_id: str):
    response = table.get_item(Key={"mealID": item_id})
    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.post("/meals")
def create_item(item: MealInfo):
    
    if not item.mealID:
        item.mealID = str(uuid.uuid4())

    
    item_data = item.dict()
    item_data["date"] = item_data["date"].isoformat()

    
    table.put_item(Item=item_data)

    return {"success": True, "item": item}


@app.put("/meals/{mealID}")
def update_item(item_id: str, item: MealInfo):
    
    response = table.get_item(Key={"mealID": item_id})
    if "Item" not in response:
        raise HTTPException(status_code=404, detail="Item not found")

    
    updated_date = item.date.isoformat()

    
    table.update_item(
        Key={"mealID": item_id},
        UpdateExpression=(
            "SET mealName = :mealName, mealType = :mealType, "
            "eatingOut = :eatingOut, #d = :date, note = :note"
        ),
        ExpressionAttributeNames={"#d": "date"},
        ExpressionAttributeValues={
            ":mealName": item.mealName,
            ":mealType": item.mealType,
            ":eatingOut": item.eatingOut,
            ":date": updated_date,
            ":note": item.note
        },
        ReturnValues="ALL_NEW"
    )

    return {"success": True, "item": item}


@app.delete("/meals/{mealID}")
def delete_item(item_id: str):
    
    response = table.get_item(Key={"mealID": item_id})
    if "Item" not in response:
        raise HTTPException(status_code=404, detail="Item not found")

    
    table.delete_item(Key={"mealID": item_id})
    return {"success": True, "message": f"Item with ID '{mealID}' deleted"}


@app.get("/meals", response_model=List[MealInfo])
def get_all_items():
    
    response = table.scan()
    items = response.get("Items", [])

    
    meal_infos = []
    for i in items:
        
        i["date"] = datetime.fromisoformat(i["date"])
        meal_infos.append(MealInfo(**i))

    return meal_infos


@app.delete("/meals")
def delete_all_items():
    response = table.scan()
    items = response.get("Items", [])
    if not items:
        return {"success": True, "message": "No items to delete"}

    
    for it in items:
        table.delete_item(Key={"mealID": it["mealID"]})
    return {"success": True, "message": "All items deleted"}
