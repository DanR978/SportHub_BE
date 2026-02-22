from fastapi import APIRouter
from models.event import Event, EventCreate
from sqlalchemy.orm import Session
from fastapi import Depends
from database import get_db
from models.db_event import DBEvent
from auth import get_current_user
from models.db_user import DBUser

router = APIRouter()

@router.post("/sports-events", response_model=Event)
async def create_event(event: EventCreate, db: Session = Depends(get_db), current_user: DBUser = Depends(get_current_user)):
    new_event = DBEvent(**event.model_dump(exclude={"created_by"}), created_by=str(current_user.user_id))
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    return new_event

@router.get("/sports-events", response_model=list[Event])
async def get_events(db: Session = Depends(get_db)):
    events = db.query(DBEvent).all()
    return events