"""
Scheduled background jobs. Started from main.py's lifespan handler.

- archive_expired_events: runs every 15 min, moves past events to archive
- send_event_reminders: runs every 5 min, pushes "starts in ~1hr" notifications

Each job opens its own DB session (APScheduler runs outside request scope).
Failures are logged but never propagate, so one bad run doesn't kill the scheduler.
"""
import json
import logging
from datetime import date, datetime, timedelta, timezone

from database import SessionLocal
from models.db_archived_event import DBArchivedEvent
from models.db_event import DBEvent
from models.db_event_participant import DBEventParticipant
from models.db_user import DBUser
from notifications import send_push

logger = logging.getLogger(__name__)

# Track events we've already sent a reminder for, in-memory (lost on restart).
# For MVP this is acceptable — duplicate reminders within a single process are
# prevented, and worst case on restart a user gets one extra ping. A persistent
# flag column can replace this when we scale beyond one process.
_reminded_event_ids: set = set()


def archive_expired_events() -> None:
    db = SessionLocal()
    try:
        now = datetime.now()
        today = now.date()

        expired = list(db.query(DBEvent).filter(DBEvent.start_date < today).all())

        for event in db.query(DBEvent).filter(DBEvent.start_date == today).all():
            if event.end_time:
                event_end = datetime.combine(today, event.end_time) + timedelta(minutes=30)
                if now > event_end:
                    expired.append(event)
            elif event.start_time:
                assumed_end = datetime.combine(today, event.start_time) + timedelta(hours=3, minutes=30)
                if now > assumed_end:
                    expired.append(event)

        seen = set()
        for event in expired:
            if event.event_id in seen:
                continue
            seen.add(event.event_id)
            _archive_one(db, event, 'expired')

        db.commit()
        if seen:
            logger.info("Archived %d expired events", len(seen))
    except Exception:
        logger.exception("archive_expired_events failed")
        db.rollback()
    finally:
        db.close()


def _archive_one(db, event, reason: str) -> None:
    participants = db.query(DBEventParticipant).filter_by(event_id=event.event_id).all()
    organizer = (
        db.query(DBUser).filter(DBUser.user_id == event.organizer_id).first()
        if event.organizer_id
        else None
    )
    archived = DBArchivedEvent(
        event_id=event.event_id,
        title=event.title,
        sport=event.sport,
        organizer_id=event.organizer_id,
        organizer_name=f"{organizer.first_name} {organizer.last_name}" if organizer else None,
        location=event.location,
        start_date=event.start_date,
        start_time=event.start_time,
        end_time=event.end_time,
        max_players=event.max_players,
        participant_count=len(participants),
        experience_level=event.experience_level,
        cost=event.cost,
        description=event.description,
        archive_reason=reason,
        participant_ids=json.dumps([str(p.user_id) for p in participants]),
    )
    db.add(archived)
    db.query(DBEventParticipant).filter(DBEventParticipant.event_id == event.event_id).delete()
    db.delete(event)


def send_event_reminders() -> None:
    """Push a reminder ~1 hour before each upcoming event starts.

    We look for events whose start_time is between 55 and 65 minutes from now,
    and send once per event (tracked in-memory).
    """
    db = SessionLocal()
    try:
        now = datetime.now()
        today = now.date()
        window_start = now + timedelta(minutes=55)
        window_end = now + timedelta(minutes=65)

        # Only today's events can start in the next hour
        todays = db.query(DBEvent).filter(
            DBEvent.start_date == today,
            DBEvent.status == 'active',
        ).all()

        sent = 0
        for event in todays:
            if event.event_id in _reminded_event_ids or not event.start_time:
                continue
            event_dt = datetime.combine(today, event.start_time)
            if not (window_start <= event_dt <= window_end):
                continue

            participant_rows = db.query(DBEventParticipant).filter_by(event_id=event.event_id).all()
            for row in participant_rows:
                send_push(
                    db,
                    row.user_id,
                    title="Event starts in 1 hour",
                    body=f'"{event.title}" at {event.location}',
                    data={"event_id": str(event.event_id), "type": "event_reminder"},
                )
            _reminded_event_ids.add(event.event_id)
            sent += 1

        if sent:
            logger.info("Sent reminders for %d events", sent)
    except Exception:
        logger.exception("send_event_reminders failed")
        db.rollback()
    finally:
        db.close()
