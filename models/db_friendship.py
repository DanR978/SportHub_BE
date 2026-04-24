from sqlalchemy import Column, DateTime, String, ForeignKey, Uuid, UniqueConstraint
from sqlalchemy.sql import func
import uuid
from database import Base


class DBFriendship(Base):
    """
    Friend relationship between two users. One row per pair — when Alice sends
    a request to Bob the row is created with requester=Alice, addressee=Bob,
    status='pending'. On accept the status flips to 'accepted' and both
    directions are treated as friends. Unfriending deletes the row.
    """
    __tablename__ = "friendships"
    __table_args__ = (UniqueConstraint("requester_id", "addressee_id", name="uq_friendship_pair"),)

    friendship_id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requester_id  = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)
    addressee_id  = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)
    status        = Column(String(16), nullable=False, default="pending")  # pending | accepted
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    accepted_at   = Column(DateTime(timezone=True), nullable=True)
