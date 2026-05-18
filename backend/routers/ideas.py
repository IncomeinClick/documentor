import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from backend.database import get_db
from backend.auth import require_auth
from backend.models import Idea, new_id

router = APIRouter(prefix="/api/ideas", tags=["ideas"])

RUN_FLAG = Path("/tmp/inc-content-running.flag")
RESEARCH_SCRIPT = "/root/scripts/inc-content-research.sh"


class IdeaItem(BaseModel):
    category: str
    sort_order: int = 0
    title: str
    url: str | None = None
    summary: str | None = None
    why_stand_out: str | None = None
    confidence: str | None = None


class IdeasBulk(BaseModel):
    research_date: str  # YYYY-MM-DD
    items: list[IdeaItem]


def _serialize(i: Idea) -> dict:
    return {
        "id": i.id,
        "research_date": i.research_date,
        "category": i.category,
        "sort_order": i.sort_order,
        "title": i.title,
        "url": i.url,
        "summary": i.summary,
        "why_stand_out": i.why_stand_out,
        "confidence": i.confidence,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "posted_to_en_at": i.posted_to_en_at.isoformat() if getattr(i, "posted_to_en_at", None) else None,
        "posted_to_th_at": i.posted_to_th_at.isoformat() if getattr(i, "posted_to_th_at", None) else None,
    }


@router.get("")
async def list_ideas(db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    """Return last 30 days of ideas grouped by date (newest first)."""
    cutoff = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    q = (
        select(Idea)
        .where(Idea.research_date >= cutoff)
        .order_by(Idea.research_date.desc(), Idea.category, Idea.sort_order)
    )
    rows = (await db.execute(q)).scalars().all()

    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r.research_date, []).append(_serialize(r))

    out = [{"date": d, "ideas": grouped[d]} for d in sorted(grouped.keys(), reverse=True)]
    return {"days": out}


@router.post("/bulk")
async def bulk_insert(
    body: IdeasBulk,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_auth),
):
    """Bulk insert ideas for one research_date. Replaces same-date rows."""
    # Wipe existing rows for this date (re-run case)
    await db.execute(delete(Idea).where(Idea.research_date == body.research_date))

    for item in body.items:
        idea = Idea(
            id=new_id(),
            research_date=body.research_date,
            category=item.category,
            sort_order=item.sort_order,
            title=item.title,
            url=item.url,
            summary=item.summary,
            why_stand_out=item.why_stand_out,
            confidence=item.confidence,
        )
        db.add(idea)

    # Auto-prune older than 30 days
    cutoff = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    await db.execute(delete(Idea).where(Idea.research_date < cutoff))

    await db.commit()
    return {"inserted": len(body.items), "research_date": body.research_date}


@router.post("/run")
async def run_research(_=Depends(require_auth)):
    """Fire-and-forget trigger for the research script."""
    if RUN_FLAG.exists():
        raise HTTPException(409, "Research already running")
    if not Path(RESEARCH_SCRIPT).exists():
        raise HTTPException(500, f"Script not found: {RESEARCH_SCRIPT}")

    started_at = datetime.now(timezone.utc).isoformat()
    subprocess.Popen(
        [RESEARCH_SCRIPT],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {
        "started_at": started_at,
        "message": "Research started. Ideas will appear in 3-5 minutes.",
    }


@router.get("/run/status")
async def run_status(db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    """Return current run status + last completion."""
    is_running = RUN_FLAG.exists()
    flag_started_at = None
    if is_running:
        try:
            flag_started_at = datetime.fromtimestamp(RUN_FLAG.stat().st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            pass

    # Last completed = most recent created_at across ideas
    last_q = select(Idea).order_by(Idea.created_at.desc()).limit(1)
    last_row = (await db.execute(last_q)).scalar_one_or_none()
    last_completed_at = last_row.created_at.isoformat() if last_row and last_row.created_at else None
    last_research_date = last_row.research_date if last_row else None

    if is_running:
        status = "running"
    elif last_completed_at:
        status = "completed"
    else:
        status = "never_run"

    return {
        "status": status,
        "started_at": flag_started_at,
        "last_completed_at": last_completed_at,
        "last_research_date": last_research_date,
    }
