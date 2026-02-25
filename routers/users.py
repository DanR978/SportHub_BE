from fastapi import APIRouter, Depends, HTTPException, Header
from models.db_event_participant import DBEventParticipant
from models.db_event import DBEvent
from models.user import User, UserCreate, SocialLoginRequest, UserUpdate
from models.db_user import DBUser
from sqlalchemy.orm import Session
from database import get_db
from fastapi.security import OAuth2PasswordRequestForm
from auth import hash_password, verify_password, create_access_token
from datetime import date as date_type
import httpx
import jwt as pyjwt
import os

router = APIRouter()

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key")
ALGORITHM = "HS256"

def get_current_user(authorization: str = Header(...), db: Session = Depends(get_db)):
    try:
        token = authorization.replace("Bearer ", "")
        payload = pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        user = db.query(DBUser).filter(DBUser.email == email).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

@router.post("/users/signup", response_model=User)
async def create_user(user: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(DBUser).filter(DBUser.email == user.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed = hash_password(user.password)
    new_user = DBUser(**user.model_dump(exclude={"password"}), hashed_password=hashed)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@router.post("/users/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(data={"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}

@router.post("/users/google-login")
async def google_login(body: SocialLoginRequest, db: Session = Depends(get_db)):
    async with httpx.AsyncClient() as client:
        res = await client.get("https://www.googleapis.com/userinfo/v2/me",
            headers={"Authorization": f"Bearer {body.access_token}"})
    if res.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid Google token")
    google_user = res.json()
    email = google_user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Could not get email from Google")

    is_new = False
    user = db.query(DBUser).filter(DBUser.email == email).first()
    if not user:
        is_new = True
        user = DBUser(
            email=email,
            first_name=google_user.get("given_name", ""),
            last_name=google_user.get("family_name", ""),
            hashed_password="",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    token = create_access_token(data={"sub": user.email})
    return {
        "access_token": token,
        "token_type": "bearer",
        "is_new": is_new,
        "user": {
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }
    }

@router.post("/users/apple-login")
async def apple_login(body: SocialLoginRequest, db: Session = Depends(get_db)):
    try:
        decoded = pyjwt.decode(
            body.identity_token,
            options={"verify_signature": False, "verify_exp": False},
            algorithms=["RS256"],
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Could not decode Apple token: {str(e)}")

    email = decoded.get("email") or body.email
    if not email:
        raise HTTPException(status_code=400, detail="Could not get email from Apple")

    is_new = False
    user = db.query(DBUser).filter(DBUser.email == email).first()
    if not user:
        is_new = True
        user = DBUser(
            email=email,
            first_name=body.first_name or "",
            last_name=body.last_name or "",
            hashed_password="",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    token = create_access_token(data={"sub": user.email})
    return {
        "access_token": token,
        "token_type": "bearer",
        "is_new": is_new,
        "user": {
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }
    }

@router.get("/users/me")
def get_me(current_user: DBUser = Depends(get_current_user)):
    return current_user

@router.get("/users/me/stats")
def get_my_stats(current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        created = db.query(DBEvent).filter(DBEvent.organizer_id == current_user.user_id).count()
        joined = db.query(DBEventParticipant).filter(DBEventParticipant.user_id == current_user.user_id).count()
        return {"created": created, "joined": joined}
    except Exception as e:
        print(f"Stats error: {e}")
        return {"created": 0, "joined": 0}

@router.patch("/users/me")
def update_me(updates: UserUpdate, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    for field, value in updates.model_dump(exclude_none=True).items():
        setattr(current_user, field, value)
    db.commit()
    db.refresh(current_user)
    return current_user

@router.get("/users/me/upcoming-events")
async def get_upcoming_events(db: Session = Depends(get_db), current_user: DBUser = Depends(get_current_user)):
    today = date_type.today()
    # Events user is organizing
    organizing = db.query(DBEvent).filter(
        DBEvent.organizer_id == current_user.user_id,
        DBEvent.start_date >= today
    ).order_by(DBEvent.start_date, DBEvent.start_time).all()

    # Events user joined but didn't organize
    joined_ids = db.query(DBEventParticipant.event_id).filter_by(user_id=current_user.user_id).all()
    joined_ids = [j[0] for j in joined_ids]
    joined = db.query(DBEvent).filter(
        DBEvent.event_id.in_(joined_ids),
        DBEvent.organizer_id != current_user.user_id,
        DBEvent.start_date >= today
    ).order_by(DBEvent.start_date, DBEvent.start_time).all()

    for e in organizing + joined:
        e.participant_count = db.query(DBEventParticipant).filter_by(event_id=e.event_id).count()
        e.joined = True

    return {
        "organizing": organizing,
        "joined": joined
    }