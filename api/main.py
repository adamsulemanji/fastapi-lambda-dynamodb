from fastapi import FastAPI
from mangum import Mangum
from fastapi.middleware.cors import CORSMiddleware
from routes import meals, movies

app = FastAPI(title="Meals & Movies API")

origins = [
    "https://adamsulemanji.com",
    "https://adamsulemanji.com/",

    "https://api.fast.adamsulemanji.com",
    "https://api.fast.adamsulemanji.com/",

    "http://localhost:8000",
    "http://localhost:8000/",

    "http://localhost:3000",
    "http://localhost:3000/",

    "https://www.adamsulemanji.com",
    "https://www.adamsulemanji.com/",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meals.router, prefix="/meals", tags=["Meals"])
app.include_router(movies.router, prefix="/movies", tags=["Movies"])

handler = Mangum(app)
