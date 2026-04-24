"""
Admin moderation endpoints. Requires DBUser.is_admin = True.
Promote your first admin manually in Supabase:
    UPDATE users SET is_admin = true WHERE email = 'you@example.com';
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import get_admin_user
from database import get_db
from models.db_user import DBUser
from models.db_report import DBReport
from models.db_event import DBEvent
from models.db_archived_event import DBArchivedEvent

router = APIRouter(prefix="/admin", tags=["admin"])

VALID_STATUSES = {"pending", "reviewed", "dismissed", "actioned"}


class ReviewBody(BaseModel):
    status: str  # 'reviewed' | 'dismissed' | 'actioned'
    notes: Optional[str] = None


@router.get("/reports")
def list_reports(
    status: str = Query(default="pending"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: DBUser = Depends(get_admin_user),
):
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(VALID_STATUSES)}")

    rows = (
        db.query(DBReport)
        .filter(DBReport.status == status)
        .order_by(DBReport.created_at.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    out = []
    for r in rows:
        reporter = db.query(DBUser).filter(DBUser.user_id == r.reporter_id).first()
        target_summary = _describe_target(db, r.target_type, r.target_id)
        out.append({
            "report_id": str(r.report_id),
            "reporter": {
                "user_id": str(r.reporter_id),
                "email": reporter.email if reporter else None,
            },
            "target_type": r.target_type,
            "target_id": str(r.target_id),
            "target": target_summary,
            "reason": r.reason,
            "details": r.details,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "review_notes": r.review_notes,
        })
    return {"reports": out, "limit": limit, "offset": offset}


@router.post("/reports/{report_id}/review")
def review_report(
    report_id: UUID,
    body: ReviewBody,
    db: Session = Depends(get_db),
    admin: DBUser = Depends(get_admin_user),
):
    if body.status not in {"reviewed", "dismissed", "actioned"}:
        raise HTTPException(status_code=400, detail="status must be reviewed, dismissed, or actioned")

    report = db.query(DBReport).filter(DBReport.report_id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    report.status = body.status
    report.reviewed_at = datetime.now(timezone.utc)
    report.reviewed_by = admin.user_id
    report.review_notes = body.notes
    db.commit()
    return {"report_id": str(report.report_id), "status": report.status}


@router.get("/reports/stats")
def report_stats(
    db: Session = Depends(get_db),
    _admin: DBUser = Depends(get_admin_user),
):
    counts = {s: db.query(DBReport).filter(DBReport.status == s).count() for s in VALID_STATUSES}
    return {"counts": counts}


def _describe_target(db: Session, target_type: str, target_id: UUID) -> dict:
    """Best-effort summary of the reported entity, for admin context."""
    if target_type == "user":
        u = db.query(DBUser).filter(DBUser.user_id == target_id).first()
        if not u:
            return {"deleted": True}
        return {
            "email": u.email,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "is_active": u.is_active,
        }
    if target_type == "event":
        e = db.query(DBEvent).filter(DBEvent.event_id == target_id).first()
        if e:
            return {"title": e.title, "sport": e.sport, "organizer_id": str(e.organizer_id) if e.organizer_id else None, "archived": False}
        a = db.query(DBArchivedEvent).filter(DBArchivedEvent.event_id == target_id).first()
        if a:
            return {"title": a.title, "sport": a.sport, "organizer_id": str(a.organizer_id) if a.organizer_id else None, "archived": True}
        return {"deleted": True}
    return {}
