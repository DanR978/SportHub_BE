from uuid import UUID, uuid4
from pydantic import BaseModel, Field
from datetime import date, time

class EventBase(BaseModel):
    title: str
    sport: str
    start_date: date
    start_time: time
    location: str
    experience_level: str

class EventCreate(EventBase):
    pass

class Event(EventBase):
    id: UUID = Field(default_factory=uuid4)