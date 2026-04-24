from sqlalchemy import Column, DateTime, ForeignKey, Uuid
from sqlalchemy.sql import func
from database import Base


class DBCommentLike(Base):
    """Comments only support likes (kept simple vs. posts)."""
    __tablename__ = "comment_likes"

    comment_id = Column(Uuid(as_uuid=True), ForeignKey("post_comments.comment_id", ondelete="CASCADE"), primary_key=True)
    user_id    = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
