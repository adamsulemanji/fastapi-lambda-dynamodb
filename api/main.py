from fastapi import FastAPI
from mangum import Mangum
from fastapi.middleware.cors import CORSMiddleware
from routes import meals, movies, base
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
    "https://www.adamsulemanji.com",
    "https://www.adamsulemanji.com/",
    
    # Your frontend domains
    "https://adamsulemanji.vercel.app",
    "https://adamsulemanji.vercel.app/",
    
    # API domains
    "https://api.fast.adamsulemanji.com",
    "https://api.fast.adamsulemanji.com/",

    # Development
    "http://localhost:8000",
    "http://localhost:8000/",
    "http://localhost:3000",
    "http://localhost:3000/",
    
    # Catch common variations
    "*"  # Allow all origins for debugging - remove in production
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Another test log
logger.info("Configuring routers")

app.include_router(meals.router, prefix="/meals", tags=["Meals"])
app.include_router(movies.router, prefix="/movies", tags=["Movies"])
app.include_router(base.router, tags=["Base"])


def include_optional_routers() -> None:
    """
    Keep core APIs (meals/movies/base) available even if optional auth stack
    dependencies are not configured in local development.
    """
    try:
        from routes import auth

        app.include_router(auth.router, prefix="/auth", tags=["Auth"])
    except Exception as exc:
        logger.warning("Auth router disabled: %s", exc)

    try:
        from routes import protected

        # The protected router already has prefix=/protected.
        app.include_router(protected.router)
    except Exception as exc:
        logger.warning("Protected router disabled: %s", exc)


include_optional_routers()

logger.info("Application setup complete")

handler = Mangum(app)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
