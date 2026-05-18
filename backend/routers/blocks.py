import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from backend.database import get_db
from backend.auth import require_auth
from backend.models import Block, Content, new_id
from backend.config import MEDIA_DIR

router = APIRouter(tags=["blocks"])


class BlockCreate(BaseModel):
    sort_order: int
    text: str
    image_url: str | None = None


class BlockUpdate(BaseModel):
    sort_order: int | None = None
    text: str | None = None
    image_url: str | None = None


class BulkBlockInput(BaseModel):
    id: str | None = None
    sort_order: int
    text: str
    image_url: str | None = None


class BulkBlockUpdate(BaseModel):
    blocks: list[BulkBlockInput]


@router.put("/api/contents/{content_id}/blocks")
async def bulk_update_blocks(content_id: str, body: BulkBlockUpdate, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    """Replace all blocks for a content piece."""
    content = await db.get(Content, content_id)
    if not content:
        raise HTTPException(404, "Content not found")

    # Build lookup of existing blocks by sort_order to preserve image_path
    existing = await db.execute(select(Block).where(Block.content_id == content_id))
    existing_blocks = existing.scalars().all()
    old_by_sort = {b.sort_order: b for b in existing_blocks}

    # Delete existing blocks
    for b in existing_blocks:
        await db.delete(b)

    # Create new blocks, preserving image_path from old blocks with same sort_order
    new_blocks = []
    for b in body.blocks:
        old = old_by_sort.get(b.sort_order)
        block = Block(
            id=b.id or new_id(),
            content_id=content_id,
            sort_order=b.sort_order,
            text=b.text,
            image_url=b.image_url,
            image_path=old.image_path if old else None,
        )
        db.add(block)
        new_blocks.append(block)

    await db.commit()
    return [_serialize(b) for b in new_blocks]


@router.post("/api/contents/{content_id}/blocks")
async def add_block(content_id: str, body: BlockCreate, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    content = await db.get(Content, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    block = Block(id=new_id(), content_id=content_id, sort_order=body.sort_order, text=body.text, image_url=body.image_url)
    db.add(block)
    await db.commit()
    await db.refresh(block)
    return _serialize(block)


@router.put("/api/blocks/{block_id}")
async def update_block(block_id: str, body: BlockUpdate, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    block = await db.get(Block, block_id)
    if not block:
        raise HTTPException(404, "Block not found")
    if body.sort_order is not None:
        block.sort_order = body.sort_order
    if body.text is not None:
        block.text = body.text
    if body.image_url is not None:
        block.image_url = body.image_url
    await db.commit()
    await db.refresh(block)
    return _serialize(block)


@router.delete("/api/blocks/{block_id}")
async def delete_block(block_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    block = await db.get(Block, block_id)
    if not block:
        raise HTTPException(404, "Block not found")
    await db.delete(block)
    await db.commit()
    return {"ok": True}


@router.post("/api/blocks/{block_id}/upload-image")
async def upload_block_image(block_id: str, file: UploadFile = File(...), db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    block = await db.get(Block, block_id)
    if not block:
        raise HTTPException(404, "Block not found")
    ext = file.filename.split(".")[-1] if file.filename else "png"
    save_path = MEDIA_DIR / f"block-{block_id}.{ext}"
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    block.image_path = str(save_path)
    await db.commit()
    await db.refresh(block)
    return _serialize(block)


def _serialize(b: Block) -> dict:
    return {
        "id": b.id,
        "content_id": b.content_id,
        "sort_order": b.sort_order,
        "text": b.text,
        "image_path": b.image_path,
        "image_url": b.image_url,
        "status": b.status,
    }
