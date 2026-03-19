from sqlalchemy import Column, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.sql import func
import uuid
from database import Base


class DBReport(Base):
    __tablename__ = "reports"

    report_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reporter_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    # What's being reported: 'event' or 'user'
    target_type = Column(String(20), nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)
    reason = Column(String(50), nullable=False)  # 'spam','harassment','inappropriate','safety','other'
    details = Column(Text, nullable=True)
    status = Column(String(20), default="pending")  # 'pending','reviewed','dismissed','actioned'
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())