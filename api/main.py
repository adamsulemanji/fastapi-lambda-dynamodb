from fastapi import FastAPI
from mangum import Mangum
from routes import meals, movies

app = FastAPI(title="Meals & Movies API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(meals.router, prefix="/meals", tags=["Meals"])
app.include_router(movies.router, prefix="/movies", tags=["Movies"])

handler = Mangum(app)