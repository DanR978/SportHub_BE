from sqlalchemy import Column, DateTime, Text, Integer, ForeignKey, Uuid
from sqlalchemy.sql import func
import uuid
from database import Base


class DBComment(Base):
    """
    Comment on a post. Supports one level of threading (parent_id).
    Counters (like_count) are denormalized.
    """
    __tablename__ = "post_comments"

    comment_id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id    = Column(Uuid(as_uuid=True), ForeignKey("posts.post_id", ondelete="CASCADE"), nullable=False, index=True)
    author_id  = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)
    parent_id  = Column(Uuid(as_uuid=True), ForeignKey("post_comments.comment_id"), nullable=True, index=True)

    body       = Column(Text, nullable=False)
    like_count = Column(Integer, nullable=False, default=0, server_default="0")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
