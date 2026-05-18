import os
import urllib.request
import json
import asyncio

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
POND_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _send_sync(text: str, parse_mode: str = "HTML"):
    if not BOT_TOKEN or not POND_CHAT_ID:
        return
    data = json.dumps({
        "chat_id": POND_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # Don't let notification failure break operations


async def send(text: str):
    await asyncio.to_thread(_send_sync, text)


async def notify_project_created(project: dict):
    lang_map = {"en": "English", "th": "Thai", "tl": "Tagalog", "id": "Indonesian"}
    lang = lang_map.get(project.get("language", "en"), project.get("language", ""))
    await send(
        f"📁 <b>New Project Created</b>\n\n"
        f"<b>{project['name']}</b>\n"
        f"Language: {lang}\n"
        f"Credential: {project.get('credential_id') or 'None'}"
    )


async def notify_content_created(content: dict, project_name: str):
    headline = ""
    comments = []
    for b in content.get("blocks", []):
        if b["sort_order"] == 0:
            headline = b["text"]
        else:
            comments.append(f"  {b['sort_order']}. {b['text'][:80]}{'...' if len(b['text']) > 80 else ''}")

    comments_text = "\n".join(comments) if comments else "  (no comments)"
    await send(
        f"📝 <b>New Content Created</b>\n"
        f"Project: {project_name}\n\n"
        f"<b>{content['title']}</b>\n"
        f"Source: {content.get('source', 'manual')}\n\n"
        f"🖼 <b>Headline:</b>\n{headline}\n\n"
        f"💬 <b>Comments:</b>\n{comments_text}"
    )


async def notify_content_scheduled(content: dict, project_name: str):
    scheduled_at = content.get("scheduled_at", "unknown")
    await send(
        f"⏰ <b>Content Scheduled</b>\n"
        f"Project: {project_name}\n"
        f"Content: {content['title']}\n"
        f"Scheduled for: {scheduled_at}"
    )


async def notify_execution_started(content_title: str, project_name: str):
    await send(
        f"🚀 <b>Executing Content</b>\n"
        f"Project: {project_name}\n"
        f"Content: {content_title}\n\n"
        f"Rendering headline image and posting to Facebook..."
    )


async def notify_execution_success(content: dict, project_name: str):
    posted_blocks = sum(1 for b in content.get("blocks", []) if b.get("status") == "posted")
    total_blocks = len(content.get("blocks", []))
    platforms = []
    if content.get("fb_post_id"):
        platforms.append(f"FB: {content['fb_post_id']}")
    if content.get("ig_post_id"):
        platforms.append(f"IG: {content['ig_post_id']}")
    await send(
        f"✅ <b>Content Posted Successfully</b>\n"
        f"Project: {project_name}\n"
        f"Content: {content['title']}\n"
        f"Blocks: {posted_blocks}/{total_blocks} posted\n"
        f"{chr(10).join(platforms)}"
    )


async def notify_execution_failed(content: dict, project_name: str, error: str):
    failed_blocks = [b for b in content.get("blocks", []) if b.get("status") == "failed"]
    failed_info = ""
    for b in failed_blocks:
        prefix = "Headline" if b["sort_order"] == 0 else f"Comment {b['sort_order']}"
        failed_info += f"\n  {prefix}: {b.get('error_message', 'Unknown error')[:100]}"

    await send(
        f"❌ <b>Execution Failed</b>\n"
        f"Project: {project_name}\n"
        f"Content: {content['title']}\n\n"
        f"<b>Error:</b> {error[:200]}\n"
        f"<b>Failed blocks:</b>{failed_info or ' None'}"
    )
