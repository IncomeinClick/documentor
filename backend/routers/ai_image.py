"""AI infographic image generation endpoints — 2-step flow.

Step 1 (cheap, ~3s): POST /api/ai-image/hook       → {hook}
Step 2 (slow, ~70s): POST /api/ai-image/image      → {temp_id, prompt, image_url}
                     POST /api/ai-image/image again → regenerates (reuses temp_id if provided)
                     DELETE /api/ai-image/temp/{temp_id} → cleanup
"""
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import require_auth
from backend.database import get_db
from backend.models import Project
from backend.config import MEDIA_DIR
from backend.services import ai_image_generator

router = APIRouter(prefix="/api/ai-image", tags=["ai-image"])

TMP_DIR = MEDIA_DIR / "_tmp"
TMP_DIR.mkdir(exist_ok=True)


class HookRequest(BaseModel):
    project_id: str
    caption: str


class ImageRequest(BaseModel):
    project_id: str
    caption: str
    hook: str
    prompt: str | None = None       # if null, GPT writes one from caption+hook
    temp_id: str | None = None      # if set, overwrite that temp file (regenerate)


def _tmp_path(temp_id: str) -> Path:
    if not temp_id or "/" in temp_id or ".." in temp_id:
        raise HTTPException(400, "Invalid temp_id")
    return TMP_DIR / f"{temp_id}.png"


async def _get_lang(db: AsyncSession, project_id: str) -> str:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project.language or "th"


@router.post("/hook")
async def hook(body: HookRequest, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    if not body.caption or not body.caption.strip():
        raise HTTPException(400, "Caption is required")
    lang = await _get_lang(db, body.project_id)
    h = await ai_image_generator.generate_hook(body.caption, lang)
    return {"hook": h}


@router.post("/image")
async def image(body: ImageRequest, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    if not body.caption or not body.hook:
        raise HTTPException(400, "caption and hook are required")
    lang = await _get_lang(db, body.project_id)

    prompt = body.prompt.strip() if body.prompt and body.prompt.strip() else \
        await ai_image_generator.generate_image_prompt(body.caption, body.hook, lang)
    img_bytes = await ai_image_generator.generate_image(prompt)

    temp_id = body.temp_id or str(uuid.uuid4())
    path = _tmp_path(temp_id)
    if path.exists():
        path.unlink()
    path.write_bytes(img_bytes)
    return {
        "temp_id": temp_id,
        "hook": body.hook,
        "prompt": prompt,
        "image_url": f"/media/_tmp/{temp_id}.png",
    }


@router.delete("/temp/{temp_id}")
async def delete_temp(temp_id: str, _=Depends(require_auth)):
    p = _tmp_path(temp_id)
    if p.exists():
        p.unlink()
    return {"ok": True}
