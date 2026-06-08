from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pathlib import Path
from pydantic import BaseModel, Field
import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from backend.database import init_db
from backend.auth import authenticate, require_auth
from backend.routers import contents, blocks, image, projects, credentials, ai_image, ideas
from backend.config import META_CAPI_ACCESS_TOKEN, META_CAPI_API_VERSION, META_PIXEL_ID

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Documentor v2", lifespan=lifespan)

# Routers
app.include_router(contents.router)
app.include_router(blocks.router)
app.include_router(image.router)
app.include_router(projects.router)
app.include_router(credentials.router)
app.include_router(ai_image.router)
app.include_router(ideas.router)


# Auth endpoints
class LoginRequest(BaseModel):
    username: str
    password: str


class TrackingEvent(BaseModel):
    event_name: str = Field(default="PageView", max_length=80)
    event_id: str = Field(..., max_length=120)
    event_source_url: str = Field(default="", max_length=2048)
    fbp: str | None = Field(default=None, max_length=255)
    fbc: str | None = Field(default=None, max_length=255)


def _client_ip(request: Request) -> str:
    return (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-real-ip")
        or (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
        or (request.client.host if request.client else "")
    )


def _send_meta_capi(payload: dict) -> dict:
    endpoint = f"https://graph.facebook.com/{META_CAPI_API_VERSION}/{META_PIXEL_ID}/events"
    body = urllib.parse.urlencode({
        "access_token": META_CAPI_ACCESS_TOKEN,
        "data": json.dumps([payload], separators=(",", ":")),
    }).encode()
    req = urllib.request.Request(endpoint, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


@app.post("/api/login")
async def login(body: LoginRequest):
    token = authenticate(body.username, body.password)
    if not token:
        return JSONResponse(status_code=401, content={"error": "Invalid credentials"})
    response = JSONResponse(content={"token": token})
    response.set_cookie("token", token, httponly=True, samesite="lax", max_age=30 * 86400)
    return response


@app.post("/api/logout")
async def logout():
    response = JSONResponse(content={"ok": True})
    response.delete_cookie("token")
    return response


@app.get("/api/me")
async def me(user: str = Depends(require_auth)):
    return {"user": user}


@app.post("/api/tracking/meta")
async def meta_tracking_event(body: TrackingEvent, request: Request):
    if not META_PIXEL_ID or not META_CAPI_ACCESS_TOKEN:
        return JSONResponse(status_code=503, content={"error": "Meta tracking is not configured"})
    if body.event_name != "PageView":
        return JSONResponse(status_code=400, content={"error": "Unsupported event"})

    user_data = {
        "client_ip_address": _client_ip(request),
        "client_user_agent": request.headers.get("user-agent", ""),
    }
    if body.fbp:
        user_data["fbp"] = body.fbp
    if body.fbc:
        user_data["fbc"] = body.fbc

    payload = {
        "event_name": body.event_name,
        "event_time": int(time.time()),
        "event_id": body.event_id,
        "event_source_url": body.event_source_url or str(request.url),
        "action_source": "website",
        "user_data": user_data,
    }
    try:
        result = await asyncio.to_thread(_send_meta_capi, payload)
    except urllib.error.HTTPError as exc:
        return JSONResponse(status_code=502, content={"error": exc.read().decode(errors="replace")[:500]})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)[:500]})
    return {"ok": True, "event_id": body.event_id, "meta": result}


# Serve media files
@app.get("/media/{filename}")
async def serve_media(filename: str):
    from backend.config import MEDIA_DIR
    path = MEDIA_DIR / filename
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return Response(content=path.read_bytes(), media_type="image/png")


@app.get("/media/_tmp/{filename}")
async def serve_media_tmp(filename: str):
    """Serve preview images for the AI infographic flow."""
    from backend.config import MEDIA_DIR
    if "/" in filename or ".." in filename:
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})
    path = MEDIA_DIR / "_tmp" / filename
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return Response(content=path.read_bytes(), media_type="image/png")


# Frontend routes
@app.get("/login")
async def login_page():
    path = FRONTEND_DIR / "login.html"
    if path.exists():
        return HTMLResponse(content=path.read_text())
    return HTMLResponse("<h1>Login page not found</h1>", status_code=500)


@app.get("/")
async def index():
    path = FRONTEND_DIR / "index.html"
    if path.exists():
        return HTMLResponse(content=path.read_text())
    return HTMLResponse("<h1>App not found</h1>", status_code=500)
