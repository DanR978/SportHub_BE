import json
from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException, Header
from fastapi.responses import HTMLResponse
from models.db_event_participant import DBEventParticipant
from models.db_host_rating import DBHostRating
from models.db_report import DBReport
from models.db_block import DBBlock
from models.db_bookmark import DBBookmark
from schemas.event import Event, EventCreate, EventUpdate
from sqlalchemy.orm import Session
from database import get_db
from models.db_event import DBEvent
from models.db_archived_event import DBArchivedEvent
from auth import get_current_user, SECRET_KEY, ALGORITHM
from models.db_user import DBUser
from typing import List, Optional
from datetime import date, datetime, timedelta, timezone
from geopy.geocoders import Nominatim
from better_profanity import profanity
from geopy.distance import geodesic
from jose import jwt as jose_jwt
from pydantic import BaseModel
import os

router = APIRouter()
geolocator = Nominatim(user_agent="gameradar")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_user_id_from_header(authorization: Optional[str], db: Session) -> Optional[UUID]:
    if not authorization:
        return None
    try:
        token = authorization.replace("Bearer ", "")
        payload = jose_jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            return None
        user = db.query(DBUser).filter(DBUser.email == email).first()
        return user.user_id if user else None
    except Exception:
        return None


def enrich_event(event, db, current_user_id=None):
    event.participant_count = (
        db.query(DBEventParticipant).filter_by(event_id=event.event_id).count()
    )
    if event.organizer_id:
        organizer = db.query(DBUser).filter(DBUser.user_id == event.organizer_id).first()
        if organizer:
            event.organizer_name = f"{organizer.first_name or ''} {organizer.last_name or ''}".strip()
            event.organizer_avatar = organizer.avatar_config
            event.organizer_photo = organizer.avatar_photo
            event.host_rating = float(organizer.host_rating) if organizer.host_rating else None
            event.total_ratings = organizer.total_ratings or 0
    if current_user_id:
        event.joined = (
            db.query(DBEventParticipant)
            .filter_by(event_id=event.event_id, user_id=current_user_id)
            .first() is not None
        )
        event.is_organizer = (event.organizer_id == current_user_id)
    else:
        event.joined = False
        event.is_organizer = False
    return event


def get_blocked_ids(db, user_id):
    """Return set of user IDs that this user has blocked."""
    if not user_id:
        return set()
    blocks = db.query(DBBlock.blocked_id).filter(DBBlock.blocker_id == user_id).all()
    return {b[0] for b in blocks}


def archive_event(db, event, reason='expired'):
    """Move an event to archived_events, preserving participant IDs."""
    participants = db.query(DBEventParticipant).filter_by(event_id=event.event_id).all()
    participant_id_list = [str(p.user_id) for p in participants]
    count = len(participants)

    organizer = db.query(DBUser).filter(DBUser.user_id == event.organizer_id).first() if event.organizer_id else None

    archived = DBArchivedEvent(
        event_id          = event.event_id,
        title             = event.title,
        sport             = event.sport,
        organizer_id      = event.organizer_id,
        organizer_name    = f"{organizer.first_name} {organizer.last_name}" if organizer else None,
        location          = event.location,
        start_date        = event.start_date,
        start_time        = event.start_time,
        end_time          = getattr(event, 'end_time', None),
        max_players       = event.max_players,
        participant_count = count,
        experience_level  = event.experience_level,
        cost              = event.cost,
        description       = getattr(event, 'description', None),
        archive_reason    = reason,
        participant_ids   = json.dumps(participant_id_list),
    )
    db.add(archived)
    db.query(DBEventParticipant).filter(DBEventParticipant.event_id == event.event_id).delete()
    db.delete(event)

@router.post("/sports-events", response_model=Event)
async def create_event(event: EventCreate, db: Session = Depends(get_db), current_user: DBUser = Depends(get_current_user)):
    if profanity.contains_profanity(event.title) or profanity.contains_profanity(event.description or ""):
        raise HTTPException(status_code=400, detail="Content contains inappropriate language")

    # 4-hour max duration
    if event.end_time and event.start_time:
        start_dt = datetime.combine(date.today(), event.start_time)
        end_dt = datetime.combine(date.today(), event.end_time)
        diff_hours = (end_dt - start_dt).total_seconds() / 3600
        if diff_hours > 4:
            raise HTTPException(status_code=400, detail="Events cannot last more than 4 hours")
        if diff_hours <= 0:
            raise HTTPException(status_code=400, detail="End time must be after start time")

    lat = event.latitude
    lng = event.longitude
    if lat is None or lng is None:
        try:
            geo = geolocator.geocode(event.location)
            if not geo:
                raise HTTPException(status_code=400, detail="Could not geocode that location. Please provide latitude and longitude manually.")
            lat = geo.latitude
            lng = geo.longitude
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=400, detail="Location lookup failed. Please provide latitude and longitude manually.")

    new_event = DBEvent(
        **event.model_dump(exclude={"sport", "experience_level", "latitude", "longitude", "title", "location", "description"}),
        title=event.title.strip(),
        location=event.location.strip(),
        sport=event.sport.lower().strip(),
        experience_level=event.experience_level.lower().strip(),
        description=event.description.strip() if event.description else None,
        latitude=lat,
        longitude=lng,
        organizer_id=current_user.user_id,
    )

    db.add(new_event)
    db.commit()
    db.refresh(new_event)

    db.add(DBEventParticipant(event_id=new_event.event_id, user_id=current_user.user_id))
    db.commit()

    return enrich_event(new_event, db, current_user.user_id)

@router.get("/sports-events", response_model=list[Event])
async def get_events(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    current_user_id = _get_user_id_from_header(authorization, db)
    blocked = get_blocked_ids(db, current_user_id)
    today = date.today()
    events = db.query(DBEvent).filter(
        DBEvent.status == 'active',
        DBEvent.start_date >= today,
    ).all()
    result = []
    for event in events:
        if event.organizer_id in blocked:
            continue
        enrich_event(event, db, current_user_id)
        result.append(event)
    return result


@router.get("/sports-events/filter", response_model=list[Event])
async def filter_event(
    sports: List[str] = Query(default=None),
    experience_levels: List[str] = Query(default=None),
    start_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    latitude: Optional[float] = Query(default=None),
    longitude: Optional[float] = Query(default=None),
    radius_miles: Optional[float] = Query(default=20.0),
    authorization: Optional[str] = Header(default=None),
):
    current_user_id = _get_user_id_from_header(authorization, db)
    blocked = get_blocked_ids(db, current_user_id)
    today = date.today()
    query = db.query(DBEvent).filter(DBEvent.status == 'active', DBEvent.start_date >= today)

    if sports:
        query = query.filter(DBEvent.sport.in_([s.lower() for s in sports]))
    if experience_levels:
        query = query.filter(DBEvent.experience_level.in_([l.lower() for l in experience_levels]))
    if start_from:
        query = query.filter(DBEvent.start_date >= start_from)
    if date_to:
        query = query.filter(DBEvent.start_date <= date_to)

    events = [e for e in query.all() if e.organizer_id not in blocked]

    if latitude is not None and longitude is not None:
        nearby = []
        for event in events:
            if event.latitude is not None and event.longitude is not None:
                dist = geodesic((latitude, longitude), (event.latitude, event.longitude)).miles
                if dist <= radius_miles:
                    enrich_event(event, db, current_user_id)
                    nearby.append(event)
        return nearby

    for event in events:
        enrich_event(event, db, current_user_id)
    return events


@router.get("/sports-events/{event_id}", response_model=Event)
async def get_event(
    event_id: UUID,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    current_user_id = _get_user_id_from_header(authorization, db)
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return enrich_event(event, db, current_user_id)


# ── Participants ─────────────────────────────────────────────────────────────

@router.get("/sports-events/{event_id}/participants")
async def get_participants(event_id: UUID, db: Session = Depends(get_db)):
    """Returns the list of participants for an event with basic profile info."""
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    entries = (
        db.query(DBEventParticipant)
        .filter(DBEventParticipant.event_id == event_id)
        .order_by(DBEventParticipant.joined_at)
        .all()
    )
    participants = []
    for p in entries:
        user = db.query(DBUser).filter(DBUser.user_id == p.user_id).first()
        if not user:
            continue
        participants.append({
            "user_id":       str(user.user_id),
            "first_name":    user.first_name,
            "last_name":     user.last_name,
            "avatar_photo":  user.avatar_photo,
            "avatar_config": user.avatar_config,
            "is_organizer":  user.user_id == event.organizer_id,
            "joined_at":     p.joined_at.isoformat() if p.joined_at else None,
        })
    return {"participants": participants, "count": len(participants)}


# ── Join / Leave ──────────────────────────────────────────────────────────────

@router.post("/sports-events/{event_id}/join")
async def join_event(event_id: UUID, db: Session = Depends(get_db), current_user: DBUser = Depends(get_current_user)):
    # Lock the event row so concurrent joins can't both pass the capacity check
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).with_for_update().first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.status != 'active':
        raise HTTPException(status_code=400, detail="Event is not active")
    if db.query(DBEventParticipant).filter_by(event_id=event_id, user_id=current_user.user_id).first():
        raise HTTPException(status_code=400, detail="Already joined this event")
    count = db.query(DBEventParticipant).filter_by(event_id=event_id).count()
    if count >= event.max_players:
        raise HTTPException(status_code=400, detail="Event is full")

    db.add(DBEventParticipant(event_id=event_id, user_id=current_user.user_id))
    db.commit()

    # Notify the organizer — non-blocking; silent if APNs not configured
    if event.organizer_id and event.organizer_id != current_user.user_id:
        from notifications import send_push
        joiner_name = f"{current_user.first_name or ''} {current_user.last_name or ''}".strip() or "A player"
        send_push(
            db,
            event.organizer_id,
            title=f"{joiner_name} joined your event",
            body=f'"{event.title}" — {count + 1}/{event.max_players} spots filled',
            data={"event_id": str(event_id), "type": "participant_joined"},
        )

    return {"message": "Joined event successfully"}


@router.delete("/sports-events/{event_id}/leave")
async def leave_event(event_id: UUID, db: Session = Depends(get_db), current_user: DBUser = Depends(get_current_user)):
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if event and event.organizer_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="Organizer cannot leave their own event")
    participant = db.query(DBEventParticipant).filter_by(event_id=event_id, user_id=current_user.user_id).first()
    if not participant:
        raise HTTPException(status_code=404, detail="You are not a participant of this event")
    db.delete(participant)
    db.commit()
    return {"message": "Left event successfully"}

@router.patch("/sports-events/{event_id}", response_model=Event)
async def update_event(
    event_id: UUID,
    updates: EventUpdate,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.organizer_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Only the organizer can edit this event")

    data = updates.model_dump(exclude_unset=True)

    # Profanity check on updated fields
    if "title" in data and profanity.contains_profanity(data["title"]):
        raise HTTPException(status_code=400, detail="Title contains inappropriate language")
    if "description" in data and data["description"] and profanity.contains_profanity(data["description"]):
        raise HTTPException(status_code=400, detail="Description contains inappropriate language")

    # Normalize casing
    if "title" in data:
        data["title"] = data["title"].strip()
    if "location" in data:
        data["location"] = data["location"].strip()
    if "sport" in data:
        data["sport"] = data["sport"].lower().strip()
    if "experience_level" in data:
        data["experience_level"] = data["experience_level"].lower().strip()
    if "description" in data and data["description"]:
        data["description"] = data["description"].strip()

    # 4-hour max duration check
    new_start = data.get("start_time", event.start_time)
    new_end = data.get("end_time", event.end_time)
    if new_start and new_end:
        start_dt = datetime.combine(date.today(), new_start)
        end_dt = datetime.combine(date.today(), new_end)
        diff_hours = (end_dt - start_dt).total_seconds() / 3600
        if diff_hours > 4:
            raise HTTPException(status_code=400, detail="Events cannot last more than 4 hours")
        if diff_hours <= 0:
            raise HTTPException(status_code=400, detail="End time must be after start time")

    for field, value in data.items():
        setattr(event, field, value)

    db.commit()
    db.refresh(event)
    return enrich_event(event, db, current_user.user_id)


@router.delete("/sports-events/{event_id}")
async def delete_event(event_id: UUID, db: Session = Depends(get_db), current_user: DBUser = Depends(get_current_user)):
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.organizer_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Only the organizer can delete this event")
    archive_event(db, event, reason='deleted_by_organizer')
    db.commit()
    return {"message": "Event archived"}


# ── Rate Host ─────────────────────────────────────────────────────────────────
# Works for BOTH active events and archived events

class RateHostRequest(BaseModel):
    rating: int
    comment: Optional[str] = None


@router.post("/sports-events/{event_id}/rate-host")
async def rate_host(
    event_id: UUID,
    body: RateHostRequest,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    # Try active events first
    organizer_id = None
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if event:
        organizer_id = event.organizer_id
    else:
        # Check archived events
        archived = db.query(DBArchivedEvent).filter(DBArchivedEvent.event_id == event_id).first()
        if not archived:
            raise HTTPException(status_code=404, detail="Event not found")
        organizer_id = archived.organizer_id
        # Verify user was a participant
        participant_ids = json.loads(archived.participant_ids) if archived.participant_ids else []
        if str(current_user.user_id) not in participant_ids:
            raise HTTPException(status_code=403, detail="You did not attend this event")

    if organizer_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="Cannot rate yourself")
    if body.rating < 1 or body.rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be 1-5")

    existing = db.query(DBHostRating).filter_by(
        event_id=event_id, rater_id=current_user.user_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already rated this host for this event")

    new_rating = DBHostRating(
        event_id=event_id,
        rater_id=current_user.user_id,
        host_id=organizer_id,
        rating=body.rating,
        comment=body.comment,
    )
    db.add(new_rating)

    # Update host average
    host = db.query(DBUser).filter(DBUser.user_id == organizer_id).first()
    if host:
        all_ratings = db.query(DBHostRating).filter(DBHostRating.host_id == host.user_id).all()
        total = len(all_ratings) + 1
        avg = (sum(r.rating for r in all_ratings) + body.rating) / total
        host.host_rating = round(avg, 2)
        host.total_ratings = total

    db.commit()
    return {
        "message": "Rating submitted",
        "new_average": float(host.host_rating) if host and host.host_rating else None,
        "total_ratings": host.total_ratings if host else 0,
    }


# ── Archive ───────────────────────────────────────────────────────────────────

@router.post("/sports-events/archive-expired")
async def archive_expired_events(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Archives events whose date has passed or whose end_time today has passed."""
    # Use local time consistently — date.today() is local, so now must be local too
    now = datetime.now()
    today = now.date()

    # All events with start_date before today
    expired = db.query(DBEvent).filter(DBEvent.start_date < today).all()

    # Also grab today's events where end_time has passed (with buffer)
    today_events = db.query(DBEvent).filter(DBEvent.start_date == today).all()
    for event in today_events:
        if event.end_time:
            # Archive 30 min after end_time
            event_end = datetime.combine(today, event.end_time) + timedelta(minutes=30)
            if now > event_end:
                expired.append(event)
        elif event.start_time:
            # No end_time: assume 3 hour duration + 30 min buffer
            assumed_end = datetime.combine(today, event.start_time) + timedelta(hours=3, minutes=30)
            if now > assumed_end:
                expired.append(event)

    # Deduplicate
    seen = set()
    unique_expired = []
    for e in expired:
        if e.event_id not in seen:
            seen.add(e.event_id)
            unique_expired.append(e)

    count = len(unique_expired)
    for event in unique_expired:
        archive_event(db, event, reason='expired')
    db.commit()
    return {"archived": count}


# ── Event History ─────────────────────────────────────────────────────────────

@router.get("/users/me/event-history")
async def get_event_history(current_user: DBUser = Depends(get_current_user), db: Session = Depends(get_db)):
    organized = db.query(DBArchivedEvent).filter(
        DBArchivedEvent.organizer_id == current_user.user_id
    ).order_by(DBArchivedEvent.start_date.desc()).limit(20).all()

    def serialize(e):
        return {
            "archive_id":       str(e.archive_id),
            "event_id":         str(e.event_id),
            "title":            e.title,
            "sport":            e.sport,
            "start_date":       str(e.start_date),
            "location":         e.location,
            "participant_count":e.participant_count,
            "archive_reason":   e.archive_reason,
        }
    return {"organized": [serialize(e) for e in organized]}


# ── Pending Ratings (unrated events user attended) ────────────────────────────

@router.get("/users/me/pending-ratings")
async def get_pending_ratings(
    current_user: DBUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns archived events the user attended but hasn't rated yet."""
    user_id_str = str(current_user.user_id)
    two_weeks_ago = date.today() - timedelta(days=14)

    # Get archived events from last 2 weeks
    archived = db.query(DBArchivedEvent).filter(
        DBArchivedEvent.start_date >= two_weeks_ago,
        DBArchivedEvent.archive_reason == 'expired',
    ).order_by(DBArchivedEvent.start_date.desc()).all()

    pending = []
    for event in archived:
        # Check if user was a participant
        participant_ids = json.loads(event.participant_ids) if event.participant_ids else []
        if user_id_str not in participant_ids:
            continue
        # Skip if user was the organizer
        if event.organizer_id == current_user.user_id:
            continue
        # Check if already rated
        already_rated = db.query(DBHostRating).filter_by(
            event_id=event.event_id, rater_id=current_user.user_id
        ).first()
        if already_rated:
            continue

        pending.append({
            "event_id":       str(event.event_id),
            "title":          event.title,
            "sport":          event.sport,
            "start_date":     str(event.start_date),
            "start_time":     str(event.start_time) if event.start_time else None,
            "location":       event.location,
            "organizer_id":   str(event.organizer_id) if event.organizer_id else None,
            "organizer_name": event.organizer_name,
        })

    return {"pending": pending}


# ── Recent Activity (past events from last 2 weeks) ──────────────────────────

@router.get("/users/me/recent-activity")
async def get_recent_activity(
    current_user: DBUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns past events the user attended in the last 2 weeks, with rating status."""
    user_id_str = str(current_user.user_id)
    two_weeks_ago = date.today() - timedelta(days=14)

    archived = db.query(DBArchivedEvent).filter(
        DBArchivedEvent.start_date >= two_weeks_ago,
    ).order_by(DBArchivedEvent.start_date.desc()).all()

    activities = []
    for event in archived:
        participant_ids = json.loads(event.participant_ids) if event.participant_ids else []
        if user_id_str not in participant_ids and event.organizer_id != current_user.user_id:
            continue

        is_organizer = (event.organizer_id == current_user.user_id)

        # Check if user rated this event's host
        my_rating = None
        if not is_organizer:
            rating = db.query(DBHostRating).filter_by(
                event_id=event.event_id, rater_id=current_user.user_id
            ).first()
            if rating:
                my_rating = rating.rating

        activities.append({
            "event_id":       str(event.event_id),
            "title":          event.title,
            "sport":          event.sport,
            "start_date":     str(event.start_date),
            "start_time":     str(event.start_time) if event.start_time else None,
            "location":       event.location,
            "organizer_name": event.organizer_name,
            "is_organizer":   is_organizer,
            "my_rating":      my_rating,      # null = not yet rated, int = already rated
            "can_rate":       not is_organizer and my_rating is None,
        })

    return {"activities": activities}


# ── Public Recent Activity (for viewing other profiles) ───────────────────────

@router.get("/users/{user_id}/recent-activity")
async def get_user_recent_activity(user_id: UUID, db: Session = Depends(get_db)):
    """Public view of a user's recent events — no rating info shown."""
    user_id_str = str(user_id)
    two_weeks_ago = date.today() - timedelta(days=14)

    archived = db.query(DBArchivedEvent).filter(
        DBArchivedEvent.start_date >= two_weeks_ago,
    ).order_by(DBArchivedEvent.start_date.desc()).all()

    activities = []
    for event in archived:
        participant_ids = json.loads(event.participant_ids) if event.participant_ids else []
        if user_id_str not in participant_ids and str(event.organizer_id) != user_id_str:
            continue

        activities.append({
            "event_id":       str(event.event_id),
            "title":          event.title,
            "sport":          event.sport,
            "start_date":     str(event.start_date),
            "location":       event.location,
            "is_organizer":   str(event.organizer_id) == user_id_str,
        })

    return {"activities": activities}


# ── Public User Profile ──────────────────────────────────────────────────────

@router.get("/users/{user_id}/profile")
async def get_user_profile(user_id: UUID, db: Session = Depends(get_db)):
    """Public profile for any user."""
    user = db.query(DBUser).filter(DBUser.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "user_id":       str(user.user_id),
        "first_name":    user.first_name,
        "last_name":     user.last_name,
        "bio":           user.bio,
        "sports":        user.sports,
        "avatar_config": user.avatar_config,
        "avatar_photo":  user.avatar_photo,
        "banner_photo":  user.banner_photo,
        "host_rating":   float(user.host_rating) if user.host_rating else None,
        "total_ratings": user.total_ratings or 0,
        "nationality":   user.nationality,
        "instagram":     user.instagram,
        "facebook":      user.facebook,
    }


@router.get("/users/{user_id}/reviews")
async def get_user_reviews(user_id: UUID, db: Session = Depends(get_db)):
    """Returns all host ratings received by this user, with reviewer info."""
    ratings = (
        db.query(DBHostRating)
        .filter(DBHostRating.host_id == user_id)
        .order_by(DBHostRating.created_at.desc())
        .limit(50)
        .all()
    )
    reviews = []
    for r in ratings:
        reviewer = db.query(DBUser).filter(DBUser.user_id == r.rater_id).first()
        reviews.append({
            "rating_id":      str(r.rating_id),
            "rating":         r.rating,
            "comment":        r.comment,
            "created_at":     r.created_at.isoformat() if r.created_at else None,
            "reviewer_name":  f"{reviewer.first_name or ''} {reviewer.last_name or ''}".strip() if reviewer else "Unknown",
            "reviewer_photo": reviewer.avatar_photo if reviewer else None,
            "reviewer_avatar":reviewer.avatar_config if reviewer else None,
        })
    return {"reviews": reviews}


# ── Report ───────────────────────────────────────────────────────────────────

class ReportRequest(BaseModel):
    target_type: str   # 'event' or 'user'
    target_id: str
    reason: str        # 'spam','harassment','inappropriate','safety','other'
    details: Optional[str] = None


@router.post("/reports")
async def create_report(
    body: ReportRequest,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    valid_reasons = {'spam', 'harassment', 'inappropriate', 'safety', 'other'}
    if body.reason not in valid_reasons:
        raise HTTPException(status_code=400, detail=f"Reason must be one of: {', '.join(valid_reasons)}")
    if body.target_type not in ('event', 'user'):
        raise HTTPException(status_code=400, detail="target_type must be 'event' or 'user'")

    # Prevent duplicate reports
    existing = db.query(DBReport).filter_by(
        reporter_id=current_user.user_id,
        target_type=body.target_type,
        target_id=UUID(body.target_id),
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="You already reported this")

    report = DBReport(
        reporter_id=current_user.user_id,
        target_type=body.target_type,
        target_id=UUID(body.target_id),
        reason=body.reason,
        details=body.details,
    )
    db.add(report)
    db.commit()
    return {"message": "Report submitted. We'll review it shortly."}


# ── Block / Unblock ──────────────────────────────────────────────────────────

@router.post("/users/{user_id}/block")
async def block_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    if user_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="Cannot block yourself")

    target = db.query(DBUser).filter(DBUser.user_id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    existing = db.query(DBBlock).filter_by(
        blocker_id=current_user.user_id, blocked_id=user_id
    ).first()
    if existing:
        return {"message": "Already blocked"}

    db.add(DBBlock(blocker_id=current_user.user_id, blocked_id=user_id))

    # Also remove them from any of your events
    my_events = db.query(DBEvent).filter(DBEvent.organizer_id == current_user.user_id).all()
    for event in my_events:
        part = db.query(DBEventParticipant).filter_by(
            event_id=event.event_id, user_id=user_id
        ).first()
        if part:
            db.delete(part)

    db.commit()
    return {"message": "User blocked"}


@router.delete("/users/{user_id}/block")
async def unblock_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    block = db.query(DBBlock).filter_by(
        blocker_id=current_user.user_id, blocked_id=user_id
    ).first()
    if not block:
        raise HTTPException(status_code=404, detail="User is not blocked")
    db.delete(block)
    db.commit()
    return {"message": "User unblocked"}


@router.get("/users/me/blocked")
async def get_blocked_users(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    blocks = db.query(DBBlock).filter(DBBlock.blocker_id == current_user.user_id).all()
    blocked = []
    for b in blocks:
        user = db.query(DBUser).filter(DBUser.user_id == b.blocked_id).first()
        if user:
            blocked.append({
                "user_id": str(user.user_id),
                "first_name": user.first_name,
                "last_name": user.last_name,
                "avatar_photo": user.avatar_photo,
            })
    return {"blocked": blocked}


# ── Share Preview (Open Graph) ────────────────────────────────────────────────

SPORT_EMOJIS = {
    "soccer": "⚽", "basketball": "🏀", "tennis": "🎾", "volleyball": "🏐",
    "pickleball": "🏓", "baseball": "⚾", "football": "🏈", "handball": "🤾",
    "softball": "🥎", "dodgeball": "🎯", "kickball": "⚽",
}

@router.get("/events/{event_id}/share", response_class=HTMLResponse)
async def share_preview(event_id: UUID, db: Session = Depends(get_db)):
    """
    Serves an HTML page with Open Graph meta tags.
    When shared on iMessage / WhatsApp / social media, platforms scrape these
    tags to generate a rich link preview with title, description, and image.
    """
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if not event:
        return HTMLResponse("<html><body><h1>Event not found</h1></body></html>", status_code=404)

    emoji = SPORT_EMOJIS.get(event.sport.lower(), "🏅")
    sport_cap = event.sport.capitalize()

    # Format date & time
    date_str = event.start_date.strftime("%a, %b %d") if event.start_date else ""
    time_str = ""
    if event.start_time:
        h, m = event.start_time.hour, event.start_time.minute
        time_str = f"{h % 12 or 12}:{m:02d} {'PM' if h >= 12 else 'AM'}"

    participant_count = db.query(DBEventParticipant).filter_by(event_id=event.event_id).count()
    spots_left = max(0, event.max_players - participant_count)

    description = f"{emoji} {sport_cap} · {date_str} · {time_str}\n📍 {event.location}\n👥 {spots_left} spots left"
    cost_str = "Free" if not event.cost or float(event.cost) == 0 else f"${event.cost}"

    og_title = f"{event.title} — {sport_cap}"
    og_desc = f"{date_str} at {time_str} · {event.location} · {cost_str} · {spots_left}/{event.max_players} spots open"

    # Static map image from Google Maps (provides the preview thumbnail)
    maps_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    og_image = ""
    if event.latitude and event.longitude and maps_key:
        og_image = (
            f"https://maps.googleapis.com/maps/api/staticmap"
            f"?center={event.latitude},{event.longitude}"
            f"&zoom=14&size=600x314&scale=2"
            f"&markers=color:green%7C{event.latitude},{event.longitude}"
            f"&key={maps_key}"
        )

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta property="og:type" content="website" />
    <meta property="og:title" content="{og_title}" />
    <meta property="og:description" content="{og_desc}" />
    {"<meta property='og:image' content='" + og_image + "' />" if og_image else ""}
    <meta property="og:image:width" content="1200" />
    <meta property="og:image:height" content="628" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="{og_title}" />
    <meta name="twitter:description" content="{og_desc}" />
    {"<meta name='twitter:image' content='" + og_image + "' />" if og_image else ""}
    <title>{og_title}</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; margin: 0; padding: 40px 20px; background: #f8f9fb; text-align: center; }}
        .card {{ max-width: 420px; margin: 0 auto; background: #fff; border-radius: 16px; padding: 32px 24px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }}
        .emoji {{ font-size: 48px; margin-bottom: 12px; }}
        h1 {{ font-size: 22px; color: #1a1a2e; margin: 0 0 8px; }}
        .sport {{ color: #16a34a; font-weight: 700; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }}
        .detail {{ color: #666; margin: 6px 0; font-size: 15px; }}
        .spots {{ background: #e8f5e9; color: #16a34a; font-weight: 700; padding: 8px 16px; border-radius: 8px; display: inline-block; margin-top: 16px; }}
        .cta {{ display: inline-block; margin-top: 20px; background: #16a34a; color: #fff; text-decoration: none; padding: 14px 32px; border-radius: 12px; font-weight: 700; font-size: 16px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="emoji">{emoji}</div>
        <p class="sport">{sport_cap}</p>
        <h1>{event.title}</h1>
        <p class="detail">📅 {date_str} · {time_str}</p>
        <p class="detail">📍 {event.location}</p>
        <p class="detail">💰 {cost_str}</p>
        <div class="spots">👥 {spots_left} of {event.max_players} spots open</div>
        <br>
        <a class="cta" href="#">Open in Game Radar</a>
    </div>
</body>
</html>"""
    return HTMLResponse(html)


# ── Bookmarks (Save Events) ──────────────────────────────────────────────────

@router.post("/sports-events/{event_id}/bookmark")
async def bookmark_event(
    event_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    existing = db.query(DBBookmark).filter_by(event_id=event_id, user_id=current_user.user_id).first()
    if existing:
        return {"status": "already_bookmarked"}
    db.add(DBBookmark(event_id=event_id, user_id=current_user.user_id))
    db.commit()
    return {"status": "bookmarked"}


@router.delete("/sports-events/{event_id}/bookmark")
async def remove_bookmark(
    event_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    bookmark = db.query(DBBookmark).filter_by(event_id=event_id, user_id=current_user.user_id).first()
    if bookmark:
        db.delete(bookmark)
        db.commit()
    return {"status": "removed"}


@router.get("/users/me/bookmarks", response_model=list[Event])
async def get_bookmarks(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    bookmark_ids = (
        db.query(DBBookmark.event_id)
        .filter(DBBookmark.user_id == current_user.user_id)
        .all()
    )
    event_ids = [b[0] for b in bookmark_ids]
    if not event_ids:
        return []

    events = (
        db.query(DBEvent)
        .filter(DBEvent.event_id.in_(event_ids), DBEvent.status == "active")
        .order_by(DBEvent.start_date)
        .all()
    )
    return [enrich_event(e, db, current_user.user_id) for e in events]


# ── Player Stats ──────────────────────────────────────────────────────────────

@router.get("/users/me/stats")
async def get_my_stats(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    # Events joined (as participant)
    events_joined = (
        db.query(DBEventParticipant)
        .filter(DBEventParticipant.user_id == current_user.user_id)
        .count()
    )

    # Events hosted (as organizer)
    events_hosted = (
        db.query(DBEvent)
        .filter(DBEvent.organizer_id == current_user.user_id)
        .count()
    )
    archived_hosted = (
        db.query(DBArchivedEvent)
        .filter(DBArchivedEvent.organizer_id == current_user.user_id)
        .count()
    )

    # Archived events participated in
    archived_joined = (
        db.query(DBArchivedEvent)
        .filter(DBArchivedEvent.participant_ids.contains(str(current_user.user_id)))
        .count()
    )

    # Top sport (most events joined/hosted)
    # Count from active events where user is participant or organizer
    sport_counts = {}
    participated_events = (
        db.query(DBEvent.sport)
        .join(DBEventParticipant, DBEvent.event_id == DBEventParticipant.event_id)
        .filter(DBEventParticipant.user_id == current_user.user_id)
        .all()
    )
    for (sport,) in participated_events:
        sport_counts[sport] = sport_counts.get(sport, 0) + 1

    hosted_events = (
        db.query(DBEvent.sport)
        .filter(DBEvent.organizer_id == current_user.user_id)
        .all()
    )
    for (sport,) in hosted_events:
        sport_counts[sport] = sport_counts.get(sport, 0) + 1

    top_sport = max(sport_counts, key=sport_counts.get) if sport_counts else None

    return {
        "events_joined": events_joined + archived_joined,
        "events_hosted": events_hosted + archived_hosted,
        "host_rating": float(current_user.host_rating) if current_user.host_rating else None,
        "total_ratings": current_user.total_ratings or 0,
        "top_sport": top_sport,
    }


@router.get("/users/{user_id}/stats")
async def get_user_stats(user_id: UUID, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    events_joined = (
        db.query(DBEventParticipant)
        .filter(DBEventParticipant.user_id == user_id)
        .count()
    )
    events_hosted = (
        db.query(DBEvent)
        .filter(DBEvent.organizer_id == user_id)
        .count()
    )
    archived_hosted = (
        db.query(DBArchivedEvent)
        .filter(DBArchivedEvent.organizer_id == user_id)
        .count()
    )

    return {
        "events_joined": events_joined,
        "events_hosted": events_hosted + archived_hosted,
        "host_rating": float(user.host_rating) if user.host_rating else None,
        "total_ratings": user.total_ratings or 0,
    }