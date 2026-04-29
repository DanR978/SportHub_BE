"""
Friends: request, accept, decline, unfriend, list, pending.

Rows canonicalize around (requester_id, addressee_id). We don't store a row
per direction — the accepted state is symmetric and we resolve "friend of"
queries with an OR across both columns.
"""
from typing import Optional, List
from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from database import get_db
from auth import get_current_user
from models.db_user import DBUser
from models.db_block import DBBlock
from models.db_friendship import DBFriendship


router = APIRouter(prefix="/friends", tags=["friends"])


class FriendRequestBody(BaseModel):
    user_id: UUID


def _user_summary(u: Optional[DBUser]) -> dict:
    if not u:
        return {"user_id": None, "name": "Unknown"}
    return {
        "user_id":      str(u.user_id),
        "first_name":   u.first_name,
        "last_name":    u.last_name,
        "name":         f"{u.first_name or ''} {u.last_name or ''}".strip() or "Player",
        "avatar_photo": u.avatar_photo,
        "avatar_config":u.avatar_config,
        "bio":          u.bio,
        "last_seen_at": u.last_seen_at.isoformat() if u.last_seen_at else None,
    }


def _find_pair(db: Session, a: UUID, b: UUID) -> Optional[DBFriendship]:
    return db.query(DBFriendship).filter(
        or_(
            and_(DBFriendship.requester_id == a, DBFriendship.addressee_id == b),
            and_(DBFriendship.requester_id == b, DBFriendship.addressee_id == a),
        )
    ).first()


def _blocked_between(db: Session, a: UUID, b: UUID) -> bool:
    return db.query(DBBlock).filter(
        or_(
            and_(DBBlock.blocker_id == a, DBBlock.blocked_id == b),
            and_(DBBlock.blocker_id == b, DBBlock.blocked_id == a),
        )
    ).first() is not None


@router.post("/request")
def send_request(
    body: FriendRequestBody,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    if body.user_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="Can't friend yourself")
    target = db.query(DBUser).filter(DBUser.user_id == body.user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if _blocked_between(db, current_user.user_id, body.user_id):
        raise HTTPException(status_code=403, detail="Can't friend this user")

    existing = _find_pair(db, current_user.user_id, body.user_id)
    if existing:
        if existing.status == "accepted":
            return {"status": "accepted", "message": "Already friends"}
        # If the other side had previously requested you, accept automatically.
        if existing.addressee_id == current_user.user_id:
            existing.status = "accepted"
            existing.accepted_at = datetime.now(timezone.utc)
            db.commit()
            return {"status": "accepted", "message": "Friend request accepted"}
        return {"status": "pending", "message": "Request already sent"}

    row = DBFriendship(
        requester_id=current_user.user_id,
        addressee_id=body.user_id,
        status="pending",
    )
    db.add(row)
    db.commit()

    # Best-effort push
    try:
        from notifications import send_push
        name = f"{current_user.first_name or ''} {current_user.last_name or ''}".strip() or "Someone"
        send_push(
            db,
            body.user_id,
            title="New friend request",
            body=f"{name} wants to be your friend",
            data={"type": "friend_request", "user_id": str(current_user.user_id)},
        )
    except Exception:
        pass

    return {"status": "pending", "message": "Friend request sent"}


@router.post("/{user_id}/accept")
def accept_request(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    row = _find_pair(db, current_user.user_id, user_id)
    if not row or row.status != "pending":
        raise HTTPException(status_code=404, detail="No pending request")
    if row.addressee_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Only the recipient can accept")
    row.status = "accepted"
    row.accepted_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "accepted"}


@router.post("/{user_id}/decline")
def decline_request(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    row = _find_pair(db, current_user.user_id, user_id)
    if not row or row.status != "pending":
        raise HTTPException(status_code=404, detail="No pending request")
    # Both sides can dismiss a pending request (sender cancels, receiver declines)
    db.delete(row)
    db.commit()
    return {"status": "declined"}


@router.delete("/{user_id}")
def unfriend(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    row = _find_pair(db, current_user.user_id, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not friends")
    db.delete(row)
    db.commit()
    return {"status": "unfriended"}


@router.get("")
def list_friends(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    rows = db.query(DBFriendship).filter(
        DBFriendship.status == "accepted",
        or_(
            DBFriendship.requester_id == current_user.user_id,
            DBFriendship.addressee_id == current_user.user_id,
        )
    ).all()

    friends = []
    for r in rows:
        other_id = r.addressee_id if r.requester_id == current_user.user_id else r.requester_id
        user = db.query(DBUser).filter(DBUser.user_id == other_id).first()
        if user:
            friends.append(_user_summary(user))
    friends.sort(key=lambda u: (u.get("first_name") or "", u.get("last_name") or ""))
    return {"friends": friends, "count": len(friends)}


@router.get("/pending")
def pending_requests(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    incoming_rows = db.query(DBFriendship).filter(
        DBFriendship.status == "pending",
        DBFriendship.addressee_id == current_user.user_id,
    ).all()
    outgoing_rows = db.query(DBFriendship).filter(
        DBFriendship.status == "pending",
        DBFriendship.requester_id == current_user.user_id,
    ).all()

    def serialize(row, other_id):
        u = db.query(DBUser).filter(DBUser.user_id == other_id).first()
        return {**_user_summary(u), "created_at": row.created_at.isoformat() if row.created_at else None}

    return {
        "incoming": [serialize(r, r.requester_id) for r in incoming_rows],
        "outgoing": [serialize(r, r.addressee_id) for r in outgoing_rows],
    }


def _accepted_friend_ids(db: Session, user_id: UUID) -> set[UUID]:
    """All other-user-ids in `accepted` friendships involving user_id."""
    rows = db.query(DBFriendship).filter(
        DBFriendship.status == "accepted",
        or_(
            DBFriendship.requester_id == user_id,
            DBFriendship.addressee_id == user_id,
        ),
    ).all()
    out: set[UUID] = set()
    for r in rows:
        out.add(r.addressee_id if r.requester_id == user_id else r.requester_id)
    return out


@router.get("/of/{user_id}")
def friends_of(
    user_id: UUID,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Friends list for a profile page.

    Always returns the target's friends. Also returns `mutuals` — the subset
    that the current viewer is also friends with — and `mutual_count` for
    rendering "X friends in common" on someone else's profile. When viewing
    your own profile, mutuals is empty by design.
    """
    target = db.query(DBUser).filter(DBUser.user_id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if _blocked_between(db, current_user.user_id, user_id):
        # Don't leak the friend list of someone you've blocked / blocked you.
        return {"friends": [], "count": 0, "mutuals": [], "mutual_count": 0}

    target_friend_ids = _accepted_friend_ids(db, user_id)
    is_self = user_id == current_user.user_id

    if not is_self:
        my_friend_ids = _accepted_friend_ids(db, current_user.user_id)
        mutual_ids = target_friend_ids & my_friend_ids
    else:
        mutual_ids = set()

    if not target_friend_ids:
        return {"friends": [], "count": 0, "mutuals": [], "mutual_count": 0}

    users = db.query(DBUser).filter(DBUser.user_id.in_(list(target_friend_ids))).all()
    by_id = {u.user_id: u for u in users}

    # Surface mutuals first; cap to `limit` for big rosters.
    ordered_ids = list(mutual_ids) + [uid for uid in target_friend_ids if uid not in mutual_ids]
    serialized = []
    for uid in ordered_ids[:limit]:
        u = by_id.get(uid)
        if not u:
            continue
        serialized.append({**_user_summary(u), "is_mutual": uid in mutual_ids})

    return {
        "friends":      serialized,
        "count":        len(target_friend_ids),
        "mutuals":      [s for s in serialized if s["is_mutual"]],
        "mutual_count": len(mutual_ids),
    }


@router.get("/status/{user_id}")
def friendship_status(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    """Get the caller's relationship to `user_id` — used by profile screens."""
    if user_id == current_user.user_id:
        return {"status": "self"}
    row = _find_pair(db, current_user.user_id, user_id)
    if not row:
        return {"status": "none"}
    if row.status == "accepted":
        return {"status": "friends"}
    direction = "outgoing" if row.requester_id == current_user.user_id else "incoming"
    return {"status": "pending", "direction": direction}
