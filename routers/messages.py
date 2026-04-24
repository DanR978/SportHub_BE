"""
Messaging: 1:1 DMs, ad-hoc group chats, and per-event chats.

Design notes
------------
* A DM is a conversation with exactly two members; we dedupe by finding the
  existing conversation shared between the two users.
* An event chat is auto-created the first time anyone opens an event's chat,
  seeded with the current roster.
* Unread = messages created_at > member.last_read_at. Read markers are bumped
  when the client fetches the message list.
* We use long-polling-free REST for now — clients should poll the
  `/conversations` list and the active `/messages` endpoint. A WebSocket
  upgrade is a natural next step.
"""
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy import and_, func, select

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from auth import get_current_user
from models.db_user import DBUser
from models.db_event import DBEvent
from models.db_event_participant import DBEventParticipant
from models.db_block import DBBlock
from models.db_post import DBPost
from models.db_conversation import DBConversation, DBConversationMember
from models.db_message import DBMessage


router = APIRouter(prefix="/messaging", tags=["messaging"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class DMStartBody(BaseModel):
    recipient_id: UUID


class GroupStartBody(BaseModel):
    member_ids: List[UUID]
    title: Optional[str] = None


class SendMessageBody(BaseModel):
    body: Optional[str] = None
    kind: str = "text"
    shared_post_id: Optional[UUID] = None
    shared_event_id: Optional[UUID] = None
    image_url: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_member(db: Session, conversation_id: UUID, user_id: UUID):
    row = db.query(DBConversationMember).filter_by(
        conversation_id=conversation_id, user_id=user_id
    ).first()
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this conversation")
    return row


def _blocked_between(db: Session, a: UUID, b: UUID) -> bool:
    return db.query(DBBlock).filter(
        ((DBBlock.blocker_id == a) & (DBBlock.blocked_id == b)) |
        ((DBBlock.blocker_id == b) & (DBBlock.blocked_id == a))
    ).first() is not None


def _user_summary(user: Optional[DBUser]) -> dict:
    if not user:
        return {"user_id": None, "name": "Unknown", "avatar_photo": None}
    return {
        "user_id": str(user.user_id),
        "name": f"{user.first_name or ''} {user.last_name or ''}".strip() or "Player",
        "avatar_photo": user.avatar_photo,
        "avatar_config": user.avatar_config,
    }


def _serialize_message(m: DBMessage, db: Session) -> dict:
    sender = db.query(DBUser).filter(DBUser.user_id == m.sender_id).first()
    payload = {
        "message_id":      str(m.message_id),
        "conversation_id": str(m.conversation_id),
        "sender":          _user_summary(sender),
        "kind":            m.kind,
        "body":            m.body,
        "image_url":       m.image_url,
        "created_at":      m.created_at.isoformat() if m.created_at else None,
    }
    if m.shared_post_id:
        post = db.query(DBPost).filter(DBPost.post_id == m.shared_post_id).first()
        if post:
            post_author = db.query(DBUser).filter(DBUser.user_id == post.author_id).first()
            payload["shared_post"] = {
                "post_id":   str(post.post_id),
                "author":    _user_summary(post_author),
                "body":      post.body[:200],
                "image_url": post.image_url,
                "sport":     post.sport,
            }
    if m.shared_event_id:
        ev = db.query(DBEvent).filter(DBEvent.event_id == m.shared_event_id).first()
        if ev:
            payload["shared_event"] = {
                "event_id":   str(ev.event_id),
                "title":      ev.title,
                "sport":      ev.sport,
                "start_date": str(ev.start_date) if ev.start_date else None,
                "location":   ev.location,
            }
    return payload


def _serialize_conversation(conv: DBConversation, db: Session, viewer_id: UUID) -> dict:
    members = (
        db.query(DBConversationMember)
        .filter(DBConversationMember.conversation_id == conv.conversation_id)
        .all()
    )
    users = []
    my_last_read = None
    for m in members:
        u = db.query(DBUser).filter(DBUser.user_id == m.user_id).first()
        users.append(_user_summary(u))
        if m.user_id == viewer_id:
            my_last_read = m.last_read_at

    last_msg = (
        db.query(DBMessage)
        .filter(DBMessage.conversation_id == conv.conversation_id)
        .order_by(DBMessage.created_at.desc())
        .first()
    )

    unread = 0
    if my_last_read is not None:
        unread = (
            db.query(DBMessage)
            .filter(
                DBMessage.conversation_id == conv.conversation_id,
                DBMessage.created_at > my_last_read,
                DBMessage.sender_id != viewer_id,
            )
            .count()
        )

    title = conv.title
    if not title:
        if conv.kind == "direct":
            other = next((u for u in users if u["user_id"] != str(viewer_id)), None)
            title = other["name"] if other else "Direct message"
        elif conv.kind == "event" and conv.event_id:
            ev = db.query(DBEvent).filter(DBEvent.event_id == conv.event_id).first()
            title = ev.title if ev else "Event chat"
        else:
            names = [u["name"].split()[0] for u in users if u["user_id"] != str(viewer_id)]
            title = ", ".join(names[:3]) or "Group chat"

    return {
        "conversation_id": str(conv.conversation_id),
        "kind":            conv.kind,
        "title":           title,
        "event_id":        str(conv.event_id) if conv.event_id else None,
        "members":         users,
        "unread_count":    unread,
        "last_message":    _serialize_message(last_msg, db) if last_msg else None,
        "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
    }


def _find_direct_conversation(db: Session, a: UUID, b: UUID) -> Optional[DBConversation]:
    # Fast path: any 'direct' conversation where both a and b are members and
    # there are exactly 2 members.
    candidates = (
        db.query(DBConversation)
        .join(DBConversationMember, DBConversation.conversation_id == DBConversationMember.conversation_id)
        .filter(DBConversation.kind == "direct", DBConversationMember.user_id == a)
        .all()
    )
    for conv in candidates:
        members = db.query(DBConversationMember).filter(
            DBConversationMember.conversation_id == conv.conversation_id
        ).all()
        member_ids = {m.user_id for m in members}
        if member_ids == {a, b}:
            return conv
    return None


# ── Conversation endpoints ────────────────────────────────────────────────────

@router.get("/conversations")
def list_conversations(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    rows = (
        db.query(DBConversation)
        .join(DBConversationMember, DBConversation.conversation_id == DBConversationMember.conversation_id)
        .filter(DBConversationMember.user_id == current_user.user_id)
        .order_by(DBConversation.last_message_at.desc())
        .all()
    )
    return {"conversations": [_serialize_conversation(c, db, current_user.user_id) for c in rows]}


@router.post("/direct")
def start_direct(
    body: DMStartBody,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    if body.recipient_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="Can't DM yourself")
    target = db.query(DBUser).filter(DBUser.user_id == body.recipient_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if _blocked_between(db, current_user.user_id, body.recipient_id):
        raise HTTPException(status_code=403, detail="Messaging is unavailable between these users")

    existing = _find_direct_conversation(db, current_user.user_id, body.recipient_id)
    if existing:
        return _serialize_conversation(existing, db, current_user.user_id)

    conv = DBConversation(kind="direct", created_by=current_user.user_id)
    db.add(conv)
    db.flush()
    db.add(DBConversationMember(conversation_id=conv.conversation_id, user_id=current_user.user_id))
    db.add(DBConversationMember(conversation_id=conv.conversation_id, user_id=body.recipient_id))
    db.commit()
    db.refresh(conv)
    return _serialize_conversation(conv, db, current_user.user_id)


@router.post("/group")
def start_group(
    body: GroupStartBody,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    member_ids = list({*body.member_ids, current_user.user_id})
    if len(member_ids) < 2:
        raise HTTPException(status_code=400, detail="Need at least one other member")

    conv = DBConversation(kind="group", title=body.title, created_by=current_user.user_id)
    db.add(conv)
    db.flush()
    for uid in member_ids:
        db.add(DBConversationMember(conversation_id=conv.conversation_id, user_id=uid))
    db.commit()
    db.refresh(conv)
    return _serialize_conversation(conv, db, current_user.user_id)


@router.post("/event/{event_id}/open")
def open_event_chat(
    event_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """
    Lazily creates (or returns) the group chat for an event and makes sure
    the caller is a member (they must be a participant of the event).
    """
    event = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    is_participant = db.query(DBEventParticipant).filter_by(
        event_id=event_id, user_id=current_user.user_id
    ).first() is not None
    if not is_participant and event.organizer_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Join this event to access its chat")

    conv = db.query(DBConversation).filter_by(kind="event", event_id=event_id).first()
    if not conv:
        conv = DBConversation(kind="event", event_id=event_id, title=event.title, created_by=event.organizer_id)
        db.add(conv)
        db.flush()
        # Seed the member list with every current participant
        participants = db.query(DBEventParticipant).filter_by(event_id=event_id).all()
        seen = set()
        for p in participants:
            if p.user_id in seen:
                continue
            db.add(DBConversationMember(conversation_id=conv.conversation_id, user_id=p.user_id))
            seen.add(p.user_id)
        if event.organizer_id and event.organizer_id not in seen:
            db.add(DBConversationMember(conversation_id=conv.conversation_id, user_id=event.organizer_id))
        db.commit()
        db.refresh(conv)
    else:
        # Make sure current user is added (e.g. they joined after chat was made)
        existing = db.query(DBConversationMember).filter_by(
            conversation_id=conv.conversation_id, user_id=current_user.user_id
        ).first()
        if not existing:
            db.add(DBConversationMember(conversation_id=conv.conversation_id, user_id=current_user.user_id))
            db.commit()

    return _serialize_conversation(conv, db, current_user.user_id)


# ── Messages ─────────────────────────────────────────────────────────────────

@router.get("/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    before: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    member = _ensure_member(db, conversation_id, current_user.user_id)

    q = db.query(DBMessage).filter(DBMessage.conversation_id == conversation_id)
    if before:
        q = q.filter(DBMessage.created_at < before)
    msgs = q.order_by(DBMessage.created_at.desc()).limit(limit).all()
    msgs.reverse()  # chronological

    # Bump read marker
    member.last_read_at = datetime.now(timezone.utc)
    db.commit()

    return {"messages": [_serialize_message(m, db) for m in msgs]}


@router.post("/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: UUID,
    body: SendMessageBody,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    _ensure_member(db, conversation_id, current_user.user_id)

    kind = (body.kind or "text").strip()
    if kind not in ("text", "post_share", "event_share", "image"):
        raise HTTPException(status_code=400, detail="Unknown message kind")

    if kind == "text" and (not body.body or not body.body.strip()):
        raise HTTPException(status_code=400, detail="Message body required")
    if body.body and len(body.body) > 2000:
        raise HTTPException(status_code=400, detail="Message is too long")

    msg = DBMessage(
        conversation_id = conversation_id,
        sender_id       = current_user.user_id,
        kind            = kind,
        body            = body.body.strip() if body.body else None,
        shared_post_id  = body.shared_post_id,
        shared_event_id = body.shared_event_id,
        image_url       = body.image_url,
    )
    db.add(msg)

    conv = db.query(DBConversation).filter(DBConversation.conversation_id == conversation_id).first()
    if conv:
        conv.last_message_at = datetime.now(timezone.utc)

    # Bump the sender's own read marker — they've "read" their own message
    sender_member = db.query(DBConversationMember).filter_by(
        conversation_id=conversation_id, user_id=current_user.user_id
    ).first()
    if sender_member:
        sender_member.last_read_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(msg)

    # Fire-and-forget push to every other member
    try:
        from notifications import send_push
        preview = (body.body or {
            "post_share":  "shared a post",
            "event_share": "shared an event",
            "image":       "sent a photo",
        }.get(kind, "sent a message"))[:140]
        sender_name = f"{current_user.first_name or ''} {current_user.last_name or ''}".strip() or "New message"
        members = db.query(DBConversationMember).filter(
            DBConversationMember.conversation_id == conversation_id,
            DBConversationMember.user_id != current_user.user_id,
        ).all()
        for m in members:
            send_push(
                db,
                m.user_id,
                title=sender_name,
                body=preview,
                data={"conversation_id": str(conversation_id), "type": "new_message"},
            )
    except Exception:
        # Push is best-effort; never block the send
        pass

    return _serialize_message(msg, db)


@router.post("/conversations/{conversation_id}/read")
def mark_read(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    member = _ensure_member(db, conversation_id, current_user.user_id)
    member.last_read_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "read"}


@router.get("/unread-summary")
def unread_summary(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Total unread message count across all conversations (for tab badges)."""
    members = db.query(DBConversationMember).filter(
        DBConversationMember.user_id == current_user.user_id
    ).all()
    total = 0
    for m in members:
        total += (
            db.query(DBMessage)
            .filter(
                DBMessage.conversation_id == m.conversation_id,
                DBMessage.created_at > m.last_read_at,
                DBMessage.sender_id != current_user.user_id,
            )
            .count()
        )
    return {"unread": total}
