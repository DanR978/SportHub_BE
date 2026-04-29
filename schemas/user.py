from uuid import UUID, uuid4
from pydantic import BaseModel, EmailStr, Field
from datetime import date, datetime
from typing import Optional
from decimal import Decimal

class UserBase(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    date_of_birth: Optional[date] = None
    nationality: Optional[str] = None
    phone_number: Optional[str] = None

class UserCreate(UserBase):
    password: str

class User(UserBase):
    user_id:       UUID = Field(default_factory=uuid4)
    bio:           Optional[str] = None
    sports:        Optional[str] = None
    avatar_photo:  Optional[str] = None
    banner_photo:  Optional[str] = None
    host_rating:   Optional[Decimal] = None
    total_ratings: Optional[int] = 0
    instagram:     Optional[str] = None
    facebook:      Optional[str] = None
    is_active:     Optional[bool] = True
    is_admin:      Optional[bool] = False
    terms_accepted_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class SocialLoginRequest(BaseModel):
    access_token:   Optional[str] = None
    identity_token: Optional[str] = None
    email:          Optional[str] = None
    first_name:     Optional[str] = None
    last_name:      Optional[str] = None

class UserUpdate(BaseModel):
    first_name:     Optional[str] = None
    last_name:      Optional[str] = None
    date_of_birth:  Optional[date] = None
    nationality:    Optional[str] = None
    phone_number:   Optional[str] = None
    bio:            Optional[str] = None
    sports:         Optional[str] = None
    avatar_photo:   Optional[str] = None
    banner_photo:   Optional[str] = None
    instagram:      Optional[str] = None
    facebook:       Optional[str] = None
    is_active:      Optional[bool] = None