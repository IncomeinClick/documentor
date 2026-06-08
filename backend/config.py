import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

PORT = int(os.getenv("PORT", "8100"))
HOST = os.getenv("HOST", "127.0.0.1")
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "pond")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./documentor.db")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
META_PIXEL_ID = os.getenv("META_PIXEL_ID", "")
META_CAPI_ACCESS_TOKEN = os.getenv("META_CAPI_ACCESS_TOKEN", "")
META_CAPI_API_VERSION = os.getenv("META_CAPI_API_VERSION", "v23.0")
MEDIA_DIR = BASE_DIR / "media"
MEDIA_DIR.mkdir(exist_ok=True)

DEFAULT_IMAGE_SETTINGS = {
    "font": "Sarabun",
    "font_weight": 700,
    "font_size": 90,
    "bg_color": "#000000",
    "text_color": "#FFFFFF",
    "text_shadow": "0 2px 4px rgba(0,0,0,0.3)",
    "width": 1200,
    "height": 900,
    "padding": 80,
    "line_height": 1.5,
}
