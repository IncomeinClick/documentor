from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from backend.auth import require_auth
from backend.services.image_renderer import render_image, render_and_save
from backend.config import MEDIA_DIR, DEFAULT_IMAGE_SETTINGS
from pathlib import Path

router = APIRouter(prefix="/api/image", tags=["image"])


class RenderRequest(BaseModel):
    text: str
    settings: dict | None = None


@router.post("/preview")
async def preview_image(body: RenderRequest, _=Depends(require_auth)):
    """Render text to PNG and return bytes (no save)."""
    png = await render_image(body.text, body.settings)
    return Response(content=png, media_type="image/png")


@router.post("/download")
async def download_image(body: RenderRequest, _=Depends(require_auth)):
    """Render text to PNG and return as downloadable file."""
    png = await render_image(body.text, body.settings)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": "attachment; filename=headline.png"},
    )


@router.post("/render/{content_id}")
async def render_for_content(content_id: str, body: RenderRequest | None = None, _=Depends(require_auth)):
    """Render headline image for a content piece and save to media/."""
    text = body.text if body else ""
    settings = body.settings if body else None
    save_path = str(MEDIA_DIR / f"{content_id}.png")
    await render_and_save(text, save_path, settings)
    return {"ok": True, "path": save_path}


@router.get("/{content_id}")
async def get_content_image(content_id: str, _=Depends(require_auth)):
    """Serve a saved headline image."""
    path = MEDIA_DIR / f"{content_id}.png"
    if not path.exists():
        raise HTTPException(404, "Image not found")
    return Response(content=path.read_bytes(), media_type="image/png")
