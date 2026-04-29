from passlib.hash import bcrypt
from jose import jwt
from datetime import date, datetime, timedelta, timezone
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from database import get_db
from models.db_user import DBUser
import os
from dotenv import load_dotenv

load_dotenv()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/users/login")

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))

MIN_AGE_YEARS = 13


def hash_password(plain_password: str) -> str:
    return bcrypt.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"type": "access", "exp": expire})
    return jwt.encode(claims=to_encode, key=SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"type": "refresh", "exp": expire})
    return jwt.encode(claims=to_encode, key=SECRET_KEY, algorithm=ALGORITHM)


def issue_token_pair(email: str) -> dict:
    return {
        "access_token": create_access_token({"sub": email}),
        "refresh_token": create_refresh_token({"sub": email}),
        "token_type": "bearer",
    }


def decode_refresh_token(token: str) -> str:
    """Return the subject (email) if the token is a valid refresh token; raise 401 otherwise."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Wrong token type")
    email = payload.get("sub")
    if not email:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    return email


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> DBUser:
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        token_type = payload.get("type", "access")  # legacy tokens have no type; treat as access
        if email is None or token_type != "access":
            raise credentials_exception
    except jwt.JWTError:
        raise credentials_exception

    user = db.query(DBUser).filter(DBUser.email == email).first()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    # Bump last_seen_at at most once per minute. We do this in a separate
    # short-lived session so committing here doesn't expire the `user`
    # attributes loaded by the request's session — endpoints like /users/me
    # return `current_user` directly and need its fields intact.
    try:
        now = datetime.now(timezone.utc)
        last = user.last_seen_at
        if last is None or (now - last).total_seconds() > 60:
            from sqlalchemy import update as _sa_update
            from database import SessionLocal
            with SessionLocal() as touch_db:
                touch_db.execute(
                    _sa_update(DBUser)
                    .where(DBUser.user_id == user.user_id)
                    .values(last_seen_at=now)
                )
                touch_db.commit()
            # Mirror onto the in-memory object so the rest of this request
            # sees fresh data without a re-fetch.
            user.last_seen_at = now
    except Exception:
        pass

    return user


def get_admin_user(current_user: DBUser = Depends(get_current_user)) -> DBUser:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def verify_minimum_age(dob: date | None) -> None:
    """Raise 400 if dob is below MIN_AGE_YEARS. No-op if dob is None."""
    if dob is None:
        return
    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age < MIN_AGE_YEARS:
        raise HTTPException(
            status_code=400,
            detail=f"You must be at least {MIN_AGE_YEARS} years old to use Game Radar.",
        )
