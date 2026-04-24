import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from rate_limiter import limiter
from routers.admin import router as admin_router
from routers.events import router as events_router
from routers.legal import router as legal_router
from routers.users import router as users_router

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Create tables at startup (Alembic remains source of truth for migrations).
    Base.metadata.create_all(bind=engine)

    scheduler = None
    if os.getenv("DISABLE_SCHEDULER", "").lower() != "true":
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from jobs import archive_expired_events, send_event_reminders

            scheduler = AsyncIOScheduler()
            scheduler.add_job(archive_expired_events, "interval", minutes=15, id="archive_expired", max_instances=1)
            scheduler.add_job(send_event_reminders, "interval", minutes=5, id="event_reminders", max_instances=1)
            scheduler.start()
            logger.info("Background scheduler started")
        except Exception:
            logger.exception("Scheduler failed to start; continuing without background jobs")
            scheduler = None

    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Game Radar API", version="1.0.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — comma-separated origins via env. Defaults to "*" for local dev.
allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(events_router)
app.include_router(users_router)
app.include_router(legal_router)
app.include_router(admin_router)


@app.get("/")
def read_root():
    return {"message": "Game Radar API is running"}


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unreachable")
