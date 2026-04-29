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
* Group chats have admins (`conversation_members.is_admin`). Admins can rename,
  add/remove members, and promote/demote. Direct + event chats ignore the flag
  (event chats are moderated by the event organizer at the app layer).
* The conversation list and incremental message endpoints are tuned for the
  hot polling path: bulk-fetch users, last messages, and unread counts so a
  10-conversation list is two queries instead of forty.
"""
from typing import List, Optional, Iterable, Dict
from uuid import UUID
from datetime import datetime, timezone, timedelta
from sqlalchemy import and_, func, or_, select, tuple_

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from auth import get_current_user
from models.db_user import DBUser
from models.db_event import DBEvent
from models.db_event_participant import DBEventParticipant
from models.db_post import DBPost
from models.db_friendship import DBFriendship
from models.db_conversation import DBConversation, DBConversationMember
from models.db_conversation_ban import DBConversationBan
from models.db_message import DBMessage


router = APIRouter(prefix="/messaging", tags=["messaging"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class DMStartBody(BaseModel):
    recipient_id: UUID


class GroupStartBody(BaseModel):
    member_ids: List[UUID]
    title: Optional[str] = None
    image_url: Optional[str] = None


class SendMessageBody(BaseModel):
    body: Optional[str] = None
    kind: str = "text"
    shared_post_id: Optional[UUID] = None
    shared_event_id: Optional[UUID] = None
    image_url: Optional[str] = None
    # For voice / gif kinds. media_url holds the audio or gif URL; for
    # voice messages, voice_duration_seconds carries the clip length.
    media_url: Optional[str] = None
    voice_duration_seconds: Optional[float] = None


class AddMembersBody(BaseModel):
    member_ids: List[UUID] = Field(default_factory=list)


class UpdateMemberBody(BaseModel):
    is_admin: bool


class UpdateConversationBody(BaseModel):
    title: Optional[str] = None
    image_url: Optional[str] = None


class UpdateNicknameBody(BaseModel):
    nickname: Optional[str] = None  # empty / None clears it


class UpdateChatThemeBody(BaseModel):
    chat_theme: Optional[str] = None  # empty / None clears it


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_member(db: Session, conversation_id: UUID, user_id: UUID) -> DBConversationMember:
    row = db.query(DBConversationMember).filter_by(
        conversation_id=conversation_id, user_id=user_id
    ).first()
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this conversation")
    return row


def _ensure_admin(db: Session, conv: DBConversation, user_id: UUID) -> DBConversationMember:
    """Group-only admin guard. Direct chats reject; event chats defer to the organizer."""
    if conv.kind == "direct":
        raise HTTPException(status_code=400, detail="Direct chats don't support admin actions")
    if conv.kind == "event":
        # Allow the event organizer to manage their event chat even without an admin flag.
        ev = db.query(DBEvent).filter(DBEvent.event_id == conv.event_id).first()
        if ev and ev.organizer_id == user_id:
            return _ensure_member(db, conv.conversation_id, user_id)
        raise HTTPException(status_code=403, detail="Only the event organizer can manage an event chat")
    member = _ensure_member(db, conv.conversation_id, user_id)
    if not member.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return member


# Block helpers live in `blocks.py` — keep these aliases so the rest of this
# module doesn't have to change naming.
from blocks import is_blocked_between as _blocked_between  # noqa: E402
from blocks import invisible_user_ids as _invisible_user_ids  # noqa: E402


def _redacted_summary() -> dict:
    """Anonymized stand-in returned in place of a blocked user's profile."""
    return {
        "user_id":      None,
        "name":         "Blocked user",
        "avatar_photo": None,
        "avatar_config":None,
        "last_seen_at": None,
        "blocked":      True,
    }


def _user_summary(user: Optional[DBUser]) -> dict:
    if not user:
        return {"user_id": None, "name": "Unknown", "avatar_photo": None}
    return {
        "user_id": str(user.user_id),
        "name": f"{user.first_name or ''} {user.last_name or ''}".strip() or "Player",
        "avatar_photo": user.avatar_photo,
        "avatar_config": user.avatar_config,
    }


def _bulk_user_map(db: Session, ids: Iterable[UUID]) -> Dict[UUID, DBUser]:
    ids = list({i for i in ids if i is not None})
    if not ids:
        return {}
    rows = db.query(DBUser).filter(DBUser.user_id.in_(ids)).all()
    return {u.user_id: u for u in rows}


def _serialize_message_with_users(m: DBMessage, users: Dict[UUID, DBUser],
                                  posts: Dict[UUID, DBPost] = None,
                                  events: Dict[UUID, DBEvent] = None,
                                  post_authors: Dict[UUID, DBUser] = None,
                                  invisible: set = None) -> dict:
    sender = (
        _redacted_summary()
        if invisible and m.sender_id in invisible
        else _user_summary(users.get(m.sender_id))
    )
    payload = {
        "message_id":      str(m.message_id),
        "conversation_id": str(m.conversation_id),
        "sender":          sender,
        "kind":            m.kind,
        "body":            m.body,
        "image_url":       m.image_url,
        "media_url":       m.media_url,
        "voice_duration_seconds": m.voice_duration_seconds,
        "created_at":      m.created_at.isoformat() if m.created_at else None,
    }
    if m.shared_post_id and posts and m.shared_post_id in posts:
        post = posts[m.shared_post_id]
        author = (post_authors or {}).get(post.author_id)
        payload["shared_post"] = {
            "post_id":   str(post.post_id),
            "author":    _user_summary(author),
            "body":      (post.body or "")[:200],
            "image_url": post.image_url,
            "sport":     post.sport,
        }
    if m.shared_event_id and events and m.shared_event_id in events:
        ev = events[m.shared_event_id]
        payload["shared_event"] = {
            "event_id":   str(ev.event_id),
            "title":      ev.title,
            "sport":      ev.sport,
            "start_date": str(ev.start_date) if ev.start_date else None,
            "location":   ev.location,
        }
    return payload


def _serialize_message(m: DBMessage, db: Session, viewer_id: UUID = None) -> dict:
    """Single-message serializer (kept for the send_message return path).

    For lists of messages prefer _serialize_message_with_users with a prefetched
    user/post/event map — this function does N round trips per call.
    """
    users = _bulk_user_map(db, [m.sender_id])
    posts: Dict[UUID, DBPost] = {}
    events: Dict[UUID, DBEvent] = {}
    post_authors: Dict[UUID, DBUser] = {}
    if m.shared_post_id:
        p = db.query(DBPost).filter(DBPost.post_id == m.shared_post_id).first()
        if p:
            posts[p.post_id] = p
            post_authors = _bulk_user_map(db, [p.author_id])
    if m.shared_event_id:
        e = db.query(DBEvent).filter(DBEvent.event_id == m.shared_event_id).first()
        if e:
            events[e.event_id] = e
    invisible = _invisible_user_ids(db, viewer_id) if viewer_id else None
    return _serialize_message_with_users(m, users, posts, events, post_authors, invisible)


def _read_state_for(db: Session, conversation_id: UUID, viewer_id: UUID,
                    members: List[DBConversationMember]) -> dict:
    """Compute who-has-read-up-to-when for the latest viewer-sent message.

    Used to power read receipts. Returns:
      {
        "last_read_at": iso_or_null,    # max of OTHER members' last_read_at
        "read_by_count": int,           # how many other members have read
                                        # past the viewer's most recent message
        "members_total": int,
      }
    """
    others = [m for m in members if m.user_id != viewer_id]
    if not others:
        return {"last_read_at": None, "read_by_count": 0, "members_total": 0}
    last_mine = (
        db.query(DBMessage.created_at)
        .filter(DBMessage.conversation_id == conversation_id, DBMessage.sender_id == viewer_id)
        .order_by(DBMessage.created_at.desc())
        .first()
    )
    if not last_mine:
        return {"last_read_at": None, "read_by_count": 0, "members_total": len(others)}
    last_mine_at = last_mine[0]
    read_by = sum(
        1 for m in others if m.last_read_at and m.last_read_at >= last_mine_at
    )
    most_recent = max((m.last_read_at for m in others if m.last_read_at), default=None)
    return {
        "last_read_at":  most_recent.isoformat() if most_recent else None,
        "read_by_count": read_by,
        "members_total": len(others),
    }


def _list_conversations_payload(db: Session, viewer_id: UUID, include_archived: bool = False) -> List[dict]:
    """Bulk-load every conversation the viewer is in, plus the data needed to
    render a list row, in a small fixed number of queries.

    By default archived conversations are filtered out — pass
    include_archived=True to fetch them (used by an Archived inbox view).
    Favorites are pinned to the top regardless.
    """
    invisible = _invisible_user_ids(db, viewer_id)
    q = (
        db.query(
            DBConversation,
            DBConversationMember.archived_at,
            DBConversationMember.favorited_at,
            DBConversationMember.muted_at,
            DBConversationMember.nickname,
            DBConversationMember.chat_theme,
        )
        .join(DBConversationMember, DBConversation.conversation_id == DBConversationMember.conversation_id)
        .filter(DBConversationMember.user_id == viewer_id)
    )
    if not include_archived:
        q = q.filter(DBConversationMember.archived_at.is_(None))
    rows = q.order_by(DBConversation.last_message_at.desc()).all()
    if not rows:
        return []
    convs = [r[0] for r in rows]
    archived_by_conv  = {r[0].conversation_id: r[1] for r in rows}
    favorited_by_conv = {r[0].conversation_id: r[2] for r in rows}
    muted_by_conv     = {r[0].conversation_id: r[3] for r in rows}
    nickname_by_conv  = {r[0].conversation_id: r[4] for r in rows}
    theme_by_conv     = {r[0].conversation_id: r[5] for r in rows}
    # Favorites pinned to top, both groups still in last_message_at desc order.
    convs.sort(key=lambda c: (
        0 if favorited_by_conv.get(c.conversation_id) else 1,
        -(c.last_message_at.timestamp() if c.last_message_at else 0),
    ))

    conv_ids = [c.conversation_id for c in convs]

    # All members across all those conversations (one query).
    all_members: List[DBConversationMember] = (
        db.query(DBConversationMember)
        .filter(DBConversationMember.conversation_id.in_(conv_ids))
        .all()
    )
    members_by_conv: Dict[UUID, List[DBConversationMember]] = {}
    for m in all_members:
        members_by_conv.setdefault(m.conversation_id, []).append(m)

    user_ids = {m.user_id for m in all_members}
    users = _bulk_user_map(db, user_ids)

    # Last message per conversation, via DISTINCT ON (Postgres). We fetch the
    # candidate set with a window to keep this portable across drivers.
    last_msgs: Dict[UUID, DBMessage] = {}
    if conv_ids:
        # Subquery: rank by created_at desc per conversation, take rank 1.
        subq = (
            db.query(
                DBMessage,
                func.row_number().over(
                    partition_by=DBMessage.conversation_id,
                    order_by=DBMessage.created_at.desc(),
                ).label("rn"),
            )
            .filter(DBMessage.conversation_id.in_(conv_ids))
            .subquery()
        )
        # SQLAlchemy 2.x: re-hydrate DBMessage from the subquery.
        rows = db.query(DBMessage).join(subq, DBMessage.message_id == subq.c.message_id).filter(subq.c.rn == 1).all()
        last_msgs = {m.conversation_id: m for m in rows}

    # Unread counts per conversation, in one grouped query.
    last_read_by_conv = {
        m.conversation_id: m.last_read_at for m in all_members if m.user_id == viewer_id
    }
    unread_counts: Dict[UUID, int] = {cid: 0 for cid in conv_ids}
    if last_read_by_conv:
        # Build (conv_id, last_read_at) tuples → message count where created_at > last_read_at.
        # Fallback to per-conversation small queries (tuple_in vs jsonb is messier across DBs).
        for cid, lra in last_read_by_conv.items():
            if lra is None:
                continue
            unread_counts[cid] = (
                db.query(func.count(DBMessage.message_id))
                .filter(
                    DBMessage.conversation_id == cid,
                    DBMessage.created_at > lra,
                    DBMessage.sender_id != viewer_id,
                )
                .scalar() or 0
            )

    # Prefetch any shared posts/events on the last-message preview.
    shared_post_ids = {m.shared_post_id for m in last_msgs.values() if m.shared_post_id}
    shared_event_ids = {m.shared_event_id for m in last_msgs.values() if m.shared_event_id}
    posts: Dict[UUID, DBPost] = {}
    events: Dict[UUID, DBEvent] = {}
    if shared_post_ids:
        for p in db.query(DBPost).filter(DBPost.post_id.in_(shared_post_ids)).all():
            posts[p.post_id] = p
    if shared_event_ids:
        for e in db.query(DBEvent).filter(DBEvent.event_id.in_(shared_event_ids)).all():
            events[e.event_id] = e
    post_authors = _bulk_user_map(db, {p.author_id for p in posts.values()})

    # Event titles for event-kind conversations whose title is null.
    extra_event_ids = {c.event_id for c in convs if c.kind == "event" and c.event_id and not c.title}
    if extra_event_ids:
        for e in db.query(DBEvent).filter(DBEvent.event_id.in_(extra_event_ids)).all():
            events[e.event_id] = e

    out: List[dict] = []
    for conv in convs:
        members = members_by_conv.get(conv.conversation_id, [])
        member_payload = []
        for m in members:
            u = users.get(m.user_id)
            # Belt-and-suspenders: even if the is_admin column is unset (eg
            # a row created before migration 0005 backfilled), the conversation
            # creator is always treated as an admin.
            is_admin = bool(m.is_admin) or (conv.created_by is not None and m.user_id == conv.created_by)
            base = (
                _redacted_summary()
                if (m.user_id in invisible and m.user_id != viewer_id)
                else _user_summary(u)
            )
            member_payload.append({**base, "is_admin": is_admin})

        # Per-viewer nickname overrides the DM title (private to this user).
        viewer_nickname = nickname_by_conv.get(conv.conversation_id)
        title = conv.title
        if conv.kind == "direct" and viewer_nickname:
            title = viewer_nickname
        elif not title:
            if conv.kind == "direct":
                other = next((m for m in member_payload if m["user_id"] != str(viewer_id)), None)
                title = other["name"] if other else "Direct message"
            elif conv.kind == "event" and conv.event_id and conv.event_id in events:
                title = events[conv.event_id].title or "Event chat"
            elif conv.kind == "event":
                title = "Event chat"
            else:
                names = [m["name"].split()[0] for m in member_payload if m["user_id"] != str(viewer_id)]
                title = ", ".join(names[:3]) or "Group chat"

        last_msg = last_msgs.get(conv.conversation_id)
        last_msg_payload = (
            _serialize_message_with_users(last_msg, users, posts, events, post_authors, invisible)
            if last_msg else None
        )

        out.append({
            "conversation_id": str(conv.conversation_id),
            "kind":            conv.kind,
            "title":           title,
            "image_url":       conv.image_url,
            "created_by":      str(conv.created_by) if conv.created_by else None,
            "event_id":        str(conv.event_id) if conv.event_id else None,
            "members":         member_payload,
            "unread_count":    unread_counts.get(conv.conversation_id, 0),
            "last_message":    last_msg_payload,
            "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
            "read_state":      _read_state_for(db, conv.conversation_id, viewer_id, members),
            "is_favorited":    favorited_by_conv.get(conv.conversation_id) is not None,
            "is_archived":     archived_by_conv.get(conv.conversation_id) is not None,
            "is_muted":        muted_by_conv.get(conv.conversation_id) is not None,
            "nickname":        viewer_nickname,
            "chat_theme":      theme_by_conv.get(conv.conversation_id),
        })
    return out


def _serialize_conversation(conv: DBConversation, db: Session, viewer_id: UUID) -> dict:
    """Single-conversation serializer used after writes (start group, send message)."""
    members = (
        db.query(DBConversationMember)
        .filter(DBConversationMember.conversation_id == conv.conversation_id)
        .all()
    )
    users = _bulk_user_map(db, [m.user_id for m in members])
    invisible = _invisible_user_ids(db, viewer_id)
    member_payload = []
    for m in members:
        is_admin = bool(m.is_admin) or (conv.created_by is not None and m.user_id == conv.created_by)
        base = (
            _redacted_summary()
            if (m.user_id in invisible and m.user_id != viewer_id)
            else _user_summary(users.get(m.user_id))
        )
        member_payload.append({**base, "is_admin": is_admin})

    last_msg = (
        db.query(DBMessage)
        .filter(DBMessage.conversation_id == conv.conversation_id)
        .order_by(DBMessage.created_at.desc())
        .first()
    )

    my_last_read = next((m.last_read_at for m in members if m.user_id == viewer_id), None)
    unread = 0
    if my_last_read is not None:
        unread = (
            db.query(func.count(DBMessage.message_id))
            .filter(
                DBMessage.conversation_id == conv.conversation_id,
                DBMessage.created_at > my_last_read,
                DBMessage.sender_id != viewer_id,
            )
            .scalar() or 0
        )

    # Per-viewer personalization for this conv.
    my_member = next((m for m in members if m.user_id == viewer_id), None)
    viewer_nickname = getattr(my_member, "nickname", None) if my_member else None
    viewer_theme    = getattr(my_member, "chat_theme", None) if my_member else None
    viewer_muted    = bool(getattr(my_member, "muted_at", None)) if my_member else False

    title = conv.title
    if conv.kind == "direct" and viewer_nickname:
        title = viewer_nickname
    elif not title:
        if conv.kind == "direct":
            other = next((u for u in member_payload if u["user_id"] != str(viewer_id)), None)
            title = other["name"] if other else "Direct message"
        elif conv.kind == "event" and conv.event_id:
            ev = db.query(DBEvent).filter(DBEvent.event_id == conv.event_id).first()
            title = ev.title if ev else "Event chat"
        else:
            names = [u["name"].split()[0] for u in member_payload if u["user_id"] != str(viewer_id)]
            title = ", ".join(names[:3]) or "Group chat"

    return {
        "conversation_id": str(conv.conversation_id),
        "kind":            conv.kind,
        "title":           title,
        "image_url":       conv.image_url,
        "created_by":      str(conv.created_by) if conv.created_by else None,
        "event_id":        str(conv.event_id) if conv.event_id else None,
        "members":         member_payload,
        "unread_count":    unread,
        "last_message":    _serialize_message(last_msg, db, viewer_id) if last_msg else None,
        "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
        "is_muted":        viewer_muted,
        "nickname":        viewer_nickname,
        "chat_theme":      viewer_theme,
    }


def _find_direct_conversation(db: Session, a: UUID, b: UUID) -> Optional[DBConversation]:
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
    include_archived: bool = Query(False, description="Include archived conversations in the response."),
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    return {
        "conversations": _list_conversations_payload(
            db, current_user.user_id, include_archived=include_archived,
        ),
    }


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

    title = (body.title or "").strip() or None
    if title and len(title) > 120:
        raise HTTPException(status_code=400, detail="Title is too long")

    conv = DBConversation(
        kind="group",
        title=title,
        image_url=(body.image_url or None),
        created_by=current_user.user_id,
    )
    db.add(conv)
    db.flush()
    for uid in member_ids:
        db.add(DBConversationMember(
            conversation_id=conv.conversation_id,
            user_id=uid,
            is_admin=(uid == current_user.user_id),
        ))
    db.commit()
    db.refresh(conv)
    return _serialize_conversation(conv, db, current_user.user_id)


@router.patch("/conversations/{conversation_id}")
def update_conversation(
    conversation_id: UUID,
    body: UpdateConversationBody,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    conv = db.query(DBConversation).filter(DBConversation.conversation_id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _ensure_admin(db, conv, current_user.user_id)

    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title can't be empty")
        if len(title) > 120:
            raise HTTPException(status_code=400, detail="Title is too long")
        conv.title = title

    if body.image_url is not None:
        # Empty string clears the photo; otherwise overwrite.
        conv.image_url = body.image_url.strip() or None

    db.commit()
    db.refresh(conv)
    return _serialize_conversation(conv, db, current_user.user_id)


@router.post("/conversations/{conversation_id}/members")
def add_members(
    conversation_id: UUID,
    body: AddMembersBody,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    conv = db.query(DBConversation).filter(DBConversation.conversation_id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _ensure_admin(db, conv, current_user.user_id)

    if not body.member_ids:
        return _serialize_conversation(conv, db, current_user.user_id)

    existing_ids = {
        m.user_id for m in db.query(DBConversationMember.user_id)
        .filter(DBConversationMember.conversation_id == conversation_id).all()
    }
    requested = set(body.member_ids)
    new_ids = [uid for uid in requested if uid not in existing_ids]

    # Reject anyone who's been banned from this conversation. Admins must lift
    # the ban first via DELETE /conversations/{id}/bans/{user_id}.
    if new_ids:
        banned_ids = {
            b.user_id for b in db.query(DBConversationBan.user_id).filter(
                DBConversationBan.conversation_id == conversation_id,
                DBConversationBan.user_id.in_(new_ids),
            ).all()
        }
        new_ids = [uid for uid in new_ids if uid not in banned_ids]

    if new_ids:
        valid_users = {
            u.user_id for u in db.query(DBUser.user_id).filter(DBUser.user_id.in_(new_ids)).all()
        }
        for uid in new_ids:
            if uid not in valid_users:
                continue
            db.add(DBConversationMember(conversation_id=conversation_id, user_id=uid))
        db.commit()
        db.refresh(conv)

    return _serialize_conversation(conv, db, current_user.user_id)


@router.delete("/conversations/{conversation_id}/members/{user_id}")
def remove_member(
    conversation_id: UUID,
    user_id: UUID,
    ban: bool = Query(False, description="Admin-only: also add the user to the ban list so they can't be re-added without lifting the ban."),
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Self-leave OR admin-kick. The last admin can't leave without promoting another first.

    Pass ?ban=true (admin only) to additionally ban the user from re-joining.
    """
    conv = db.query(DBConversation).filter(DBConversation.conversation_id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.kind == "direct":
        raise HTTPException(status_code=400, detail="Direct chats can't have members removed")

    target = db.query(DBConversationMember).filter_by(
        conversation_id=conversation_id, user_id=user_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User isn't in this conversation")

    is_self = user_id == current_user.user_id
    if not is_self:
        _ensure_admin(db, conv, current_user.user_id)

    if ban and is_self:
        raise HTTPException(status_code=400, detail="You can't ban yourself")
    if ban and not is_self:
        # Already verified admin above for the non-self path.
        pass

    if conv.kind == "group" and target.is_admin:
        other_admins = db.query(func.count(DBConversationMember.user_id)).filter(
            DBConversationMember.conversation_id == conversation_id,
            DBConversationMember.is_admin.is_(True),
            DBConversationMember.user_id != user_id,
        ).scalar() or 0
        if other_admins == 0:
            raise HTTPException(
                status_code=400,
                detail="Promote another admin before removing the last admin",
            )

    db.delete(target)
    if ban:
        # Upsert: ignore if a ban row already exists.
        existing_ban = db.query(DBConversationBan).filter_by(
            conversation_id=conversation_id, user_id=user_id,
        ).first()
        if not existing_ban:
            db.add(DBConversationBan(
                conversation_id=conversation_id,
                user_id=user_id,
                banned_by=current_user.user_id,
            ))
    db.commit()
    return {"status": "removed", "banned": ban}


@router.get("/conversations/{conversation_id}/bans")
def list_bans(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Admin-only: list users currently banned from this conversation."""
    conv = db.query(DBConversation).filter(DBConversation.conversation_id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _ensure_admin(db, conv, current_user.user_id)

    rows = db.query(DBConversationBan).filter_by(conversation_id=conversation_id).all()
    user_map = _bulk_user_map(db, [r.user_id for r in rows])
    return {
        "bans": [
            {
                **_user_summary(user_map.get(r.user_id)),
                "banned_at": r.banned_at.isoformat() if r.banned_at else None,
                "banned_by": str(r.banned_by) if r.banned_by else None,
            }
            for r in rows
        ],
    }


@router.delete("/conversations/{conversation_id}/bans/{user_id}")
def lift_ban(
    conversation_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Admin-only: lift a ban so the user can be re-added."""
    conv = db.query(DBConversation).filter(DBConversation.conversation_id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _ensure_admin(db, conv, current_user.user_id)

    row = db.query(DBConversationBan).filter_by(
        conversation_id=conversation_id, user_id=user_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="No active ban for this user")
    db.delete(row)
    db.commit()
    return {"status": "lifted"}


@router.patch("/conversations/{conversation_id}/members/{user_id}")
def update_member(
    conversation_id: UUID,
    user_id: UUID,
    body: UpdateMemberBody,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Promote or demote a member. Admin only. Last admin can't demote themselves."""
    conv = db.query(DBConversation).filter(DBConversation.conversation_id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _ensure_admin(db, conv, current_user.user_id)

    target = db.query(DBConversationMember).filter_by(
        conversation_id=conversation_id, user_id=user_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User isn't in this conversation")

    if not body.is_admin and target.is_admin:
        other_admins = db.query(func.count(DBConversationMember.user_id)).filter(
            DBConversationMember.conversation_id == conversation_id,
            DBConversationMember.is_admin.is_(True),
            DBConversationMember.user_id != user_id,
        ).scalar() or 0
        if other_admins == 0:
            raise HTTPException(status_code=400, detail="Promote another admin first")

    target.is_admin = bool(body.is_admin)
    db.commit()
    return {"status": "ok", "is_admin": target.is_admin}


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
        participants = db.query(DBEventParticipant).filter_by(event_id=event_id).all()
        seen = set()
        for p in participants:
            if p.user_id in seen:
                continue
            db.add(DBConversationMember(
                conversation_id=conv.conversation_id,
                user_id=p.user_id,
                is_admin=(p.user_id == event.organizer_id),
            ))
            seen.add(p.user_id)
        if event.organizer_id and event.organizer_id not in seen:
            db.add(DBConversationMember(
                conversation_id=conv.conversation_id,
                user_id=event.organizer_id,
                is_admin=True,
            ))
        db.commit()
        db.refresh(conv)
    else:
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
    since: Optional[datetime] = Query(
        None,
        description="Return only messages strictly newer than this timestamp. "
                    "Use for incremental polling — much smaller payload than full refetch.",
    ),
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    member = _ensure_member(db, conversation_id, current_user.user_id)

    q = db.query(DBMessage).filter(DBMessage.conversation_id == conversation_id)
    # message_id is the deterministic tiebreaker when two messages share a
    # created_at (sub-second collisions on SQLite, batched inserts on Postgres).
    if since is not None:
        msgs = (q.filter(DBMessage.created_at > since)
                  .order_by(DBMessage.created_at.asc(), DBMessage.message_id.asc())
                  .limit(limit).all())
    else:
        if before:
            q = q.filter(DBMessage.created_at < before)
        msgs = (q.order_by(DBMessage.created_at.desc(), DBMessage.message_id.desc())
                  .limit(limit).all())
        msgs.reverse()  # chronological

    member.last_read_at = datetime.now(timezone.utc)
    db.commit()

    if not msgs:
        return {"messages": []}

    sender_ids = {m.sender_id for m in msgs}
    users = _bulk_user_map(db, sender_ids)

    shared_post_ids = {m.shared_post_id for m in msgs if m.shared_post_id}
    shared_event_ids = {m.shared_event_id for m in msgs if m.shared_event_id}
    posts: Dict[UUID, DBPost] = {}
    events: Dict[UUID, DBEvent] = {}
    if shared_post_ids:
        for p in db.query(DBPost).filter(DBPost.post_id.in_(shared_post_ids)).all():
            posts[p.post_id] = p
    if shared_event_ids:
        for e in db.query(DBEvent).filter(DBEvent.event_id.in_(shared_event_ids)).all():
            events[e.event_id] = e
    post_authors = _bulk_user_map(db, {p.author_id for p in posts.values()})
    invisible = _invisible_user_ids(db, current_user.user_id)

    return {"messages": [
        _serialize_message_with_users(m, users, posts, events, post_authors, invisible) for m in msgs
    ]}


@router.post("/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: UUID,
    body: SendMessageBody,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    _ensure_member(db, conversation_id, current_user.user_id)

    kind = (body.kind or "text").strip()
    if kind not in ("text", "post_share", "event_share", "image", "voice", "gif"):
        raise HTTPException(status_code=400, detail="Unknown message kind")

    if kind == "text" and (not body.body or not body.body.strip()):
        raise HTTPException(status_code=400, detail="Message body required")
    if body.body and len(body.body) > 2000:
        raise HTTPException(status_code=400, detail="Message is too long")
    if kind == "image" and not body.image_url:
        raise HTTPException(status_code=400, detail="image_url required for image messages")
    if kind == "voice" and not (body.media_url or body.image_url):
        raise HTTPException(status_code=400, detail="media_url required for voice messages")
    if kind == "gif" and not (body.media_url or body.image_url):
        raise HTTPException(status_code=400, detail="media_url required for gif messages")

    # In a 1:1 DM, refuse delivery if either side has blocked the other.
    # We only enforce on direct chats — group/event chats stay functional but
    # the other side's identity is anonymized in the read paths.
    conv_for_block = db.query(DBConversation).filter(
        DBConversation.conversation_id == conversation_id
    ).first()
    if conv_for_block and conv_for_block.kind == "direct":
        other_member = db.query(DBConversationMember).filter(
            DBConversationMember.conversation_id == conversation_id,
            DBConversationMember.user_id != current_user.user_id,
        ).first()
        if other_member and _blocked_between(db, current_user.user_id, other_member.user_id):
            raise HTTPException(
                status_code=403,
                detail="Messages can't be delivered between blocked users.",
            )

    # Set created_at from Python so we get microsecond precision (SQLite's
    # server-side CURRENT_TIMESTAMP only has second resolution and would tie
    # messages sent in rapid succession, breaking ordering and ?since= polling).
    now = datetime.now(timezone.utc)
    msg = DBMessage(
        conversation_id = conversation_id,
        sender_id       = current_user.user_id,
        kind            = kind,
        body            = body.body.strip() if body.body else None,
        shared_post_id  = body.shared_post_id,
        shared_event_id = body.shared_event_id,
        image_url       = body.image_url,
        media_url       = body.media_url,
        voice_duration_seconds = body.voice_duration_seconds,
        created_at      = now,
    )
    db.add(msg)

    conv = db.query(DBConversation).filter(DBConversation.conversation_id == conversation_id).first()
    if conv:
        conv.last_message_at = now

    sender_member = db.query(DBConversationMember).filter_by(
        conversation_id=conversation_id, user_id=current_user.user_id
    ).first()
    if sender_member:
        sender_member.last_read_at = datetime.now(timezone.utc)

    # A new message un-archives the conversation for *every* member who had
    # it archived — both sender and recipients. People want their inbox to
    # surface a chat when there's something fresh in it.
    db.query(DBConversationMember).filter(
        DBConversationMember.conversation_id == conversation_id,
        DBConversationMember.archived_at.is_not(None),
    ).update({"archived_at": None}, synchronize_session=False)

    db.commit()
    db.refresh(msg)

    try:
        from notifications import send_push
        preview = (body.body or {
            "post_share":  "shared a post",
            "event_share": "shared an event",
            "image":       "sent a photo",
            "voice":       "sent a voice message",
            "gif":         "sent a GIF",
        }.get(kind, "sent a message"))[:140]
        sender_name = f"{current_user.first_name or ''} {current_user.last_name or ''}".strip() or "New message"
        members = db.query(DBConversationMember).filter(
            DBConversationMember.conversation_id == conversation_id,
            DBConversationMember.user_id != current_user.user_id,
        ).all()
        for m in members:
            # Honor mute — skip push for anyone who muted this conversation.
            if m.muted_at is not None:
                continue
            send_push(
                db,
                m.user_id,
                title=sender_name,
                body=preview,
                data={"conversation_id": str(conversation_id), "type": "new_message"},
            )
    except Exception:
        pass

    return _serialize_message(msg, db, current_user.user_id)


@router.delete("/conversations/{conversation_id}/messages/{message_id}")
def delete_message(
    conversation_id: UUID,
    message_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Sender OR a group/event admin can delete a message."""
    msg = db.query(DBMessage).filter(
        DBMessage.message_id == message_id,
        DBMessage.conversation_id == conversation_id,
    ).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    is_sender = msg.sender_id == current_user.user_id
    if not is_sender:
        conv = db.query(DBConversation).filter(DBConversation.conversation_id == conversation_id).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        # _ensure_admin handles event/group/direct correctly
        _ensure_admin(db, conv, current_user.user_id)

    db.delete(msg)
    db.commit()
    return {"status": "deleted"}


@router.post("/conversations/{conversation_id}/archive")
def archive_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Hide this conversation from the user's default inbox."""
    member = _ensure_member(db, conversation_id, current_user.user_id)
    member.archived_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "archived"}


@router.delete("/conversations/{conversation_id}/archive")
def unarchive_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    member = _ensure_member(db, conversation_id, current_user.user_id)
    member.archived_at = None
    db.commit()
    return {"status": "active"}


@router.post("/conversations/{conversation_id}/favorite")
def favorite_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Pin this conversation to the top of the user's inbox."""
    member = _ensure_member(db, conversation_id, current_user.user_id)
    member.favorited_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "favorited"}


@router.delete("/conversations/{conversation_id}/favorite")
def unfavorite_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    member = _ensure_member(db, conversation_id, current_user.user_id)
    member.favorited_at = None
    db.commit()
    return {"status": "unfavorited"}


# ── Mute ────────────────────────────────────────────────────────────────────

@router.post("/conversations/{conversation_id}/mute")
def mute_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Suppress push notifications + in-app toasts for this conversation,
    just for the calling user. Other members are unaffected."""
    member = _ensure_member(db, conversation_id, current_user.user_id)
    member.muted_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "muted"}


@router.delete("/conversations/{conversation_id}/mute")
def unmute_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    member = _ensure_member(db, conversation_id, current_user.user_id)
    member.muted_at = None
    db.commit()
    return {"status": "unmuted"}


# ── Nickname (DM only — overrides title for the calling user only) ──────────

@router.patch("/conversations/{conversation_id}/nickname")
def update_nickname(
    conversation_id: UUID,
    body: UpdateNicknameBody,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    member = _ensure_member(db, conversation_id, current_user.user_id)
    nickname = (body.nickname or "").strip() or None
    if nickname and len(nickname) > 60:
        raise HTTPException(status_code=400, detail="Nickname is too long (max 60)")
    member.nickname = nickname
    db.commit()
    return {"status": "ok", "nickname": member.nickname}


# ── Chat theme (per-user background — color hex or image URL) ───────────────

@router.patch("/conversations/{conversation_id}/theme")
def update_chat_theme(
    conversation_id: UUID,
    body: UpdateChatThemeBody,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    member = _ensure_member(db, conversation_id, current_user.user_id)
    theme = (body.chat_theme or "").strip() or None
    # Cap at 1024 chars — covers any reasonable URL or hex value with slack.
    if theme and len(theme) > 1024:
        raise HTTPException(status_code=400, detail="Theme value is too long")
    member.chat_theme = theme
    db.commit()
    return {"status": "ok", "chat_theme": member.chat_theme}


# ── Media library (images sent in this conversation) ───────────────────────

@router.get("/conversations/{conversation_id}/media")
def list_conversation_media(
    conversation_id: UUID,
    limit: int = Query(60, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Return the most recent image / gif attachments in a conversation,
    newest first. Used by the chat info screen's Media grid."""
    _ensure_member(db, conversation_id, current_user.user_id)

    msgs = (
        db.query(DBMessage)
        .filter(
            DBMessage.conversation_id == conversation_id,
            DBMessage.kind.in_(("image", "gif")),
        )
        .order_by(DBMessage.created_at.desc(), DBMessage.message_id.desc())
        .limit(limit)
        .all()
    )

    out = []
    for m in msgs:
        url = m.image_url or m.media_url
        if not url:
            continue
        out.append({
            "message_id":  str(m.message_id),
            "kind":        m.kind,
            "url":         url,
            "created_at":  m.created_at.isoformat() if m.created_at else None,
            "sender_id":   str(m.sender_id) if m.sender_id else None,
        })
    return {"media": out}


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


@router.get("/conversations/{conversation_id}/read-receipts")
def conversation_read_receipts(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Per-member read state for a conversation, used to render read receipts.

    Returns the timestamp each OTHER member last read up to. Clients render this
    as: members whose last_read_at >= a given message's created_at have seen it.
    """
    _ensure_member(db, conversation_id, current_user.user_id)
    members = db.query(DBConversationMember).filter_by(conversation_id=conversation_id).all()
    others = [m for m in members if m.user_id != current_user.user_id]
    user_map = _bulk_user_map(db, [m.user_id for m in others])
    invisible = _invisible_user_ids(db, current_user.user_id)
    out = []
    for m in others:
        base = (
            _redacted_summary()
            if m.user_id in invisible
            else _user_summary(user_map.get(m.user_id))
        )
        out.append({
            **base,
            "last_read_at": m.last_read_at.isoformat() if m.last_read_at else None,
        })
    return {"members": out}


@router.get("/unread-summary")
def unread_summary(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Total unread message count across all conversations (for tab badges)."""
    members = db.query(DBConversationMember).filter(
        DBConversationMember.user_id == current_user.user_id
    ).all()
    if not members:
        return {"unread": 0}
    total = 0
    for m in members:
        total += (
            db.query(func.count(DBMessage.message_id))
            .filter(
                DBMessage.conversation_id == m.conversation_id,
                DBMessage.created_at > m.last_read_at,
                DBMessage.sender_id != current_user.user_id,
            )
            .scalar() or 0
        )
    return {"unread": total}


# ── Notification feed (powers the in-app top banner) ─────────────────────────

@router.get("/notifications/summary")
def notifications_summary(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """One-shot endpoint for the top-of-screen banner.

    Returns the data needed to show: unread DM count, pending friend requests,
    and the next upcoming event (within the next 48h) the user is part of.
    Cheap enough to poll every 20–30s.
    """
    # Unread messages.
    members = db.query(DBConversationMember).filter(
        DBConversationMember.user_id == current_user.user_id
    ).all()
    unread = 0
    for m in members:
        unread += (
            db.query(func.count(DBMessage.message_id))
            .filter(
                DBMessage.conversation_id == m.conversation_id,
                DBMessage.created_at > m.last_read_at,
                DBMessage.sender_id != current_user.user_id,
            )
            .scalar() or 0
        )

    # Pending incoming friend requests.
    incoming = db.query(func.count(DBFriendship.requester_id)).filter(
        DBFriendship.status == "pending",
        DBFriendship.addressee_id == current_user.user_id,
    ).scalar() or 0

    # Next upcoming event the user joined or is hosting (next 48h).
    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(hours=48)).date()
    today = now.date()

    organizer_q = db.query(DBEvent).filter(
        DBEvent.organizer_id == current_user.user_id,
        DBEvent.status == "active",
        DBEvent.start_date >= today,
        DBEvent.start_date <= horizon,
    )
    participant_q = (
        db.query(DBEvent)
        .join(DBEventParticipant, DBEvent.event_id == DBEventParticipant.event_id)
        .filter(
            DBEventParticipant.user_id == current_user.user_id,
            DBEvent.status == "active",
            DBEvent.start_date >= today,
            DBEvent.start_date <= horizon,
        )
    )
    candidates = list({e.event_id: e for e in (organizer_q.all() + participant_q.all())}.values())
    candidates.sort(key=lambda e: (e.start_date, e.start_time or datetime.min.time()))
    next_event = candidates[0] if candidates else None

    next_event_payload = None
    if next_event:
        next_event_payload = {
            "event_id":   str(next_event.event_id),
            "title":      next_event.title,
            "sport":      next_event.sport,
            "start_date": str(next_event.start_date) if next_event.start_date else None,
            "start_time": str(next_event.start_time) if next_event.start_time else None,
            "location":   next_event.location,
        }

    return {
        "unread_messages":     unread,
        "pending_friend_requests": incoming,
        "next_event":          next_event_payload,
    }
