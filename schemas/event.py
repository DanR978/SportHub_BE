from uuid import UUID, uuid4
from pydantic import BaseModel, Field
from datetime import date, time
from typing import Optional

class EventBase(BaseModel):
    title: str
    sport: str
    start_date: date
    start_time: time
    location: str
    experience_level: str
    description: str | None = None
    max_players: int = 10
    cost: float = 0.00
    end_time: time | None = None

class EventCreate(EventBase):
    latitude: float | None = None
    longitude: float | None = None

class EventUpdate(BaseModel):
    title: Optional[str] = None
    sport: Optional[str] = None
    start_date: Optional[date] = None
    start_time: Optional[time] = None
    location: Optional[str] = None
    experience_level: Optional[str] = None
    description: Optional[str] = None
    max_players: Optional[int] = None
    cost: Optional[float] = None
    end_time: Optional[time] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class Event(EventBase):
    event_id: UUID = Field(default_factory=uuid4)
    latitude: float | None = None
    longitude: float | None = None
    organizer_id: UUID | None = None
    status: str = 'active'
    participant_count: int = 0
    joined: bool = False
    is_organizer: bool = False
    organizer_name: str | None = None
    organizer_photo: str | None = None
    host_rating:   float | None = None
    total_ratings: int = 0

    class Config:
        from_attributes = True