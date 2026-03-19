from sqlalchemy import Boolean, Column, Date, DateTime, String, Time, Float, Integer, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
import uuid
from database import Base

class DBUser(Base):
    __tablename__ = "users"

    user_id        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    first_name     = Column(String(50), index=True)
    last_name      = Column(String(50), index=True)
    email          = Column(String, unique=True, index=True)
    hashed_password= Column(String)
    date_of_birth  = Column(Date, nullable=True)
    nationality    = Column(String(100), nullable=True)
    phone_number   = Column(String(20), nullable=True)
    bio            = Column(String(300), nullable=True)
    sports         = Column(String, nullable=True)
    avatar_config  = Column(String, nullable=True)
    avatar_photo   = Column(String, nullable=True)
    banner_photo   = Column(String, nullable=True)
    host_rating    = Column(Numeric(3, 2), nullable=True)
    total_ratings  = Column(Integer, default=0)
    instagram      = Column(String(100), nullable=True)
    facebook       = Column(String(100), nullable=True)
    is_active      = Column(Boolean, default=True, server_default='true')
    reset_token        = Column(String(10), nullable=True)
    reset_token_expiry = Column(DateTime, nullable=True)
    is_premium         = Column(Boolean, default=False, server_default='false')
    premium_expires    = Column(DateTime, nullable=True)