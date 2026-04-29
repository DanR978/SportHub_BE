from sqlalchemy import Column, DateTime, ForeignKey, Uuid
from sqlalchemy.sql import func
from database import Base


class DBBlock(Base):
    __tablename__ = "blocks"

    blocker_id = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), primary_key=True)
    blocked_id = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())