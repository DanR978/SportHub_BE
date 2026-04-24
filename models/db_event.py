from sqlalchemy import Column, Date, String, Time, Float, Integer, Numeric, ForeignKey, Uuid
import uuid
from database import Base


class DBEvent(Base):
    __tablename__ = "events"

    event_id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(100), index=True)
    sport = Column(String(100), index=True)
    start_date = Column(Date, index=True)
    start_time = Column(Time)
    end_time = Column(Time, nullable=True)
    location = Column(String(100))
    experience_level = Column(String(50))
    description = Column(String(500), nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    organizer_id = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=True, index=True)
    max_players = Column(Integer, default=10)
    cost = Column(Numeric(6, 2), default=0.00)
    status = Column(String(20), default="active", index=True)
