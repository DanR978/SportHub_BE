from sqlalchemy import Column, DateTime, ForeignKey, Uuid
from sqlalchemy.sql import func
from database import Base


class DBEventBan(Base):
    """A user banned from a specific event by the organizer.

    Banned users are kicked from the participant list (if joined) and can't
    rejoin unless the organizer lifts the ban. The /sports-events/{id}/join
    endpoint refuses banned users with 403.
    """
    __tablename__ = "event_bans"

    event_id  = Column(
        Uuid(as_uuid=True),
        ForeignKey("events.event_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id   = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), primary_key=True)
    banned_by = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    banned_at = Column(DateTime(timezone=True), server_default=func.now())
