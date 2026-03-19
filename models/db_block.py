from sqlalchemy import Column, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.sql import func
from database import Base


class DBBlock(Base):
    __tablename__ = "blocks"

    blocker_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), primary_key=True)
    blocked_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), primary_key=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())