from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.events import router as events_router
from routers.users import router as users_router
from database import engine, Base

app = FastAPI(title="Game Radar API", version="1.0.0")

# CORS — allow all origins in dev, lock down for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(events_router)
app.include_router(users_router)


@app.get("/")
def read_root():
    return {"message": "SportMap API is running"}


# Create all tables on startup
Base.metadata.create_all(bind=engine)
