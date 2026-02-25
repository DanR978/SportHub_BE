from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException
from models.db_event_participant import DBEventParticipant
from models.event import Event, EventCreate
from sqlalchemy.orm import Session
from database import get_db
from models.db_event import DBEvent
from auth import get_current_user
from models.db_user import DBUser
from typing import List, Optional
from datetime import date, datetime, timedelta
from geopy.geocoders import Nominatim
from better_profanity import profanity
from geopy.distance import geodesic
from models.db_host_rating import DBHostRating

router = APIRouter()
geolocator = Nominatim(user_agent="sporthub")


@router.post("/sports-events", response_model=Event)
async def create_event(event: EventCreate, db: Session = Depends(get_db), current_user: DBUser = Depends(get_current_user)):
    geo = geolocator.geocode(event.location)
    lat = geo.latitude if geo else 0.0
    lng = geo.longitude if geo else 0.0

    if profanity.contains_profanity(event.title) or profanity.contains_profanity(event.description or ""):
        raise HTTPException(status_code=400, detail="Content contains inappropriate language")
    
    if event.end_time:
        start_dt = datetime.combine(date.today(), event.start_time)
        end_dt   = datetime.combine(date.today(), event.end_time)
        if end_dt < start_dt:
            end_dt += timedelta(days=1)
        if (end_dt - start_dt) > timedelta(hours=6):
            raise HTTPException(status_code=400, detail="Events cannot last more than 6 hours")

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
    new_event.participant_count = 0
    return new_event


@router.get("/sports-events", response_model=list[Event])
async def get_events(db: Session = Depends(get_db), current_user: DBUser = Depends(get_current_user)):
    events = db.query(DBEvent).all()
    for event in events:
        event.participant_count = db.query(DBEventParticipant).filter_by(event_id=event.event_id).count()
        joined = db.query(DBEventParticipant).filter_by(event_id=event.event_id, user_id=current_user.user_id).first()
        event.joined = True if joined else False
    return events


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
    current_user: DBUser = Depends(get_current_user),  # >>> ADDED
):
    query = db.query(DBEvent)

    if sports:
        sports = [sport.lower() for sport in sports]
        query = query.filter(DBEvent.sport.in_(sports))
    if experience_levels:
        experience_levels = [level.lower() for level in experience_levels]
        query = query.filter(DBEvent.experience_level.in_(experience_levels))
    if start_from:
        query = query.filter(DBEvent.start_date >= start_from)
    if date_to:
        query = query.filter(DBEvent.start_date <= date_to)

    events = query.all()

    if latitude and longitude:
        nearby = []
        for event in events:
            if event.latitude and event.longitude:
                distance = geodesic((latitude, longitude), (event.latitude, event.longitude)).miles
                if distance <= radius_miles:
                    event.participant_count = db.query(DBEventParticipant).filter_by(event_id=event.event_id).count()
                    joined = db.query(DBEventParticipant).filter_by(event_id=event.event_id, user_id=current_user.user_id).first()  # >>> ADDED
                    event.joined = True if joined else False  # >>> ADDED
                    nearby.append(event)
        return nearby

    for event in events:
        event.participant_count = db.query(DBEventParticipant).filter_by(event_id=event.event_id).count()
        joined = db.query(DBEventParticipant).filter_by(event_id=event.event_id, user_id=current_user.user_id).first()  # >>> ADDED
        event.joined = True if joined else False

    return events


@router.get("/sports-events/{event_id}", response_model=Event)
async def get_event(event_id: UUID, db: Session = Depends(get_db), current_user: DBUser = Depends(get_current_user)):
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    participants = db.query(DBEventParticipant).filter_by(event_id=event_id).all()
    event.participant_count = len(participants)
    event.joined = any(p.user_id == current_user.user_id for p in participants)
    
    # Attach organizer info
    organizer = db.query(DBUser).filter(DBUser.user_id == event.organizer_id).first()
    if organizer:
        event.organizer_name = f"{organizer.first_name} {organizer.last_name}"
        event.organizer_avatar = organizer.avatar_config
        event.organizer_photo = organizer.avatar_photo
        event.host_rating      = float(organizer.host_rating) if organizer.host_rating else None
        event.total_ratings    = organizer.total_ratings or 0
    return event


@router.post("/sports-events/{event_id}/join")
async def join_event(event_id: UUID, db: Session = Depends(get_db), current_user: DBUser = Depends(get_current_user)):
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.status != 'active':
        raise HTTPException(status_code=400, detail="Event is not active")

    already_joined = db.query(DBEventParticipant).filter_by(event_id=event_id, user_id=current_user.user_id).first()
    if already_joined:
        raise HTTPException(status_code=400, detail="Already joined this event")

    count = db.query(DBEventParticipant).filter_by(event_id=event_id).count()
    if count >= event.max_players:
        raise HTTPException(status_code=400, detail="Event is full")

    db.add(DBEventParticipant(event_id=event_id, user_id=current_user.user_id))
    db.commit()
    return {"message": "Joined event successfully"}


@router.delete("/sports-events/{event_id}")
async def delete_event(event_id: UUID, db: Session = Depends(get_db), current_user: DBUser = Depends(get_current_user)):
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.organizer_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Only the organizer can delete this event")
    db.delete(event)
    db.commit()
    return {"message": "Event deleted"}

@router.post("/sports-events/{event_id}/rate")
async def rate_host(
    event_id: UUID,
    rating: int = Query(..., ge=1, le=5),
    comment: str = Query(default=None),
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    participant = db.query(DBEventParticipant).filter_by(
        event_id=event_id, user_id=current_user.user_id
    ).first()
    if not participant:
        raise HTTPException(status_code=403, detail="You must have joined this event to rate the host")

    if event.organizer_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="You cannot rate yourself")

    event_dt = datetime.combine(event.start_date, event.start_time)
    if datetime.now() < event_dt:
        raise HTTPException(status_code=400, detail="You can only rate after the event has started")

    existing = db.query(DBHostRating).filter_by(
        event_id=event_id, rater_id=current_user.user_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="You have already rated this host for this event")

    db.add(DBHostRating(
        event_id=event_id,
        rater_id=current_user.user_id,
        host_id=event.organizer_id,
        rating=rating,
        comment=comment,
    ))

    host = db.query(DBUser).filter(DBUser.user_id == event.organizer_id).first()
    if host:
        all_ratings = db.query(DBHostRating).filter_by(host_id=event.organizer_id).all()
        total = len(all_ratings) + 1
        avg = (sum(r.rating for r in all_ratings) + rating) / total
        host.host_rating = round(avg, 2)
        host.total_ratings = total

    db.commit()
    return {"message": "Rating submitted", "new_average": round(avg, 2), "total_ratings": total}