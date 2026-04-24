from sqlalchemy import Column, DateTime, ForeignKey, String, Uuid
from sqlalchemy.sql import func
import uuid
from database import Base


class DBDeviceToken(Base):
    """APNs device token for sending push notifications to a user's iOS device.

    One user can have multiple devices (iPhone + iPad). Tokens are opaque
    strings from APNs; if a token is reported invalid by APNs we delete the row.
    """
    __tablename__ = "device_tokens"

    id         = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id    = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)
    token      = Column(String(256), nullable=False, unique=True, index=True)
    platform   = Column(String(16), nullable=False, default="ios")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), server_default=func.now())
