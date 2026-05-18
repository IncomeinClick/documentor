"""
Blog queue writer — writes to /root/th-blog-queue.json when TH content is posted.
A scheduled Claude Code trigger picks up queue items and writes blog posts.
"""

import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone

QUEUE_FILE = Path("/root/th-blog-queue.json")
TH_PROJECT_ID = "c4755235-ff9e-4a2c-9cb9-290cfbdf56f7"


def _read_queue() -> list[dict]:
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _write_queue(items: list[dict]):
    QUEUE_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False))


async def enqueue_if_th(content: dict, project_id: str, project_name: str):
    """Add content to TH blog queue if it's from the Thai project."""
    if project_id != TH_PROJECT_ID:
        return

    queue = await asyncio.to_thread(_read_queue)

    # Skip if already in queue
    if any(item["content_id"] == content["id"] for item in queue):
        return

    # Extract block texts for blog writing
    blocks = sorted(content.get("blocks", []), key=lambda b: b.get("sort_order", 0))
    block_texts = [b["text"] for b in blocks]

    entry = {
        "content_id": content["id"],
        "title": content.get("title", ""),
        "posted_at": content.get("posted_at", datetime.now(timezone.utc).isoformat()),
        "block_texts": block_texts,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }

    queue.append(entry)
    await asyncio.to_thread(_write_queue, queue)
