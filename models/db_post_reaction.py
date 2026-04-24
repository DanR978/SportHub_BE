from sqlalchemy import Column, DateTime, String, ForeignKey, Uuid, UniqueConstraint
from sqlalchemy.sql import func
from database import Base


class DBPostReaction(Base):
    """
    A user's reaction to a post. `kind` is 'like' or 'downvote'. One row per
    (user, post) — switching reactions flips this row in place.
    """
    __tablename__ = "post_reactions"

    post_id    = Column(Uuid(as_uuid=True), ForeignKey("posts.post_id", ondelete="CASCADE"), primary_key=True)
    user_id    = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), primary_key=True)
    kind       = Column(String(16), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
