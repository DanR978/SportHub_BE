from fastapi import APIRouter, Depends, HTTPException, Header
from schemas.user import User, UserCreate, SocialLoginRequest, UserUpdate
from models.db_user import DBUser
from sqlalchemy.orm import Session
from database import get_db
from fastapi.security import OAuth2PasswordRequestForm
from auth import hash_password, verify_password, create_access_token, get_current_user
import httpx
import jwt as pyjwt
import json
import os

router = APIRouter()

APPLE_CLIENT_ID = os.getenv("APPLE_CLIENT_ID", "")

# ── Apple JWKS key cache ─────────────────────────────────────────────────────
_apple_keys_cache = {"keys": None}


async def _get_apple_public_keys():
    """Fetch Apple's public signing keys (they rotate infrequently)."""
    if _apple_keys_cache["keys"]:
        return _apple_keys_cache["keys"]
    async with httpx.AsyncClient() as client:
        res = await client.get("https://appleid.apple.com/auth/keys")
        if res.status_code == 200:
            _apple_keys_cache["keys"] = res.json()["keys"]
            return _apple_keys_cache["keys"]
    return None


# ── Signup / Login ───────────────────────────────────────────────────────────

@router.post("/users/signup", response_model=User)
async def create_user(user: UserCreate, db: Session = Depends(get_db)):
    if len(user.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
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


# ── Google Login ─────────────────────────────────────────────────────────────

@router.post("/users/google-login")
async def google_login(body: SocialLoginRequest, db: Session = Depends(get_db)):
    async with httpx.AsyncClient() as client:
        res = await client.get(
            "https://www.googleapis.com/userinfo/v2/me",
            headers={"Authorization": f"Bearer {body.access_token}"},
        )
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
        user = DBUser(email=email, first_name="", last_name="", hashed_password="")
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.date_of_birth or not user.nationality or not user.first_name:
        is_new = True

    token = create_access_token(data={"sub": user.email})
    return {
        "access_token": token,
        "token_type": "bearer",
        "is_new": is_new,
        "user": {"email": user.email, "first_name": user.first_name, "last_name": user.last_name},
    }


# ── Apple Login ──────────────────────────────────────────────────────────────

@router.post("/users/apple-login")
async def apple_login(body: SocialLoginRequest, db: Session = Depends(get_db)):
    if not body.identity_token:
        raise HTTPException(status_code=400, detail="identity_token is required")

    try:
        # 1. Read token header to find signing key
        unverified_header = pyjwt.get_unverified_header(body.identity_token)
        kid = unverified_header.get("kid")

        # 2. Peek at unverified claims for debugging
        unverified_claims = pyjwt.decode(
            body.identity_token,
            options={"verify_signature": False, "verify_exp": False},
            algorithms=["RS256"],
        )
        token_audience = unverified_claims.get("aud", "")
        token_exp = unverified_claims.get("exp", 0)
        print(f"[Apple Login] Token audience: {token_audience}, Configured APPLE_CLIENT_ID: {APPLE_CLIENT_ID}")
        print(f"[Apple Login] Token kid: {kid}, exp: {token_exp}")

        # 3. Fetch Apple's public keys and find the match
        apple_keys = await _get_apple_public_keys()
        if not apple_keys:
            print("[Apple Login] ERROR: Could not fetch Apple public keys")
            raise HTTPException(status_code=502, detail="Could not fetch Apple public keys")

        matching_key = next((k for k in apple_keys if k["kid"] == kid), None)
        if not matching_key:
            print(f"[Apple Login] Key not found for kid={kid}, refreshing cache...")
            _apple_keys_cache["keys"] = None
            apple_keys = await _get_apple_public_keys()
            matching_key = next((k for k in apple_keys if k["kid"] == kid), None) if apple_keys else None

        if not matching_key:
            print(f"[Apple Login] ERROR: No matching key for kid={kid}")
            raise HTTPException(status_code=401, detail="Apple signing key not found")

        # 4. Build public key and decode
        public_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(matching_key))

        if APPLE_CLIENT_ID:
            decoded = pyjwt.decode(
                body.identity_token,
                public_key,
                algorithms=["RS256"],
                audience=APPLE_CLIENT_ID,
                issuer="https://appleid.apple.com",
                leeway=30,
            )
        else:
            decoded = pyjwt.decode(
                body.identity_token,
                public_key,
                algorithms=["RS256"],
                issuer="https://appleid.apple.com",
                options={"verify_aud": False},
                leeway=30,
            )

        print(f"[Apple Login] SUCCESS — decoded email: {decoded.get('email')}")

    except pyjwt.ExpiredSignatureError:
        print("[Apple Login] FAILED: Token has expired")
        raise HTTPException(status_code=401, detail="Apple token has expired. Please try again.")
    except pyjwt.InvalidAudienceError:
        unverified = pyjwt.decode(
            body.identity_token,
            options={"verify_signature": False, "verify_exp": False},
            algorithms=["RS256"],
        )
        actual_aud = unverified.get("aud", "unknown")
        print(f"[Apple Login] FAILED: Audience mismatch — token='{actual_aud}', env='{APPLE_CLIENT_ID}'")
        raise HTTPException(status_code=401, detail=f"Apple audience mismatch. Set APPLE_CLIENT_ID={actual_aud} in your .env")
    except pyjwt.InvalidIssuerError:
        print("[Apple Login] FAILED: Invalid issuer (not https://appleid.apple.com)")
        raise HTTPException(status_code=401, detail="Invalid Apple token issuer")
    except pyjwt.InvalidTokenError as e:
        print(f"[Apple Login] FAILED: InvalidTokenError — {type(e).__name__}: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid Apple token: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Apple Login] FAILED: Unexpected error — {type(e).__name__}: {e}")
        raise HTTPException(status_code=401, detail=f"Could not verify Apple token: {str(e)}")

    email = decoded.get("email") or body.email
    if not email:
        raise HTTPException(status_code=400, detail="Could not get email from Apple")

    is_new = False
    user = db.query(DBUser).filter(DBUser.email == email).first()
    if not user:
        is_new = True
        user = DBUser(email=email, first_name="", last_name="", hashed_password="")
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.date_of_birth or not user.nationality or not user.first_name:
        is_new = True

    token = create_access_token(data={"sub": user.email})
    return {
        "access_token": token,
        "token_type": "bearer",
        "is_new": is_new,
        "user": {"email": user.email, "first_name": user.first_name, "last_name": user.last_name},
    }


# ── Profile ──────────────────────────────────────────────────────────────────

@router.get("/users/me")
def get_me(current_user: DBUser = Depends(get_current_user)):
    return current_user


@router.get("/users/me/stats")
def get_my_stats(current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    from models.db_event import DBEvent
    from models.db_event_participant import DBEventParticipant
    created = db.query(DBEvent).filter(DBEvent.organizer_id == current_user.user_id).count()
    joined = db.query(DBEventParticipant).filter(
        DBEventParticipant.user_id == current_user.user_id
    ).count()
    return {"created": created, "joined": joined}


@router.patch("/users/me")
def update_me(updates: UserUpdate, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    for field, value in updates.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)
    db.commit()
    db.refresh(current_user)
    return current_user


# ── Password Reset ───────────────────────────────────────────────────────────

import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)
APP_NAME = "SportMap"


class ForgotPasswordRequest(BaseModel):
    email: str
    method: str = "email"


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


def send_reset_email(to_email: str, token: str, name: str):
    if not SMTP_USER or not SMTP_PASSWORD:
        print(f"[DEV] Reset token for {to_email}: {token}")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Reset your {APP_NAME} password"
    msg["From"] = f"{APP_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_email
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
      <h2 style="color:#1a1a2e">Hi {name},</h2>
      <p>You requested a password reset for your {APP_NAME} account.</p>
      <p>Your 6-digit reset code is:</p>
      <div style="font-size:40px;font-weight:900;letter-spacing:12px;color:#16a34a;padding:24px 0">{token}</div>
      <p style="color:#999">This code expires in 15 minutes. If you didn't request this, ignore this email.</p>
    </div>"""
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.sendmail(FROM_EMAIL, to_email, msg.as_string())
    except Exception as e:
        print(f"Email send failed: {e}")


def send_reset_sms(phone: str, token: str):
    print(f"[DEV] SMS reset token for {phone}: {token}")


@router.post("/users/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.email == body.email.lower().strip()).first()

    if body.method == "check":
        if not user:
            return {"has_phone": False}
        return {"has_phone": bool(user.phone_number)}

    if not user:
        return {"message": "If that email exists, a code has been sent."}

    if body.method == "sms":
        if not user.phone_number:
            raise HTTPException(status_code=400, detail="No phone number on this account")

    token = str(secrets.randbelow(900000) + 100000)
    expiry = datetime.now(timezone.utc) + timedelta(minutes=15)

    user.reset_token = token
    user.reset_token_expiry = expiry
    db.commit()

    if body.method == "sms":
        send_reset_sms(user.phone_number, token)
        return {"message": "Code sent via SMS", "method": "sms"}
    else:
        send_reset_email(user.email, token, user.first_name or "there")
        return {"message": "Code sent to email", "method": "email"}


@router.post("/users/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.reset_token == body.token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    if user.reset_token_expiry < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Code has expired")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    user.hashed_password = hash_password(body.new_password)
    user.reset_token = None
    user.reset_token_expiry = None
    db.commit()
    return {"message": "Password updated successfully"}


@router.post("/users/verify-reset-token")
def verify_reset_token(body: dict, db: Session = Depends(get_db)):
    token = body.get("token", "")
    user = db.query(DBUser).filter(DBUser.reset_token == token).first()
    if not user or user.reset_token_expiry < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    return {"valid": True, "email": user.email}


# ── Upcoming Events ──────────────────────────────────────────────────────────

@router.get("/users/me/upcoming-events")
def get_upcoming_events(current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    from models.db_event import DBEvent
    from models.db_event_participant import DBEventParticipant
    from datetime import date

    today = date.today()

    organizing = db.query(DBEvent).filter(
        DBEvent.organizer_id == current_user.user_id,
        DBEvent.start_date >= today,
    ).order_by(DBEvent.start_date).all()

    joined_ids = db.query(DBEventParticipant.event_id).filter(
        DBEventParticipant.user_id == current_user.user_id
    ).subquery()

    joined = db.query(DBEvent).filter(
        DBEvent.event_id.in_(joined_ids),
        DBEvent.organizer_id != current_user.user_id,
        DBEvent.start_date >= today,
    ).order_by(DBEvent.start_date).all()

    def serialize(e):
        return {
            "event_id": str(e.event_id),
            "title": e.title,
            "sport": e.sport,
            "start_date": str(e.start_date),
            "start_time": str(e.start_time) if e.start_time else None,
            "location": e.location,
            "max_players": e.max_players,
            "experience_level": e.experience_level,
            "cost": float(e.cost) if e.cost is not None else 0,
        }

    return {
        "organizing": [serialize(e) for e in organizing],
        "joined": [serialize(e) for e in joined],
    }


# ── Premium ──────────────────────────────────────────────────────────────────

@router.get("/users/me/premium")
def get_premium_status(current_user: DBUser = Depends(get_current_user)):
    is_active = current_user.is_premium and (
        current_user.premium_expires is None
        or current_user.premium_expires > datetime.now(timezone.utc)
    )
    return {
        "is_premium": is_active,
        "expires": current_user.premium_expires.isoformat() if current_user.premium_expires else None,
    }


@router.post("/users/me/premium/activate")
def activate_premium(db: Session = Depends(get_db), current_user: DBUser = Depends(get_current_user)):
    current_user.is_premium = True
    current_user.premium_expires = datetime.now(timezone.utc) + timedelta(days=30)
    db.commit()
    return {
        "is_premium": True,
        "expires": current_user.premium_expires.isoformat(),
    }