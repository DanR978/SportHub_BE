"""
Community feed: posts, reactions (like / downvote), comments, and share tracking.

Ranking philosophy
------------------
We store a denormalized `score = like_count - downvote_count` on each post.
The "hot" feed sorts by score, then recency. The "new" feed is strictly recent.
A "nearby" feed additionally filters by Haversine radius around the caller.
"""
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Header, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from geopy.distance import geodesic
from better_profanity import profanity
from jose import jwt as jose_jwt

from database import get_db
from auth import get_current_user, SECRET_KEY, ALGORITHM
from models.db_user import DBUser
from models.db_post import DBPost
from models.db_post_reaction import DBPostReaction
from models.db_comment import DBComment
from models.db_comment_reaction import DBCommentLike
from models.db_block import DBBlock

ALLOWED_POST_CONTENT_TYPES = {
    # Images — includes HEIC/HEIF (iPhone default) and common formats from
    # both iOS and Android.
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
    "image/heic", "image/heif",
    # Videos — limited to short clips (30s) enforced client-side; the
    # file-size cap here is the server-side safety net.
    "video/mp4", "video/quicktime", "video/x-m4v",
    # Audio — voice messages from chat. m4a (AAC) is what expo-audio writes
    # by default on iOS; aac/mp3/webm/wav cover Android + future use cases.
    "audio/m4a", "audio/mp4", "audio/aac", "audio/mpeg", "audio/wav",
    "audio/x-m4a", "audio/webm",
}
MAX_POST_MEDIA_MB = 50


router = APIRouter(prefix="/feed", tags=["feed"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class PostCreate(BaseModel):
    body: str
    image_url: Optional[str] = None
    sport: Optional[str] = None
    event_id: Optional[UUID] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    place_label: Optional[str] = None


class CommentCreate(BaseModel):
    body: str
    parent_id: Optional[UUID] = None


class ReactionBody(BaseModel):
    kind: str   # 'like' or 'downvote' — send empty string or call DELETE to clear


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user_id_from_header(authorization: Optional[str], db: Session) -> Optional[UUID]:
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


def _blocked_ids(db: Session, user_id: Optional[UUID]) -> set:
    if not user_id:
        return set()
    rows = db.query(DBBlock.blocked_id).filter(DBBlock.blocker_id == user_id).all()
    return {r[0] for r in rows}


def _serialize_author(user: Optional[DBUser]) -> dict:
    if not user:
        return {"user_id": None, "name": "Unknown", "avatar_photo": None, "avatar_config": None}
    return {
        "user_id":       str(user.user_id),
        "name":          f"{user.first_name or ''} {user.last_name or ''}".strip() or "Player",
        "avatar_photo":  user.avatar_photo,
        "avatar_config": user.avatar_config,
        "host_rating":   float(user.host_rating) if user.host_rating else None,
    }


def _serialize_post(post: DBPost, db: Session, viewer_id: Optional[UUID]) -> dict:
    author = db.query(DBUser).filter(DBUser.user_id == post.author_id).first()
    my_reaction = None
    if viewer_id:
        r = db.query(DBPostReaction).filter_by(post_id=post.post_id, user_id=viewer_id).first()
        my_reaction = r.kind if r else None
    return {
        "post_id":        str(post.post_id),
        "author":         _serialize_author(author),
        "body":           post.body,
        "image_url":      post.image_url,
        "sport":          post.sport,
        "event_id":       str(post.event_id) if post.event_id else None,
        "latitude":       post.latitude,
        "longitude":      post.longitude,
        "place_label":    post.place_label,
        "like_count":     post.like_count,
        "downvote_count": post.downvote_count,
        "comment_count":  post.comment_count,
        "share_count":    post.share_count,
        "score":          post.score,
        "my_reaction":    my_reaction,
        "created_at":     post.created_at.isoformat() if post.created_at else None,
    }


def _recompute_score(post: DBPost) -> None:
    post.score = (post.like_count or 0) - (post.downvote_count or 0)


# ── Posts ─────────────────────────────────────────────────────────────────────

@router.post("/posts")
def create_post(
    body: PostCreate,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    if not body.body or not body.body.strip():
        raise HTTPException(status_code=400, detail="Post body is required")
    if profanity.contains_profanity(body.body):
        raise HTTPException(status_code=400, detail="Post contains inappropriate language")
    if len(body.body) > 2000:
        raise HTTPException(status_code=400, detail="Post is too long (2000 char max)")

    post = DBPost(
        author_id   = current_user.user_id,
        body        = body.body.strip(),
        image_url   = body.image_url,
        sport       = body.sport.lower().strip() if body.sport else None,
        event_id    = body.event_id,
        latitude    = body.latitude,
        longitude   = body.longitude,
        place_label = body.place_label,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return _serialize_post(post, db, current_user.user_id)


@router.get("/posts")
def list_posts(
    sort: str = Query("hot", pattern="^(hot|new|top)$"),
    sport: Optional[str] = Query(None),
    latitude: Optional[float] = Query(None),
    longitude: Optional[float] = Query(None),
    radius_miles: Optional[float] = Query(None),
    event_id: Optional[UUID] = Query(None),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    viewer_id = _user_id_from_header(authorization, db)
    blocked = _blocked_ids(db, viewer_id)

    q = db.query(DBPost)
    if sport:
        q = q.filter(DBPost.sport == sport.lower())
    if event_id:
        q = q.filter(DBPost.event_id == event_id)

    if sort == "new":
        q = q.order_by(DBPost.created_at.desc())
    elif sort == "top":
        q = q.order_by(DBPost.score.desc(), DBPost.created_at.desc())
    else:  # hot — blend score with recency
        q = q.order_by(DBPost.score.desc(), DBPost.created_at.desc())

    # Fetch a wider slice if we need to filter by radius (Haversine is
    # done in-Python for simplicity — swap for PostGIS when scale warrants).
    fetch_limit = limit * 4 if (latitude is not None and radius_miles) else limit
    posts = q.offset(offset).limit(fetch_limit + offset).all()

    result = []
    for post in posts:
        if post.author_id in blocked:
            continue
        if latitude is not None and longitude is not None and radius_miles:
            if post.latitude is None or post.longitude is None:
                continue
            if geodesic((latitude, longitude), (post.latitude, post.longitude)).miles > radius_miles:
                continue
        result.append(_serialize_post(post, db, viewer_id))
        if len(result) >= limit:
            break
    return {"posts": result}


@router.get("/posts/{post_id}")
def get_post(
    post_id: UUID,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    viewer_id = _user_id_from_header(authorization, db)
    post = db.query(DBPost).filter(DBPost.post_id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return _serialize_post(post, db, viewer_id)


@router.delete("/posts/{post_id}")
def delete_post(
    post_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    post = db.query(DBPost).filter(DBPost.post_id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.author_id != current_user.user_id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not allowed")
    db.delete(post)
    db.commit()
    return {"status": "deleted"}


# ── Reactions ─────────────────────────────────────────────────────────────────

@router.post("/posts/{post_id}/reactions")
def react_to_post(
    post_id: UUID,
    body: ReactionBody,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    kind = (body.kind or "").lower().strip()
    if kind not in ("like", "downvote"):
        raise HTTPException(status_code=400, detail="kind must be 'like' or 'downvote'")

    post = db.query(DBPost).filter(DBPost.post_id == post_id).with_for_update().first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    existing = db.query(DBPostReaction).filter_by(
        post_id=post_id, user_id=current_user.user_id
    ).first()

    if existing and existing.kind == kind:
        # Toggle off — remove reaction
        if kind == "like":
            post.like_count = max(0, post.like_count - 1)
        else:
            post.downvote_count = max(0, post.downvote_count - 1)
        db.delete(existing)
        _recompute_score(post)
        db.commit()
        return {"my_reaction": None, "like_count": post.like_count, "downvote_count": post.downvote_count, "score": post.score}

    if existing:
        # Switch reaction
        if existing.kind == "like":
            post.like_count = max(0, post.like_count - 1)
        else:
            post.downvote_count = max(0, post.downvote_count - 1)
        existing.kind = kind
    else:
        db.add(DBPostReaction(post_id=post_id, user_id=current_user.user_id, kind=kind))

    if kind == "like":
        post.like_count += 1
    else:
        post.downvote_count += 1
    _recompute_score(post)
    db.commit()
    return {"my_reaction": kind, "like_count": post.like_count, "downvote_count": post.downvote_count, "score": post.score}


# ── Comments ──────────────────────────────────────────────────────────────────

def _serialize_comment(comment: DBComment, db: Session, viewer_id: Optional[UUID]) -> dict:
    author = db.query(DBUser).filter(DBUser.user_id == comment.author_id).first()
    liked = False
    if viewer_id:
        liked = db.query(DBCommentLike).filter_by(
            comment_id=comment.comment_id, user_id=viewer_id
        ).first() is not None
    return {
        "comment_id": str(comment.comment_id),
        "post_id":    str(comment.post_id),
        "parent_id":  str(comment.parent_id) if comment.parent_id else None,
        "author":     _serialize_author(author),
        "body":       comment.body,
        "like_count": comment.like_count,
        "i_liked":    liked,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }


@router.post("/posts/{post_id}/comments")
def create_comment(
    post_id: UUID,
    body: CommentCreate,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    if not body.body or not body.body.strip():
        raise HTTPException(status_code=400, detail="Comment body is required")
    if profanity.contains_profanity(body.body):
        raise HTTPException(status_code=400, detail="Comment contains inappropriate language")
    if len(body.body) > 800:
        raise HTTPException(status_code=400, detail="Comment is too long")

    post = db.query(DBPost).filter(DBPost.post_id == post_id).with_for_update().first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if body.parent_id:
        parent = db.query(DBComment).filter(DBComment.comment_id == body.parent_id).first()
        if not parent or parent.post_id != post_id:
            raise HTTPException(status_code=400, detail="Invalid parent comment")

    comment = DBComment(
        post_id   = post_id,
        author_id = current_user.user_id,
        parent_id = body.parent_id,
        body      = body.body.strip(),
    )
    db.add(comment)
    post.comment_count = (post.comment_count or 0) + 1
    db.commit()
    db.refresh(comment)
    return _serialize_comment(comment, db, current_user.user_id)


@router.get("/posts/{post_id}/comments")
def list_comments(
    post_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    viewer_id = _user_id_from_header(authorization, db)
    blocked = _blocked_ids(db, viewer_id)
    comments = (
        db.query(DBComment)
        .filter(DBComment.post_id == post_id)
        .order_by(DBComment.created_at.asc())
        .limit(limit)
        .all()
    )
    return {"comments": [_serialize_comment(c, db, viewer_id) for c in comments if c.author_id not in blocked]}


@router.post("/comments/{comment_id}/like")
def like_comment(
    comment_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    comment = db.query(DBComment).filter(DBComment.comment_id == comment_id).with_for_update().first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    existing = db.query(DBCommentLike).filter_by(
        comment_id=comment_id, user_id=current_user.user_id
    ).first()
    if existing:
        db.delete(existing)
        comment.like_count = max(0, comment.like_count - 1)
        db.commit()
        return {"i_liked": False, "like_count": comment.like_count}

    db.add(DBCommentLike(comment_id=comment_id, user_id=current_user.user_id))
    comment.like_count = (comment.like_count or 0) + 1
    db.commit()
    return {"i_liked": True, "like_count": comment.like_count}


@router.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    comment = db.query(DBComment).filter(DBComment.comment_id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.author_id != current_user.user_id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not allowed")

    post = db.query(DBPost).filter(DBPost.post_id == comment.post_id).first()
    if post:
        post.comment_count = max(0, (post.comment_count or 0) - 1)
    db.delete(comment)
    db.commit()
    return {"status": "deleted"}


# ── Media upload ──────────────────────────────────────────────────────────────
# The client uploads an image / short video and receives a URL that it can
# then attach to a post via the normal create_post payload. Keeping upload
# and create separate keeps the create endpoint JSON-only.

@router.post("/media")
async def upload_post_media(
    file: UploadFile = File(...),
    current_user: DBUser = Depends(get_current_user),
):
    if file.content_type not in ALLOWED_POST_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Content-Type must be one of {ALLOWED_POST_CONTENT_TYPES}",
        )
    data = await file.read()
    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_POST_MEDIA_MB:
        raise HTTPException(status_code=400, detail=f"Media too large ({MAX_POST_MEDIA_MB}MB max)")
    if len(data) < 500:
        raise HTTPException(status_code=400, detail="File appears to be corrupt")

    import storage
    url = storage.upload_bytes(data, file.content_type, prefix="posts")
    if not url:
        # S3 not configured — keep working by returning a base64 data URL (dev only).
        # For videos this is not practical, but for small images it's fine.
        import base64 as _b64
        url = f"data:{file.content_type};base64," + _b64.b64encode(data).decode()

    return {"url": url, "content_type": file.content_type, "size_bytes": len(data)}


# ── Share counter ─────────────────────────────────────────────────────────────
# The in-app share action POSTs here so the feed can reflect trending posts.

@router.post("/posts/{post_id}/share")
def register_share(
    post_id: UUID,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    post = db.query(DBPost).filter(DBPost.post_id == post_id).with_for_update().first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    post.share_count = (post.share_count or 0) + 1
    db.commit()
    return {"share_count": post.share_count}
