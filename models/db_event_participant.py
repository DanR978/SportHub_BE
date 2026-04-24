from sqlalchemy import Column, DateTime, ForeignKey, Uuid
from sqlalchemy.sql import func
from database import Base


class DBEventParticipant(Base):
    __tablename__ = "event_participants"

    event_id = Column(Uuid(as_uuid=True), ForeignKey("events.event_id"), primary_key=True)
    user_id = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), primary_key=True)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
