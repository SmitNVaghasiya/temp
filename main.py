# main.py
import os
from fastapi import FastAPI, Request
from dotenv import load_dotenv
import uvicorn
from api.routes import auth, predictions, history
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from keep_alive import start_keep_alive

# Load environment variables
load_dotenv()

# FastAPI app
app = FastAPI(
    title="Jewelify API",
    description="API for Jewelify application",
    version="1.0.0",
)

templates = Jinja2Templates(directory="templates")

# Include routers
app.include_router(auth.router)
app.include_router(predictions.router)
app.include_router(history.router)

# Serve the index.html page at the root URL
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

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