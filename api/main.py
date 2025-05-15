from fastapi import FastAPI
from mangum import Mangum
from fastapi.middleware.cors import CORSMiddleware
from routes import meals, movies, base, auth, protected
import uvicorn
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

# Test log to verify logging is working
logger.info("Starting FastAPI application")

app = FastAPI(title="Meals, Movies, Cognito API")

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

# Another test log
logger.info("Configuring routers")

app.include_router(protected.router, prefix="/protected", tags=["Protected"])
app.include_router(auth.router, prefix="/auth", tags=["Auth"])
app.include_router(meals.router, prefix="/meals", tags=["Meals"])
app.include_router(movies.router, prefix="/movies", tags=["Movies"])
app.include_router(base.router, tags=["Base"])

logger.info("Application setup complete")

handler = Mangum(app)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, debug=True)
