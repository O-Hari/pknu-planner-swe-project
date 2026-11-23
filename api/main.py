from fastapi import FastAPI

from api.catalog import router as catalog_router
from api.planner import calendar_router, timetable_router
from api.supabase import lifespan

app = FastAPI(title="PKNU Planner API", version="1.0.0", lifespan=lifespan)
app.include_router(catalog_router, prefix="/api/v1")
app.include_router(timetable_router, prefix="/api/v1")
app.include_router(calendar_router, prefix="/api/v1")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}
