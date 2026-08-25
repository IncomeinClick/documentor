import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pathlib import Path
from backend.database import init_db
from backend.auth import authenticate, require_auth
from backend.routers import contents, blocks, image, projects, credentials, ai_image, ideas
from backend.services.scheduler import scheduler_loop
from pydantic import BaseModel

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()


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
