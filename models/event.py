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
    description: str | None = None
    max_players: int = 10
    cost: float = 0.00
    end_time: time | None = None

class EventCreate(EventBase):
    pass

class Event(EventBase):
    event_id: UUID = Field(default_factory=uuid4)
    latitude: float | None = None
    longitude: float | None = None
    organizer_id: UUID | None = None
    status: str = 'active'
    participant_count: int = 0
    joined: bool = False
    organizer_name: str | None = None
    organizer_avatar: str | None = None
    organizer_photo: str | None = None
    host_rating:   float | None = None
    total_ratings: int = 0

    class Config:
        from_attributes = True