from sqlalchemy import Column, DateTime, String, Text, Float, Integer, ForeignKey, Uuid, Index
from sqlalchemy.sql import func
import uuid
from database import Base


class DBPost(Base):
    """
    Community feed post. Can be a plain status, attached to a location
    (so nearby feeds can surface it), or linked to an event.
    Score is denormalized (likes - downvotes) for cheap ranking.
    """
    __tablename__ = "posts"

    post_id       = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    author_id     = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)

    body          = Column(Text, nullable=False)
    # TEXT — long enough to hold S3 signed URLs *and* (in dev with no S3) the
    # base64-encoded fallback the storage helper produces.
    image_url     = Column(Text, nullable=True)

    # Optional tags / grouping
    sport         = Column(String(40), nullable=True, index=True)
    event_id      = Column(Uuid(as_uuid=True), ForeignKey("events.event_id"), nullable=True, index=True)

    # Geotag — lets us surface nearby posts on map + in radius feed
    latitude      = Column(Float, nullable=True)
    longitude     = Column(Float, nullable=True)
    place_label   = Column(String(120), nullable=True)

    # Denormalized counters (kept in sync by the reactions / comments endpoints)
    like_count    = Column(Integer, nullable=False, default=0, server_default="0")
    downvote_count= Column(Integer, nullable=False, default=0, server_default="0")
    comment_count = Column(Integer, nullable=False, default=0, server_default="0")
    share_count   = Column(Integer, nullable=False, default=0, server_default="0")
    score         = Column(Integer, nullable=False, default=0, server_default="0", index=True)

    created_at    = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


Index("ix_posts_geo", DBPost.latitude, DBPost.longitude)
