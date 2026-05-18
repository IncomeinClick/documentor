from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import hashlib
import json
import base64
from backend.config import AUTH_USERNAME, AUTH_PASSWORD, SECRET_KEY

security = HTTPBearer(auto_error=False)


def _sign(payload: str) -> str:
    return hashlib.sha256(f"{payload}.{SECRET_KEY}".encode()).hexdigest()[:32]


def create_token(username: str) -> str:
    payload = {"sub": username, "exp": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = _sign(payload_b64)
    return f"{payload_b64}.{sig}"


def verify_token(token: str) -> str | None:
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        payload_b64, sig = parts
        if _sign(payload_b64) != sig:
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = datetime.fromisoformat(payload["exp"])
        if datetime.now(timezone.utc) > exp:
            return None
        return payload["sub"]
    except Exception:
        return None


def authenticate(username: str, password: str) -> str | None:
    if username == AUTH_USERNAME and password == AUTH_PASSWORD:
        return create_token(username)
    return None


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    # Check Bearer token
    if credentials:
        user = verify_token(credentials.credentials)
        if user:
            return user
        # Also accept raw password as bearer token (for Tim/API usage)
        if credentials.credentials == AUTH_PASSWORD:
            return AUTH_USERNAME
    # Check cookie
    token = request.cookies.get("token")
    if token:
        user = verify_token(token)
        if user:
            return user
    raise HTTPException(status_code=401, detail="Unauthorized")
