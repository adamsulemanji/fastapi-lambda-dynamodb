import os
import boto3
from fastapi import FastAPI
from mangum import Mangum

app = FastAPI()
handler = Mangum(app)

# Read the table name from environment variable
table_name = os.environ.get("TABLE_NAME", "MyTable")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(table_name)

@app.get("/items/{item_id}")
def get_item(item_id: str):
    response = table.get_item(Key={"pk": item_id})
    return response.get("Item", {})

@app.post("/items")
def put_item(item: dict):
    table.put_item(Item=item)
    return {"success": True, "item": item}


@app.post("/test-post")
def test_post(int: int):
    return {"success": True, "message": "Test Post", "int": int}

@app.get("/test-get")
def test_get():
    return {"success": True, "message": "Test Get"}
