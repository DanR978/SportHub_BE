from sqlalchemy import Column, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.sql import func
import uuid
from database import Base


class DBReport(Base):
    __tablename__ = "reports"

    report_id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reporter_id = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    # What's being reported: 'event' or 'user'
    target_type = Column(String(20), nullable=False)
    target_id = Column(Uuid(as_uuid=True), nullable=False)
    reason = Column(String(50), nullable=False)  # 'spam','harassment','inappropriate','safety','other'
    details = Column(Text, nullable=True)
    status = Column(String(20), default="pending", index=True)  # 'pending','reviewed','dismissed','actioned'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    review_notes = Column(Text, nullable=True)
