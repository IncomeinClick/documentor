import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from backend.database import get_db
from backend.auth import require_auth
from backend.models import Content, Block, Project, Credential, new_id
from backend.services.image_renderer import render_and_save
from backend.services.fb_poster import post_photo, post_comment, get_ig_account, post_ig_photo, post_ig_comment
from backend.services import telegram, blog_queue
from backend.config import MEDIA_DIR, DEFAULT_IMAGE_SETTINGS

TMP_DIR = MEDIA_DIR / "_tmp"
BANGKOK_TZ = timezone(timedelta(hours=7))

router = APIRouter(prefix="/api/contents", tags=["contents"])


def bkk_now() -> datetime:
    """Naive Bangkok wall-clock time — the form scheduled_at is stored in."""
    return datetime.now(BANGKOK_TZ).replace(tzinfo=None)


class BlockInput(BaseModel):
    sort_order: int
    text: str
    image_url: str | None = None


class ContentCreate(BaseModel):
    project_id: str
    title: str
    caption: str | None = None
    source: str = "manual"
    blocks: list[BlockInput]
    image_method: str = "text_on_bg"  # text_on_bg | infographic_ai
    temp_image_id: str | None = None  # required when image_method=infographic_ai


class ContentUpdate(BaseModel):
    title: str | None = None
    caption: str | None = None
    status: str | None = None
    source: str | None = None


class ScheduleRequest(BaseModel):
    scheduled_at: str  # "YYYY-MM-DDTHH:MM" Bangkok local time, straight from the datetime-local input


@router.get("")
async def list_contents(
    project_id: str | None = None,
    status: str | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_auth),
):
    q = select(Content).order_by(Content.created_at.desc())
    if project_id:
        q = q.where(Content.project_id == project_id)
    if status:
        q = q.where(Content.status.in_(status.split(",")))  # accepts "draft,scheduled"
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    contents = result.scalars().all()

    # Also get total count
    count_q = select(func.count(Content.id))
    if project_id:
        count_q = count_q.where(Content.project_id == project_id)
    if status:
        count_q = count_q.where(Content.status.in_(status.split(",")))
    total = (await db.execute(count_q)).scalar()

    out = []
    for c in contents:
        blocks = await _get_blocks(db, c.id)
        out.append(_serialize(c, blocks))
    return {"items": out, "total": total, "limit": limit, "offset": offset}


@router.post("")
async def create_content(body: ContentCreate, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    # Verify project exists
    project = await db.get(Project, body.project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    # Validate: must have exactly one headline (sort_order=0)
    headlines = [b for b in body.blocks if b.sort_order == 0]
    if len(headlines) != 1:
        raise HTTPException(400, "Must have exactly one headline block (sort_order=0)")

    # Dedup: reject if same title+project was created within last 10 minutes
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    dup_query = select(Content).where(
        and_(
            Content.project_id == body.project_id,
            Content.title == body.title,
            Content.created_at >= cutoff,
        )
    )
    dup = (await db.execute(dup_query)).scalars().first()
    if dup:
        raise HTTPException(409, f"Duplicate: content with this title was created {dup.created_at} (id={dup.id})")

    if body.image_method not in ("text_on_bg", "infographic_ai"):
        raise HTTPException(400, "image_method must be 'text_on_bg' or 'infographic_ai'")

    # For infographic_ai, the AI-generated PNG (if any) lives in MEDIA_DIR/_tmp/{temp_id}.png —
    # move it to the permanent media path and link it to the headline block.
    # temp_image_id is OPTIONAL: allow saving an infographic draft without an image yet,
    # then attach an image later via /api/contents/{id}/attach-ai-image.
    permanent_image_path = None
    if body.image_method == "infographic_ai" and body.temp_image_id:
        src = TMP_DIR / f"{body.temp_image_id}.png"
        if not src.exists():
            raise HTTPException(400, "Temp image not found — it may have expired; regenerate")

    content = Content(
        id=new_id(),
        project_id=body.project_id,
        title=body.title,
        caption=body.caption,
        source=body.source,
        image_method=body.image_method,
    )
    db.add(content)

    if body.image_method == "infographic_ai" and body.temp_image_id:
        # Move temp PNG to /media/{content_id}.png so the existing serve_media route + execute flow can use it
        permanent_image_path = str(MEDIA_DIR / f"{content.id}.png")
        Path(permanent_image_path).write_bytes((TMP_DIR / f"{body.temp_image_id}.png").read_bytes())
        try:
            (TMP_DIR / f"{body.temp_image_id}.png").unlink()
        except FileNotFoundError:
            pass

    for b in body.blocks:
        image_path = permanent_image_path if (b.sort_order == 0 and permanent_image_path) else None
        block = Block(
            id=new_id(),
            content_id=content.id,
            sort_order=b.sort_order,
            text=b.text,
            image_url=b.image_url,
            image_path=image_path,
        )
        db.add(block)

    await db.commit()
    await db.refresh(content)
    blocks = await _get_blocks(db, content.id)
    result = _serialize(content, blocks)
    await telegram.notify_content_created(result, project.name)
    return result


@router.get("/{content_id}")
async def get_content(content_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    content = await db.get(Content, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    blocks = await _get_blocks(db, content.id)
    return _serialize(content, blocks)


@router.put("/{content_id}")
async def update_content(content_id: str, body: ContentUpdate, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    content = await db.get(Content, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    if body.title is not None:
        content.title = body.title
    if body.caption is not None:
        content.caption = body.caption
    if body.status is not None:
        content.status = body.status
    if body.source is not None:
        content.source = body.source
    content.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(content)
    blocks = await _get_blocks(db, content.id)
    return _serialize(content, blocks)


@router.delete("/{content_id}")
async def delete_content(content_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    content = await db.get(Content, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    # Delete blocks first
    blocks_result = await db.execute(select(Block).where(Block.content_id == content_id))
    for block in blocks_result.scalars().all():
        await db.delete(block)
    await db.delete(content)
    await db.commit()
    # Clean up image
    img = MEDIA_DIR / f"{content_id}.png"
    if img.exists():
        img.unlink()
    return {"ok": True}


class AttachAIImageRequest(BaseModel):
    temp_image_id: str
    hook: str | None = None


@router.post("/{content_id}/attach-ai-image")
async def attach_ai_image(content_id: str, body: AttachAIImageRequest, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    """Move a generated AI image (from /api/ai-image/preview) onto an existing draft.

    Used when Pond saved an infographic_ai draft first and runs Generate Image afterwards.
    Optionally updates the headline block's text to the new hook.
    """
    content = await db.get(Content, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    if content.image_method != "infographic_ai":
        raise HTTPException(400, "attach-ai-image only works on infographic_ai content")
    if content.status not in ("draft", "failed"):
        raise HTTPException(400, f"Cannot attach image to content with status '{content.status}'")

    src = TMP_DIR / f"{body.temp_image_id}.png"
    if not src.exists():
        raise HTTPException(400, "Temp image not found — it may have expired; regenerate")

    permanent_path = MEDIA_DIR / f"{content_id}.png"
    permanent_path.write_bytes(src.read_bytes())
    try:
        src.unlink()
    except FileNotFoundError:
        pass

    blocks = await _get_blocks(db, content.id)
    headline = next((b for b in blocks if b.sort_order == 0), None)
    if headline:
        headline.image_path = str(permanent_path)
        if body.hook and body.hook.strip():
            headline.text = body.hook.strip()
    content.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(content)
    return _serialize(content, await _get_blocks(db, content.id))


@router.post("/{content_id}/draft")
async def create_fb_draft(content_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    """Push the content to Facebook as an unpublished post (draft).
    Pond reviews / edits / schedules / publishes it from Meta Business Suite afterwards.
    Comments are NOT pushed — FB unpublished posts can't have comments yet; they're added in BS.
    """
    content = await db.get(Content, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    # Allow draft / failed (initial push) and posted_draft (re-push, useful when the previous
    # attempt was made by old code that didn't actually create a Business Suite draft).
    if content.status not in ("draft", "failed", "posted_draft"):
        raise HTTPException(400, f"Cannot create FB draft from status '{content.status}'")

    project = await db.get(Project, content.project_id)
    if not project or not project.credential_id:
        raise HTTPException(400, "Project has no credential assigned")
    cred = await db.get(Credential, project.credential_id)
    if not cred:
        raise HTTPException(400, "Credential not found")

    blocks = await _get_blocks(db, content.id)
    headline = next((b for b in blocks if b.sort_order == 0), None)
    if not headline:
        raise HTTPException(400, "No headline block found")

    img_path = headline.image_path
    # Render the headline image now if it doesn't exist yet (text_on_bg flow won't have one until execute)
    if not img_path or not Path(img_path).exists():
        if content.image_method == "infographic_ai":
            raise HTTPException(400, "Generate the infographic image first (🎨 Generate Image)")
        img_settings = json.loads(project.image_settings) if project.image_settings else DEFAULT_IMAGE_SETTINGS
        img_path = str(MEDIA_DIR / f"{content.id}.png")
        await render_and_save(headline.text, img_path, img_settings)
        headline.image_path = img_path

    try:
        fb_result = await post_photo(cred.page_id, cred.access_token, img_path, caption=content.caption or "", published=False)
        fb_post_id = fb_result.get("post_id") or fb_result.get("id")
        content.fb_post_id = fb_post_id
        content.status = "posted_draft"
        content.posted_at = datetime.now(timezone.utc)
        content.updated_at = datetime.now(timezone.utc)
        await db.commit()
    except Exception as e:
        content.status = "failed"
        headline.error_message = f"Create FB Draft failed: {e}"
        content.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await telegram.notify_execution_failed(_serialize(content, blocks), project.name, str(e))
        raise HTTPException(502, f"FB request failed: {e}")

    await db.refresh(content)
    blocks = await _get_blocks(db, content.id)
    result = _serialize(content, blocks)
    business_suite_url = f"https://business.facebook.com/latest/posts/drafts?asset_id={cred.page_id}"
    await telegram.send(
        f"📝 <b>FB Draft Created</b>\nProject: {project.name}\nContent: {content.title}\n"
        f"Open: {business_suite_url}"
    )
    return {**result, "business_suite_url": business_suite_url}


@router.post("/{content_id}/schedule")
async def schedule_content(content_id: str, body: ScheduleRequest, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    """Schedule the content — the background scheduler runs the normal execute flow when due.

    scheduled_at comes in as Bangkok wall-clock time and is stored that way.
    """
    content = await db.get(Content, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    if content.status not in ("draft", "failed", "scheduled"):
        raise HTTPException(400, f"Cannot schedule content with status '{content.status}'")

    try:
        when = datetime.fromisoformat(body.scheduled_at)
    except ValueError:
        raise HTTPException(400, "Invalid scheduled_at — expected YYYY-MM-DDTHH:MM")
    if when.tzinfo:
        when = when.astimezone(BANGKOK_TZ).replace(tzinfo=None)
    if when <= bkk_now():
        raise HTTPException(400, "เวลาที่ตั้งต้องเป็นอนาคต (เวลาไทย)")

    # Same precondition as execute — an AI infographic with no image would only fail at post time
    if content.image_method == "infographic_ai" and not (MEDIA_DIR / f"{content.id}.png").exists():
        raise HTTPException(400, "Generate the infographic image first (🎨 Generate Image)")

    content.scheduled_at = when
    content.status = "scheduled"
    content.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(content)

    result = _serialize(content, await _get_blocks(db, content.id))
    project = await db.get(Project, content.project_id)
    await telegram.notify_content_scheduled(result, project.name if project else content.project_id)
    return result


@router.post("/{content_id}/unschedule")
async def unschedule_content(content_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    """Cancel a schedule — back to draft, nothing posted."""
    content = await db.get(Content, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    if content.status != "scheduled":
        raise HTTPException(400, f"Content is not scheduled (status '{content.status}')")
    content.scheduled_at = None
    content.status = "draft"
    content.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(content)
    return _serialize(content, await _get_blocks(db, content.id))


@router.post("/{content_id}/execute")
async def execute_content(content_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    """Execute: render headline → post to FB/IG → comment blocks."""
    content = await db.get(Content, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    if content.status not in ("draft", "failed", "scheduled"):
        raise HTTPException(400, f"Cannot execute content with status '{content.status}'")
    return await _execute_content_internal(content_id, db)


async def _execute_content_internal(content_id: str, db: AsyncSession) -> dict:
    """Core execute logic — used by both HTTP endpoint and background scheduler."""
    content = await db.get(Content, content_id)
    if not content:
        raise ValueError(f"Content {content_id} not found")

    # Clear schedule fields
    content.scheduled_at = None

    # Get project + credential
    project = await db.get(Project, content.project_id)
    if not project or not project.credential_id:
        raise ValueError("Project has no credential assigned")
    cred = await db.get(Credential, project.credential_id)
    if not cred:
        raise ValueError("Credential not found")

    platforms = (project.platforms or "fb").split(",")

    # Get blocks
    blocks = await _get_blocks(db, content.id)
    headline = next((b for b in blocks if b.sort_order == 0), None)
    if not headline:
        raise ValueError("No headline block found")

    content.status = "executing"
    await db.commit()
    await telegram.notify_execution_started(content.title, project.name)

    max_retries = 3
    retry_delay = 5

    try:
        # 1. Render headline image (skip if already posted, or if infographic_ai already produced one)
        img_path = str(MEDIA_DIR / f"{content.id}.png")
        if content.image_method == "infographic_ai":
            # AI-generated PNG should already live at MEDIA_DIR/{content_id}.png from the create / attach step
            if not Path(img_path).exists():
                raise ValueError("Infographic image missing — generate it first via 🎨 Generate Image")
            if headline.status != "posted":
                headline.image_path = img_path
        else:
            img_settings = json.loads(project.image_settings) if project.image_settings else DEFAULT_IMAGE_SETTINGS
            if headline.status != "posted":
                await render_and_save(headline.text, img_path, img_settings)
                headline.image_path = img_path

        # ── FACEBOOK ──
        fb_post_id = content.fb_post_id  # Reuse existing post ID if available
        if "fb" in platforms:
            if headline.status != "posted" or not fb_post_id:
                fb_result = await post_photo(cred.page_id, cred.access_token, img_path, caption=content.caption or "")
                fb_post_id = fb_result.get("post_id") or fb_result.get("id")
                content.fb_post_id = fb_post_id
                await db.commit()

            # FB comments — only process pending/failed blocks, skip already posted
            comment_blocks = sorted([b for b in blocks if b.sort_order > 0 and b.status != "posted"], key=lambda b: b.sort_order)
            for b in comment_blocks:
                await asyncio.sleep(3)
                msg = b.text
                img = b.image_path or None
                last_error = None
                for attempt in range(1, max_retries + 1):
                    try:
                        result = await post_comment(fb_post_id, cred.access_token, msg, img)
                        b.fb_comment_id = result.get("id")
                        b.status = "posted"
                        last_error = None
                        break
                    except Exception as e:
                        last_error = e
                        if attempt < max_retries:
                            await asyncio.sleep(retry_delay)
                if last_error:
                    b.status = "failed"
                    b.error_message = f"FB failed after {max_retries} attempts: {last_error}"
                    await db.commit()
                    content.status = "failed"
                    content.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    await db.refresh(content)
                    blocks = await _get_blocks(db, content.id)
                    result = _serialize(content, blocks)
                    await telegram.notify_execution_failed(result, project.name, str(last_error))
                    return result
                await db.commit()

        # ── INSTAGRAM ──
        if "ig" in platforms:
            ig_account_id = await get_ig_account(cred.page_id, cred.access_token)
            if not ig_account_id:
                raise Exception("No Instagram business account linked to this page")

            # IG needs a public URL for the image
            image_url = f"https://documentor.incomeinclick.com/media/{content.id}.png"

            # Post photo to IG — skip if already posted
            if not content.ig_post_id:
                last_error = None
                for attempt in range(1, max_retries + 1):
                    try:
                        ig_result = await post_ig_photo(ig_account_id, cred.access_token, image_url, caption=content.caption or "")
                        content.ig_post_id = ig_result.get("id")
                        last_error = None
                        break
                    except Exception as e:
                        last_error = e
                        if attempt < max_retries:
                            await asyncio.sleep(retry_delay)
                if last_error:
                    content.status = "failed"
                    content.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    await db.refresh(content)
                    blocks = await _get_blocks(db, content.id)
                    result = _serialize(content, blocks)
                    await telegram.notify_execution_failed(result, project.name, f"IG photo failed: {last_error}")
                    return result
                await db.commit()

            # Post IG comments in reverse order — only process blocks without ig_comment_id
            ig_media_id = content.ig_post_id
            comment_blocks = sorted([b for b in blocks if b.sort_order > 0 and not b.ig_comment_id], key=lambda b: b.sort_order, reverse=True)
            for b in comment_blocks:
                await asyncio.sleep(3)
                msg = b.text
                last_error = None
                for attempt in range(1, max_retries + 1):
                    try:
                        ig_comment_result = await post_ig_comment(ig_media_id, cred.access_token, msg)
                        b.ig_comment_id = ig_comment_result.get("id")
                        b.status = "posted"
                        last_error = None
                        break
                    except Exception as e:
                        last_error = e
                        if attempt < max_retries:
                            await asyncio.sleep(retry_delay)
                if last_error:
                    b.status = "failed"
                    b.error_message = f"IG comment failed after {max_retries} attempts: {last_error}"
                    await db.commit()
                    content.status = "failed"
                    content.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    await db.refresh(content)
                    blocks = await _get_blocks(db, content.id)
                    result = _serialize(content, blocks)
                    await telegram.notify_execution_failed(result, project.name, str(last_error))
                    return result
                await db.commit()

        headline.status = "posted"
        content.status = "posted"
        content.posted_at = datetime.now(timezone.utc)
    except Exception as e:
        content.status = "failed"
        headline.status = "failed"
        headline.error_message = str(e)

    content.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(content)
    blocks = await _get_blocks(db, content.id)
    result = _serialize(content, blocks)
    if content.status == "posted":
        await telegram.notify_execution_success(result, project.name)
        await blog_queue.enqueue_if_th(result, project.id, project.name)
    elif content.status == "failed":
        await telegram.notify_execution_failed(result, project.name, headline.error_message or "Unknown error")
    return result


async def _get_blocks(db: AsyncSession, content_id: str) -> list[Block]:
    result = await db.execute(
        select(Block).where(Block.content_id == content_id).order_by(Block.sort_order)
    )
    return list(result.scalars().all())


def _serialize(c: Content, blocks: list[Block]) -> dict:
    return {
        "id": c.id,
        "project_id": c.project_id,
        "title": c.title,
        "caption": c.caption,
        "status": c.status,
        "image_method": getattr(c, "image_method", None) or "text_on_bg",
        "source": c.source,
        "fb_post_id": c.fb_post_id,
        "ig_post_id": c.ig_post_id,
        "scheduled_at": c.scheduled_at.isoformat() if c.scheduled_at else None,
        "posted_at": c.posted_at.isoformat() if c.posted_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "blocks": [
            {
                "id": b.id,
                "sort_order": b.sort_order,
                "text": b.text,
                "image_path": b.image_path,
                "image_url": b.image_url,
                "fb_comment_id": b.fb_comment_id,
                "ig_comment_id": b.ig_comment_id,
                "status": b.status,
                "error_message": b.error_message,
            }
            for b in blocks
        ],
    }
