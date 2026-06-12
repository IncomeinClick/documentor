"""
Auto-sync: When Pond manually posts to FB TH page,
replicate to IG TH + FB EN (translated) + IG EN (translated).

Runs via cron every hour. Skips posts already made by Documentor.
"""
import sqlite3
import json
import os
import sys
import subprocess
import urllib.request
import urllib.parse
import time
import logging
from pathlib import Path
from datetime import datetime

# ── Config ──
DB_PATH = "/opt/documentor/documentor.db"
SYNCED_FILE = "/opt/documentor/synced_posts.json"
MEDIA_DIR = "/opt/documentor/media"
MEDIA_BASE_URL = "https://documentor.incomeinclick.com/media"
# codex (subscription auth, NOT API key) replaces claude -p — gpt-5.4 low effort.
# read-only sandbox: the text to translate is in the prompt, no file/network access needed.
_CODEX_NODE_BIN = "/root/.nvm/versions/node/v22.22.3/bin"
CODEX_ENV = {**os.environ, "PATH": _CODEX_NODE_BIN + ":" + os.environ.get("PATH", ""), "HOME": "/root"}
CODEX_CMD = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
             "-m", "gpt-5.4", "-c", "model_reasoning_effort=low", "-c", "approval_policy=never"]
GRAPH_API = "https://graph.facebook.com/v20.0"
LOG_FILE = "/var/log/sync_posts.log"

# Telegram notification
TG_BOT_TOKEN = None  # loaded from .env
TG_CHAT_ID = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("sync_posts")


def load_env():
    """Load Telegram config from Documentor .env"""
    global TG_BOT_TOKEN, TG_CHAT_ID
    env_path = Path("/opt/documentor/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k == "TELEGRAM_BOT_TOKEN":
                    TG_BOT_TOKEN = v
                elif k == "TELEGRAM_CHAT_ID":
                    TG_CHAT_ID = v


def send_telegram(message: str):
    """Send notification to Telegram group"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning(f"Telegram notification failed: {e}")


def get_credentials():
    """Get TH and EN page credentials from Documentor DB"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    creds = {}
    c.execute("SELECT id, page_id, access_token FROM credentials")
    for row in c.fetchall():
        creds[row[0]] = {"page_id": row[1], "access_token": row[2]}
    conn.close()
    return creds


def get_documentor_post_ids():
    """Get all fb_post_ids that Documentor has posted (to TH page)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT fb_post_id FROM contents WHERE fb_post_id IS NOT NULL")
    ids = {row[0] for row in c.fetchall()}
    conn.close()
    return ids


def get_synced_post_ids():
    """Get post IDs that we've already synced"""
    if not os.path.exists(SYNCED_FILE):
        return set()
    with open(SYNCED_FILE, "r") as f:
        return set(json.load(f))


def save_synced_post_ids(ids: set):
    """Save synced post IDs — keep only last 200 to avoid bloat"""
    id_list = sorted(ids)[-200:]
    with open(SYNCED_FILE, "w") as f:
        json.dump(id_list, f)


def fetch_th_feed(page_id: str, access_token: str, limit: int = 10):
    """Read recent posts from TH FB page"""
    url = (
        f"{GRAPH_API}/{page_id}/feed"
        f"?fields=id,message,created_time,full_picture"
        f"&limit={limit}"
        f"&access_token={access_token}"
    )
    req = urllib.request.Request(url)
    resp = urllib.request.urlopen(req, timeout=30)
    data = json.loads(resp.read())
    return data.get("data", [])


def download_image(image_url: str, save_path: str) -> bool:
    """Download image from URL to local path"""
    try:
        req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=30)
        with open(save_path, "wb") as f:
            f.write(resp.read())
        return True
    except Exception as e:
        log.error(f"Failed to download image: {e}")
        return False


def translate_caption(thai_text: str) -> str:
    """Use Claude CLI to translate Thai caption to English"""
    prompt = (
        "Translate this Thai social media caption to English. "
        "Keep the same tone, style, and any emojis. "
        "Only output the translation, nothing else:\n\n"
        f"{thai_text}"
    )
    try:
        result = subprocess.run(
            CODEX_CMD + [prompt],
            capture_output=True,
            text=True,
            timeout=120,
            env=CODEX_ENV,
        )
        if result.returncode == 0:
            translated = result.stdout.strip()
            if translated:
                return translated
        log.error(f"codex CLI error: {result.stderr}")
    except subprocess.TimeoutExpired:
        log.error("codex CLI timed out")
    except Exception as e:
        log.error(f"Claude CLI failed: {e}")
    return ""


def post_fb_photo(page_id: str, access_token: str, image_path: str, caption: str = "") -> str | None:
    """Post photo to Facebook page, return post_id"""
    url = f"{GRAPH_API}/{page_id}/photos"
    boundary = "----SyncPostBoundary"
    body = []
    if caption:
        body.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"message\"\r\n\r\n{caption}")
    body.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"access_token\"\r\n\r\n{access_token}")
    with open(image_path, "rb") as f:
        img_data = f.read()
    body_bytes = ("\r\n".join(body) + "\r\n").encode()
    body_bytes += f"--{boundary}\r\nContent-Disposition: form-data; name=\"source\"; filename=\"image.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
    body_bytes += img_data
    body_bytes += f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=body_bytes)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.loads(resp.read())
        return data.get("post_id") or data.get("id")
    except Exception as e:
        log.error(f"FB post failed: {e}")
        return None


def post_ig_photo(page_id: str, access_token: str, image_url: str, caption: str = "") -> str | None:
    """Post photo to Instagram via FB Graph API, return media_id"""
    # Step 1: Get IG account ID
    url = f"{GRAPH_API}/{page_id}?fields=instagram_business_account&access_token={access_token}"
    try:
        resp = urllib.request.urlopen(url, timeout=30)
        data = json.loads(resp.read())
        ig_id = data.get("instagram_business_account", {}).get("id")
        if not ig_id:
            log.error("No IG account linked to page")
            return None
    except Exception as e:
        log.error(f"Failed to get IG account: {e}")
        return None

    # Step 2: Create media container
    params = {
        "image_url": image_url,
        "access_token": access_token,
    }
    if caption:
        params["caption"] = caption
    create_url = f"{GRAPH_API}/{ig_id}/media?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(create_url, method="POST")
        resp = urllib.request.urlopen(req, timeout=60)
        container = json.loads(resp.read())
        container_id = container.get("id")
    except Exception as e:
        log.error(f"IG container creation failed: {e}")
        return None

    # Step 3: Wait for processing
    for _ in range(15):
        time.sleep(3)
        status_url = f"{GRAPH_API}/{container_id}?fields=status_code&access_token={access_token}"
        try:
            resp = urllib.request.urlopen(status_url, timeout=10)
            status = json.loads(resp.read())
            if status.get("status_code") == "FINISHED":
                break
        except Exception:
            pass

    # Step 4: Publish
    publish_url = f"{GRAPH_API}/{ig_id}/media_publish?creation_id={container_id}&access_token={access_token}"
    try:
        req = urllib.request.Request(publish_url, method="POST")
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read())
        return result.get("id")
    except Exception as e:
        log.error(f"IG publish failed: {e}")
        return None


def get_full_size_image(post_id: str, access_token: str) -> str | None:
    """Get full-size image from a FB post via attachments"""
    url = f"{GRAPH_API}/{post_id}?fields=attachments{{media}}&access_token={access_token}"
    try:
        resp = urllib.request.urlopen(url, timeout=30)
        data = json.loads(resp.read())
        attachments = data.get("attachments", {}).get("data", [])
        if attachments:
            media = attachments[0].get("media", {})
            image = media.get("image", {})
            return image.get("src")
    except Exception as e:
        log.warning(f"Could not get full-size image: {e}")
    return None


SCHEDULED_SYNCS_FILE = "/opt/documentor/scheduled_syncs.json"


def load_scheduled_syncs():
    """Load scheduled sync entries"""
    if not os.path.exists(SCHEDULED_SYNCS_FILE):
        return []
    with open(SCHEDULED_SYNCS_FILE, "r") as f:
        return json.load(f)


def save_scheduled_syncs(entries: list):
    with open(SCHEDULED_SYNCS_FILE, "w") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def process_scheduled_syncs(creds: dict, synced_ids: set):
    """Process any scheduled syncs that are due today"""
    entries = load_scheduled_syncs()
    if not entries:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    due = [e for e in entries if e["date"] <= today and not e.get("done")]
    if not due:
        return

    log.info(f"Found {len(due)} scheduled sync(s) due today")
    th_cred = creds.get("income-in-click-th")
    en_cred = creds.get("income-in-click")

    for entry in due:
        post_id = entry["post_id"]
        if post_id in synced_ids:
            entry["done"] = True
            continue

        log.info(f"Processing scheduled sync: {post_id}")
        sync_single_post(post_id, th_cred, en_cred)
        synced_ids.add(post_id)
        save_synced_post_ids(synced_ids)
        entry["done"] = True
        save_scheduled_syncs(entries)
        time.sleep(5)

    save_scheduled_syncs(entries)


def sync_single_post(post_id: str, th_cred: dict, en_cred: dict):
    """Sync a single FB TH post to IG TH + FB EN + IG EN"""
    # Get post details from Graph API
    url = (
        f"{GRAPH_API}/{post_id}"
        f"?fields=message,full_picture"
        f"&access_token={th_cred['access_token']}"
    )
    try:
        resp = urllib.request.urlopen(url, timeout=30)
        post = json.loads(resp.read())
    except Exception as e:
        log.error(f"Failed to fetch post {post_id}: {e}")
        return

    caption_th = post.get("message", "")
    image_url = post.get("full_picture")

    if not image_url:
        log.info(f"Skipping {post_id} — no image")
        return

    # Get full-size image
    full_image = get_full_size_image(post_id, th_cred["access_token"])
    dl_url = full_image or image_url

    # Download image
    safe_id = post_id.replace(":", "_")
    img_path = f"{MEDIA_DIR}/sync-{safe_id}.png"
    if not download_image(dl_url, img_path):
        log.error(f"Failed to download image for {post_id}")
        return

    img_filename = f"sync-{safe_id}.png"
    ig_image_url = f"{MEDIA_BASE_URL}/{img_filename}"

    # Translate caption
    caption_en = ""
    if caption_th:
        log.info("Translating caption...")
        caption_en = translate_caption(caption_th)
        if caption_en:
            log.info(f"Translated: {caption_en[:50]}...")

    results = []

    # 1. IG TH
    log.info("Posting to IG TH...")
    ig_th = post_ig_photo(th_cred["page_id"], th_cred["access_token"], ig_image_url, caption_th)
    results.append(("IG TH", ig_th))

    # 2. FB EN
    log.info("Posting to FB EN...")
    fb_en = post_fb_photo(en_cred["page_id"], en_cred["access_token"], img_path, caption_en)
    results.append(("FB EN", fb_en))

    # 3. IG EN
    log.info("Posting to IG EN...")
    ig_en = post_ig_photo(en_cred["page_id"], en_cred["access_token"], ig_image_url, caption_en)
    results.append(("IG EN", ig_en))

    # Summary
    success = [r[0] for r in results if r[1]]
    failed = [r[0] for r in results if not r[1]]
    log.info(f"Post {post_id} synced: {', '.join(success) if success else 'none'}")
    if failed:
        log.warning(f"Failed channels: {', '.join(failed)}")

    # Telegram notification
    status_lines = []
    for name, result_id in results:
        icon = "✅" if result_id else "❌"
        status_lines.append(f"  {icon} {name}")
    msg = (
        f"🔄 <b>Post Auto-Synced</b>\n\n"
        f"📝 {caption_th[:80] if caption_th else '(no caption)'}...\n"
        f"🌐 {caption_en[:80] if caption_en else '(no translation)'}...\n\n"
        + "\n".join(status_lines)
    )
    send_telegram(msg)


def main():
    load_env()
    log.info("=== Sync posts started ===")

    creds = get_credentials()
    th_cred = creds.get("income-in-click-th")
    en_cred = creds.get("income-in-click")

    if not th_cred or not en_cred:
        log.error("Missing credentials")
        return

    # Get posts to skip
    documentor_ids = get_documentor_post_ids()
    synced_ids = get_synced_post_ids()
    skip_ids = documentor_ids | synced_ids

    # Process scheduled syncs first
    process_scheduled_syncs(creds, synced_ids)

    # Fetch TH feed for new manual posts
    posts = fetch_th_feed(th_cred["page_id"], th_cred["access_token"])
    log.info(f"Fetched {len(posts)} posts from TH feed")

    # Reload synced_ids in case scheduled syncs added some
    synced_ids = get_synced_post_ids()
    # Also skip posts that are scheduled for future dates
    scheduled_ids = {e["post_id"] for e in load_scheduled_syncs() if not e.get("done")}
    skip_ids = documentor_ids | synced_ids | scheduled_ids

    new_posts = [p for p in posts if p["id"] not in skip_ids]
    if not new_posts:
        log.info("No new manual posts to sync")
        return

    log.info(f"Found {len(new_posts)} new manual post(s) to sync")

    for post in new_posts:
        post_id = post["id"]
        caption_th = post.get("message", "")
        log.info(f"Processing post {post_id}: {caption_th[:50]}...")

        sync_single_post(post_id, th_cred, en_cred)
        synced_ids.add(post_id)
        save_synced_post_ids(synced_ids)
        time.sleep(5)

    log.info("=== Sync posts completed ===")


if __name__ == "__main__":
    main()
