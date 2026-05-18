from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from backend.database import get_db
from backend.auth import require_auth
from backend.models import Credential
from backend.services.fb_poster import verify_page

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


class CredentialCreate(BaseModel):
    id: str
    name: str
    page_id: str
    access_token: str


@router.get("")
async def list_credentials(db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    result = await db.execute(select(Credential).order_by(Credential.created_at.desc()))
    creds = result.scalars().all()
    return [_serialize(c) for c in creds]


@router.post("")
async def create_credential(body: CredentialCreate, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    existing = await db.get(Credential, body.id)
    if existing:
        raise HTTPException(409, "Credential ID already exists")
    c = Credential(id=body.id, name=body.name, page_id=body.page_id, access_token=body.access_token)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return _serialize(c)


@router.post("/{cred_id}/test")
async def test_credential(cred_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    c = await db.get(Credential, cred_id)
    if not c:
        raise HTTPException(404, "Credential not found")
    try:
        info = await verify_page(c.page_id, c.access_token)
        return {"ok": True, "page_name": info.get("name"), "page_id": info.get("id")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.delete("/{cred_id}")
async def delete_credential(cred_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_auth)):
    c = await db.get(Credential, cred_id)
    if not c:
        raise HTTPException(404, "Credential not found")
    await db.delete(c)
    await db.commit()
    return {"ok": True}


def _serialize(c: Credential) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "page_id": c.page_id,
        "access_token": c.access_token[:8] + "..." if c.access_token else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
