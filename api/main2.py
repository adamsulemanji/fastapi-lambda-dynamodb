# app/main.py
from fastapi import FastAPI
from mangum import Mangum
from app.routes import meals, movies

app = FastAPI(title="Meals & Movies API")
handler = Mangum(app)

app.include_router(meals.router, prefix="/meals", tags=["Meals"])
app.include_router(movies.router, prefix="/movies", tags=["Movies"])

