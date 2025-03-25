import os
from fastapi import FastAPI, Request
from dotenv import load_dotenv
import uvicorn
from api.routes import auth, predictions, history
from keep_alive import start_keep_alive
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

# FastAPI app
app = FastAPI(
    title="Jewelify API",
    description="API for Jewelify application",
    version="1.0.0",
)

# Set the limiter on the app
app.state.limiter = limiter
# Removed: app.add_middleware(limiter.middleware)  # This was incorrect

# Middleware to log all requests
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"Incoming request: {request.method} {request.url}")
    response = await call_next(request)
    logger.info(f"Response status: {response.status_code}")
    return response

# Include routers
app.include_router(auth.router)
app.include_router(predictions.router)
app.include_router(history.router)

# Health check endpoint for keep-alive
@app.get('/health')
async def health_check():
    return {"status": "healthy"}

# Start keep-alive task on startup
@app.on_event("startup")
async def startup_event():
    start_keep_alive(app)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run(app, host="0.0.0.0", port=port)