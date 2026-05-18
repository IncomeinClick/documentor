import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from backend.database import get_db
from backend.auth import require_auth
from backend.models import Project, new_id
from backend.config import DEFAULT_IMAGE_SETTINGS
from backend.services import telegram

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    slug: str | None = None
    credential_id: str | None = None
    language: str = "en"
    platforms: str = "fb"  # fb, ig, fb,ig
    image_settings: dict | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    credential_id: str | None = None
    language: str | None = None
    platforms: str | None = None
    image_settings: dict | None = None


@router.get("")
async def list_projects(db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    projects = result.scalars().all()
    return [_serialize(p) for p in projects]


@router.post("")
async def create_project(body: ProjectCreate, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    import re
    slug = body.slug or re.sub(r'[^a-z0-9]+', '-', body.name.lower()).strip('-')
    p = Project(
        id=new_id(),
        name=body.name,
        slug=slug,
        credential_id=body.credential_id,
        language=body.language,
        platforms=body.platforms,
        image_settings=json.dumps(body.image_settings or DEFAULT_IMAGE_SETTINGS),
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    result = _serialize(p)
    await telegram.notify_project_created(result)
    return result


@router.put("/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    p = await db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    if body.name is not None:
        p.name = body.name
    if body.slug is not None:
        p.slug = body.slug
    if body.credential_id is not None:
        p.credential_id = body.credential_id
    if body.language is not None:
        p.language = body.language
    if body.platforms is not None:
        p.platforms = body.platforms
    if body.image_settings is not None:
        p.image_settings = json.dumps(body.image_settings)
    await db.commit()
    await db.refresh(p)
    return _serialize(p)


@router.delete("/{project_id}")
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    p = await db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    await db.delete(p)
    await db.commit()
    return {"ok": True}


def _serialize(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "slug": p.slug,
        "credential_id": p.credential_id,
        "language": p.language or "en",
        "platforms": p.platforms or "fb",
        "image_settings": json.loads(p.image_settings) if p.image_settings else DEFAULT_IMAGE_SETTINGS,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
