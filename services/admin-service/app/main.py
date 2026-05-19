from fastapi import FastAPI
from app.api.routes.admin import router as admin_router

app = FastAPI(
    title="Admin Service",
    description="Netflix-like platform admin and moderation service",
    version="1.0.0"
)

app.include_router(admin_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "admin-service"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006)
