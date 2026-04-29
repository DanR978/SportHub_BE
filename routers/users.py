import logging
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from pydantic import BaseModel
from models.db_event_participant import DBEventParticipant
from schemas.user import User, UserCreate, SocialLoginRequest, UserUpdate
from models.db_user import DBUser
from sqlalchemy.orm import Session
from database import get_db
from fastapi.security import OAuth2PasswordRequestForm
from auth import hash_password, verify_password, get_current_user, verify_minimum_age, issue_token_pair, decode_refresh_token
from rate_limiter import limiter
from better_profanity import profanity
import httpx
import jwt as pyjwt
import json
import os
import base64
from models.db_event import DBEvent
from models.db_host_rating import DBHostRating
from models.db_bookmark import DBBookmark
from models.db_device_token import DBDeviceToken

router = APIRouter()
logger = logging.getLogger(__name__)

APPLE_CLIENT_ID = os.getenv("APPLE_CLIENT_ID", "")

# A list of accepted audiences. APPLE_CLIENT_ID may be a single bundle ID or
# a comma-separated list; we accept all of them. Expo Go uses
# 'host.exp.Exponent' as its audience so dev builds pre-TestFlight work out
# of the box.
_apple_audiences = [a.strip() for a in APPLE_CLIENT_ID.split(",") if a.strip()]
if "host.exp.Exponent" not in _apple_audiences:
    _apple_audiences.append("host.exp.Exponent")

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


# ── Image validation ─────────────────────────────────────────────────────────

MAX_IMAGE_MB = 5
ALLOWED_IMAGE_PREFIXES = [
    'data:image/jpeg;base64,', 'data:image/png;base64,',
    'data:image/webp;base64,', 'data:image/gif;base64,',
    'data:image/jpg;base64,',
    'data:image/heic;base64,', 'data:image/heif;base64,',
]


def validate_image(value: str) -> None:
    """Raises HTTPException if image is invalid."""
    if not value or value == 'null':
        return
    if not any(value.startswith(p) for p in ALLOWED_IMAGE_PREFIXES):
        raise HTTPException(status_code=400, detail="Invalid image format. Use JPEG, PNG, HEIC, WebP, or GIF.")
    try:
        raw = base64.b64decode(value.split(',', 1)[1])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image data.")
    if len(raw) / (1024 * 1024) > MAX_IMAGE_MB:
        raise HTTPException(status_code=400, detail=f"Image too large. Maximum is {MAX_IMAGE_MB}MB.")
    if len(raw) < 1000:
        raise HTTPException(status_code=400, detail="Image appears to be corrupt.")


# ── Signup / Login ───────────────────────────────────────────────────────────

@router.post("/users/signup", response_model=User)
@limiter.limit("5/minute")
async def create_user(request: Request, user: UserCreate, db: Session = Depends(get_db)):
    if len(user.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    verify_minimum_age(user.date_of_birth)
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
@limiter.limit("10/minute")
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return issue_token_pair(user.email)


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/users/token/refresh")
@limiter.limit("20/minute")
async def refresh(request: Request, body: RefreshRequest, db: Session = Depends(get_db)):
    email = decode_refresh_token(body.refresh_token)
    user = db.query(DBUser).filter(DBUser.email == email).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return issue_token_pair(user.email)


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

    tokens = issue_token_pair(user.email)
    return {
        **tokens,
        "is_new": is_new,
        "user": {"email": user.email, "first_name": user.first_name, "last_name": user.last_name},
    }


# ── Apple Login ──────────────────────────────────────────────────────────────

@router.post("/users/apple-login")
async def apple_login(body: SocialLoginRequest, db: Session = Depends(get_db)):
    if not body.identity_token:
        raise HTTPException(status_code=400, detail="identity_token is required")

    try:
        unverified_header = pyjwt.get_unverified_header(body.identity_token)
        kid = unverified_header.get("kid")

        unverified_claims = pyjwt.decode(
            body.identity_token,
            options={"verify_signature": False, "verify_exp": False},
            algorithms=["RS256"],
        )
        token_audience = unverified_claims.get("aud", "")
        token_exp = unverified_claims.get("exp", 0)
        logger.debug("Apple login: aud=%s configured=%s kid=%s exp=%s", token_audience, APPLE_CLIENT_ID, kid, token_exp)

        apple_keys = await _get_apple_public_keys()
        if not apple_keys:
            logger.error("Apple login: could not fetch JWKS")
            raise HTTPException(status_code=502, detail="Could not fetch Apple public keys")

        matching_key = next((k for k in apple_keys if k["kid"] == kid), None)
        if not matching_key:
            logger.info("Apple login: kid %s not in cache, refreshing", kid)
            _apple_keys_cache["keys"] = None
            apple_keys = await _get_apple_public_keys()
            matching_key = next((k for k in apple_keys if k["kid"] == kid), None) if apple_keys else None

        if not matching_key:
            logger.warning("Apple login: no matching key for kid=%s after refresh", kid)
            raise HTTPException(status_code=401, detail="Apple signing key not found")

        public_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(matching_key))

        if _apple_audiences:
            # pyjwt accepts audience as a list; verification passes when the
            # token's aud matches any entry.
            decoded = pyjwt.decode(
                body.identity_token,
                public_key,
                algorithms=["RS256"],
                audience=_apple_audiences,
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

    except pyjwt.ExpiredSignatureError:
        logger.info("Apple login failed: token expired")
        raise HTTPException(status_code=401, detail="Apple token has expired. Please try again.")
    except pyjwt.InvalidAudienceError:
        unverified = pyjwt.decode(
            body.identity_token,
            options={"verify_signature": False, "verify_exp": False},
            algorithms=["RS256"],
        )
        actual_aud = unverified.get("aud", "unknown")
        logger.warning("Apple login failed: audience mismatch token=%s env=%s", actual_aud, APPLE_CLIENT_ID)
        raise HTTPException(status_code=401, detail=f"Apple audience mismatch. Set APPLE_CLIENT_ID={actual_aud} in your .env")
    except pyjwt.InvalidIssuerError:
        logger.warning("Apple login failed: invalid issuer")
        raise HTTPException(status_code=401, detail="Invalid Apple token issuer")
    except pyjwt.InvalidTokenError as e:
        logger.warning("Apple login failed: %s", type(e).__name__)
        raise HTTPException(status_code=401, detail=f"Invalid Apple token: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Apple login failed unexpectedly")
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

    tokens = issue_token_pair(user.email)
    return {
        **tokens,
        "is_new": is_new,
        "user": {"email": user.email, "first_name": user.first_name, "last_name": user.last_name},
    }


# ── Profile ──────────────────────────────────────────────────────────────────

@router.get("/users/me")
def get_me(current_user: DBUser = Depends(get_current_user)):
    return current_user


@router.patch("/users/me")
def update_me(updates: UserUpdate, current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    data = updates.model_dump(exclude_unset=True)

    # ── Enforce minimum age when DOB is set/changed ──────────────
    if "date_of_birth" in data:
        verify_minimum_age(data["date_of_birth"])

    # ── Validate images ──────────────────────────────────────────
    for field in ['avatar_photo', 'banner_photo']:
        if field in data and data[field]:
            validate_image(data[field])

    # ── Check text fields for profanity ──────────────────────────
    for field in ['bio', 'first_name', 'last_name']:
        if field in data and data[field]:
            if profanity.contains_profanity(data[field]):
                label = 'Bio' if field == 'bio' else 'Name'
                raise HTTPException(status_code=400, detail=f"{label} contains inappropriate language")

    for field, value in data.items():
        setattr(current_user, field, value)
    db.commit()
    db.refresh(current_user)
    return current_user


# ── Avatar / Banner Upload (multipart, S3-backed when configured) ────────────

ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
    # iPhone defaults — they reach the server as image/heic / image/heif
    # because expo-image-picker doesn't always transcode on iOS.
    "image/heic", "image/heif",
}


@router.post("/users/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    kind: str = "avatar",  # 'avatar' or 'banner'
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    if kind not in ("avatar", "banner"):
        raise HTTPException(status_code=400, detail="kind must be 'avatar' or 'banner'")
    if file.content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Content-Type must be one of {ALLOWED_IMAGE_CONTENT_TYPES}")

    data = await file.read()
    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_IMAGE_MB:
        raise HTTPException(status_code=400, detail=f"Image too large. Maximum is {MAX_IMAGE_MB}MB.")
    if len(data) < 1000:
        raise HTTPException(status_code=400, detail="Image appears to be corrupt.")

    import storage
    url = storage.upload_bytes(data, file.content_type, prefix=f"{kind}s")
    if not url:
        # S3 not configured — keep working by storing as base64 in the DB (dev mode)
        import base64 as _b64
        url = f"data:{file.content_type};base64," + _b64.b64encode(data).decode()

    field = "avatar_photo" if kind == "avatar" else "banner_photo"
    setattr(current_user, field, url)
    db.commit()
    return {kind: url}


# ── Password Reset ───────────────────────────────────────────────────────────

import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)
APP_NAME = "Game Radar"


class ForgotPasswordRequest(BaseModel):
    email: str
    method: str = "email"


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


def send_reset_email(to_email: str, token: str, name: str):
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning("SMTP not configured; reset token printed to logs (dev only). token=%s", token)
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
    except Exception:
        logger.exception("Failed to send reset email")


def send_reset_sms(phone: str, token: str):
    # SMS provider not yet integrated. Log token in dev so the flow is testable.
    logger.warning("SMS provider not configured; reset token printed to logs (dev only). token=%s", token)


@router.post("/users/forgot-password")
@limiter.limit("3/minute")
def forgot_password(request: Request, body: ForgotPasswordRequest, db: Session = Depends(get_db)):
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
@limiter.limit("5/minute")
def reset_password(request: Request, body: ResetPasswordRequest, db: Session = Depends(get_db)):
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

@router.get("/users/search")
def search_users(
    q: str,
    limit: int = 20,
    current_user: DBUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Lightweight name-prefix search used by the New Message screen and
    @-mentions. Matches against first+last name (case-insensitive) and skips
    the current user and anyone they've blocked / been blocked by.
    """
    from sqlalchemy import or_, func as sa_func
    from models.db_block import DBBlock

    q = (q or "").strip()
    if len(q) < 2:
        return []

    blocked_out = {r[0] for r in db.query(DBBlock.blocked_id).filter(DBBlock.blocker_id == current_user.user_id).all()}
    blocked_in  = {r[0] for r in db.query(DBBlock.blocker_id).filter(DBBlock.blocked_id == current_user.user_id).all()}
    excluded = blocked_out | blocked_in | {current_user.user_id}

    like = f"%{q.lower()}%"
    users = (
        db.query(DBUser)
        .filter(
            DBUser.is_active.is_(True),
            or_(
                sa_func.lower(DBUser.first_name).like(like),
                sa_func.lower(DBUser.last_name).like(like),
                sa_func.lower(sa_func.concat(DBUser.first_name, ' ', DBUser.last_name)).like(like),
            ),
        )
        .limit(limit * 2)
        .all()
    )
    results = []
    for u in users:
        if u.user_id in excluded:
            continue
        results.append({
            "user_id":      str(u.user_id),
            "first_name":   u.first_name,
            "last_name":    u.last_name,
            "avatar_photo": u.avatar_photo,
            "bio":          u.bio,
        })
        if len(results) >= limit:
            break
    return results


@router.get("/users/me/upcoming-events")
def get_upcoming_events(current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
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


# ── Device Tokens (APNs) ──────────────────────────────────────────────────────

class DeviceTokenRequest(BaseModel):
    token: str
    platform: str = "ios"


@router.post("/users/me/devices")
def register_device(
    body: DeviceTokenRequest,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    if not body.token or len(body.token) < 32:
        raise HTTPException(status_code=400, detail="Invalid device token")

    existing = db.query(DBDeviceToken).filter(DBDeviceToken.token == body.token).first()
    if existing:
        # If token was already registered to a different user (device resale, reinstall),
        # reassign to current user.
        existing.user_id = current_user.user_id
        existing.last_used_at = datetime.now(timezone.utc)
        db.commit()
        return {"id": str(existing.id)}

    row = DBDeviceToken(user_id=current_user.user_id, token=body.token, platform=body.platform)
    db.add(row)
    db.commit()
    return {"id": str(row.id)}


@router.delete("/users/me/devices/{token}")
def unregister_device(
    token: str,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    deleted = db.query(DBDeviceToken).filter(
        DBDeviceToken.token == token,
        DBDeviceToken.user_id == current_user.user_id,
    ).delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}


# ── Delete Account (permanent) ────────────────────────────────────────────────

@router.delete("/users/me")
async def delete_account(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Permanently delete user account and all associated data."""
    uid = current_user.user_id

    # 1. Remove from all events they joined
    db.query(DBEventParticipant).filter(
        DBEventParticipant.user_id == uid
    ).delete(synchronize_session=False)

    # 2. Remove all bookmarks
    try:
        db.query(DBBookmark).filter(DBBookmark.user_id == uid).delete(synchronize_session=False)
    except Exception:
        pass

    # 3. Remove all host ratings they gave
    db.query(DBHostRating).filter(
        DBHostRating.rater_id == uid
    ).delete(synchronize_session=False)

    # 4. Delete all events they organized + their participants
    organized_events = db.query(DBEvent).filter(
        DBEvent.organizer_id == uid
    ).all()
    for event in organized_events:
        db.query(DBEventParticipant).filter(
            DBEventParticipant.event_id == event.event_id
        ).delete(synchronize_session=False)
        db.delete(event)

    # 5. Remove block/report records involving this user
    try:
        from models.db_block import DBBlock
        from models.db_report import DBReport
        db.query(DBBlock).filter(
            (DBBlock.blocker_id == uid) | (DBBlock.blocked_id == uid)
        ).delete(synchronize_session=False)
        db.query(DBReport).filter(
            DBReport.reporter_id == uid
        ).delete(synchronize_session=False)
    except Exception:
        pass

    # 6. Remove APNs device tokens
    db.query(DBDeviceToken).filter(DBDeviceToken.user_id == uid).delete(synchronize_session=False)

    # 7. Delete the user
    db.delete(current_user)
    db.commit()

    return {"status": "deleted"}