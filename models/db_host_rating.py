from sqlalchemy import Column, Integer, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.sql import func
import uuid
from database import Base

class DBHostRating(Base):
    __tablename__ = "host_ratings"

    rating_id  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id   = Column(UUID(as_uuid=True), ForeignKey('events.event_id'), nullable=False)
    rater_id   = Column(UUID(as_uuid=True), ForeignKey('users.user_id'), nullable=False)
    host_id    = Column(UUID(as_uuid=True), ForeignKey('users.user_id'), nullable=False)
    rating     = Column(Integer, nullable=False)
    comment    = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())