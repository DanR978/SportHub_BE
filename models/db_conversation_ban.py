from sqlalchemy import Column, DateTime, ForeignKey, Uuid
from sqlalchemy.sql import func
from database import Base


class DBConversationBan(Base):
    """A user banned from a specific conversation. Banned users cannot be
    re-added by anyone (including admins must lift the ban first).
    """
    __tablename__ = "conversation_bans"

    conversation_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id    = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), primary_key=True)
    banned_by  = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    banned_at  = Column(DateTime(timezone=True), server_default=func.now())
