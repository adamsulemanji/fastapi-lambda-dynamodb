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

# Read the table name from the environment variable
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
        # Automatically encode datetime fields as ISO strings when returning JSON
        json_encoders = { datetime: lambda dt: dt.isoformat() }


@app.get("/items/{item_id}")
def get_item(item_id: str):
    response = table.get_item(Key={"mealID": item_id})
    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.post("/items")
def create_item(item: MealInfo):
    # If mealID is not provided, generate a UUID
    if not item.mealID:
        item.mealID = str(uuid.uuid4())

    # Convert date to ISO string for DynamoDB
    item_data = item.dict()
    item_data["date"] = item_data["date"].isoformat()

    # Put the item into DynamoDB (this will include "mealID" as the partition key)
    table.put_item(Item=item_data)

    return {"success": True, "item": item}


@app.put("/items/{item_id}")
def update_item(item_id: str, item: MealInfo):
    # Check if item already exists
    response = table.get_item(Key={"mealID": item_id})
    if "Item" not in response:
        raise HTTPException(status_code=404, detail="Item not found")

    # Convert date to ISO string
    updated_date = item.date.isoformat()

    # Update the item in DynamoDB
    table.update_item(
        Key={"mealID": item_id},
        UpdateExpression=(
            "SET mealName = :mealName, mealType = :mealType, "
            "eatingOut = :eatingOut, #d = :date, note = :note"
        ),
        ExpressionAttributeNames={"#d": "date"},  # 'date' can be reserved
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


@app.delete("/items/{item_id}")
def delete_item(item_id: str):
    # Check if item exists
    response = table.get_item(Key={"mealID": item_id})
    if "Item" not in response:
        raise HTTPException(status_code=404, detail="Item not found")

    # Delete the item
    table.delete_item(Key={"mealID": item_id})
    return {"success": True, "message": f"Item with ID '{item_id}' deleted"}


@app.get("/items", response_model=List[MealInfo])
def get_all_items():
    # Scan all items (can be slow for large tables)
    response = table.scan()
    items = response.get("Items", [])

    # Convert each DynamoDB item (string date) into a MealInfo object
    meal_infos = []
    for i in items:
        # i["date"] is stored as an ISO string; parse it back to datetime
        i["date"] = datetime.fromisoformat(i["date"])
        meal_infos.append(MealInfo(**i))

    return meal_infos


@app.delete("/items")
def delete_all_items():
    response = table.scan()
    items = response.get("Items", [])
    if not items:
        return {"success": True, "message": "No items to delete"}

    # Delete each item
    for it in items:
        table.delete_item(Key={"mealID": it["mealID"]})
    return {"success": True, "message": "All items deleted"}
