"""
Shared blocking helpers — single home for the "is X blocked from Y" logic
that previously lived in three places (events.py, friends.py, messages.py)
with three slightly different signatures.

Two questions this module answers:

  - `is_blocked_between(db, a, b)` — is there a block row in EITHER direction?
    True means "a blocked b" or "b blocked a". Used wherever we want symmetric
    enforcement — DM delivery, event visibility, friendship gating.

  - `invisible_user_ids(db, viewer_id)` — set of user IDs whose content the
    viewer should not see. Includes both directions of every block touching
    the viewer. The hot path for list endpoints; one query per request.

The narrow `get_blocked_ids(db, viewer_id)` (one-direction "users I blocked")
is preserved for the few cases where direction matters — see kick-from-event.
"""
from typing import Set
from uuid import UUID

from sqlalchemy.orm import Session

from models.db_block import DBBlock


def is_blocked_between(db: Session, a: UUID, b: UUID) -> bool:
    """True iff there's a block row in either direction between `a` and `b`."""
    return db.query(DBBlock).filter(
        ((DBBlock.blocker_id == a) & (DBBlock.blocked_id == b)) |
        ((DBBlock.blocker_id == b) & (DBBlock.blocked_id == a))
    ).first() is not None


def get_blocked_ids(db: Session, user_id: UUID | None) -> Set[UUID]:
    """Set of user IDs THAT `user_id` HAS BLOCKED. Direction matters."""
    if not user_id:
        return set()
    rows = db.query(DBBlock.blocked_id).filter(DBBlock.blocker_id == user_id).all()
    return {r[0] for r in rows}


def invisible_user_ids(db: Session, viewer_id: UUID | None) -> Set[UUID]:
    """Union of "users `viewer_id` blocked" and "users who blocked `viewer_id`".

    Use this anywhere the viewer's experience should hide the other party
    entirely — event listings, conversation member redaction, profile pages.
    """
    if not viewer_id:
        return set()
    rows = db.query(DBBlock).filter(
        (DBBlock.blocker_id == viewer_id) | (DBBlock.blocked_id == viewer_id)
    ).all()
    out: Set[UUID] = set()
    for r in rows:
        out.add(r.blocked_id if r.blocker_id == viewer_id else r.blocker_id)
    return out
