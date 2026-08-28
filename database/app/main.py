from fastapi import FastAPI
from app.api.routes import patients, queue

app = FastAPI(
    title="SIH Healthcare Platform API",
    version="1.0.0"
)

# Register routes
app.include_router(patients.router)
app.include_router(queue.router)

@app.get("/")
async def health_check():
    return {"status": "online", "message": "SIH Backend is running"}