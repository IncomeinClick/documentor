"""Background scheduler — posts contents whose scheduled_at is due.

scheduled_at is stored as naive BANGKOK wall-clock time (see models.Content), so every
comparison here uses bkk_now(), never datetime.utcnow().

A due content runs through the exact same path as pressing Execute in the UI
(_execute_content_internal): headline image → FB post → comments → IG.
If the service was down past LATE_GRACE the post is NOT published — a stale post going
out at the wrong hour is worse than no post — it's marked failed and Pond gets a Telegram.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.database import async_session
from backend.models import Content, Project
from backend.routers.contents import _execute_content_internal, bkk_now
from backend.services import telegram

CHECK_INTERVAL_SECONDS = 60
LATE_GRACE = timedelta(hours=6)


async def run_due_contents():
    """One tick: publish everything that is due, skip what is too late."""
    async with async_session() as db:
        now = bkk_now()
        result = await db.execute(
            select(Content).where(Content.status == "scheduled", Content.scheduled_at <= now)
        )
        due = result.scalars().all()
        for content in due:
            late_by = now - content.scheduled_at
            project = await db.get(Project, content.project_id)
            project_name = project.name if project else content.project_id

            if late_by > LATE_GRACE:
                content.status = "failed"
                content.scheduled_at = None
                content.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await telegram.send(
                    f"⏰ <b>Scheduled Post Skipped</b>\n"
                    f"Project: {project_name}\n"
                    f"Content: {content.title}\n"
                    f"เลยเวลามา {int(late_by.total_seconds() // 3600)} ชม. (เกิน 6 ชม.) — ไม่โพสต์ให้อัตโนมัติ "
                    f"กด Execute เองถ้ายังต้องการโพสต์"
                )
                continue

            try:
                await _execute_content_internal(content.id, db)
            except Exception as e:
                content.status = "failed"
                content.scheduled_at = None
                content.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await telegram.send(
                    f"❌ <b>Scheduled Post Failed</b>\n"
                    f"Project: {project_name}\n"
                    f"Content: {content.title}\n{e}"
                )


async def scheduler_loop():
    while True:
        try:
            await run_due_contents()
        except Exception:
            pass  # a bad tick must never kill the loop
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
