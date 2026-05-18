# Documentor v2 — Product Requirements Document

## Overview

Documentor is an AI-first content management tool for creating and publishing Facebook posts in Pond's signature style: a **headline image** followed by **numbered comment blocks** (text + optional images).

The primary operator is Tim (Claude Code) via API. The web UI exists for Pond to review, edit, and manually execute when needed. Tim can also execute posts, manage content, and operate the full lifecycle programmatically.

## Content Format

Every content piece follows this structure:

```
┌─────────────────────────┐
│  HEADLINE IMAGE         │  ← Block 0: text rendered as image, posted as FB photo
│  (text on colored bg)   │
└─────────────────────────┘
     │
     ├── 1. Comment text...    ← Block 1 (optional image attachment)
     ├── 2. Comment text...    ← Block 2 (optional image attachment)
     ├── 3. Comment text...    ← Block 3 (optional image attachment)
     └── N. Comment text...    ← Block N
```

- Comment numbers ("1", "2", "3"...) are **auto-prepended** on execution — Pond's brand style
- Each comment block can have **text only** or **text + image**
- The headline block is always rendered into an image using the image renderer

## Architecture

**AI-first design:** Every action available in the UI is also available via API. Tim (or any agent) can fully operate Documentor without the UI.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | FastAPI (Python 3.12) |
| Database | SQLite |
| Frontend | Single-page HTML/CSS/JS (no framework) |
| Image Gen | HTML/CSS → Playwright screenshot (headless Chromium) |
| Service | systemd (`documentor.service`) |
| Reverse Proxy | Nginx → localhost:8100 |

## Location & Access

- **Path:** `/opt/documentor/`
- **Port:** 8100
- **URL:** `https://documentor.incomeinclick.in.th`
- **Auth:** Simple username/password (set via `AUTH_USERNAME` / `AUTH_PASSWORD` env vars)

---

## Data Model

### projects

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | UUID |
| name | TEXT | Display name ("Income in Click", "Loom Updates") |
| slug | TEXT UNIQUE | URL-friendly identifier |
| credential_id | TEXT | FB credential to use for posting |
| image_settings | TEXT (JSON) | Default image renderer settings for this project |
| created_at | DATETIME | |

### credentials

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | User-defined slug ("income-in-click-th") |
| name | TEXT | Display name |
| page_id | TEXT | Facebook Page ID |
| access_token | TEXT | Long-lived page access token |
| created_at | DATETIME | |

### contents

A single content piece = one FB post (headline image + comments).

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | UUID |
| project_id | TEXT FK | Links to projects |
| title | TEXT | Internal title for reference (not posted) |
| status | TEXT | `draft` / `executing` / `posted` / `failed` |
| source | TEXT | Where this content came from ("manual", "news:AI", "cron:topic") |
| fb_post_id | TEXT | Facebook post ID after posting (for commenting) |
| posted_at | DATETIME | When posted to FB |
| created_at | DATETIME | |
| updated_at | DATETIME | |

### blocks

Ordered content blocks within a content piece.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | UUID |
| content_id | TEXT FK | Links to contents |
| sort_order | INTEGER | 0 = headline, 1+ = comments |
| text | TEXT | Block text content |
| image_path | TEXT | Path to attached image (optional for comments, auto-generated for headline) |
| image_url | TEXT | External image URL (alternative to image_path) |
| fb_comment_id | TEXT | Facebook comment ID after posting |
| status | TEXT | `pending` / `posted` / `failed` |
| error_message | TEXT | If posting failed |
| created_at | DATETIME | |

**Block rules:**
- `sort_order = 0` → headline block. Text is rendered into an image on execution.
- `sort_order >= 1` → comment blocks. Auto-prefixed with number on execution.
- A content piece must have exactly one headline block (sort_order 0).
- Comment blocks are posted in sort_order sequence.

---

## Features

### 1. Content Queue

The main view. Shows all `draft` content pieces for the selected project, newest first.

Each content card shows:
- Title (internal reference)
- Source label (where it came from)
- Headline text preview
- Comment blocks (numbered, editable inline)
- **Execute** button
- Edit / Delete buttons
- Created date

**Archive tab/filter:** Shows `posted` content. Hidden by default. Accessible via tab or query parameter.

### 2. Content Blocks (Editable)

Each content piece is a list of blocks:
- **Block 0 (Headline):** Large text area. This becomes the headline image.
- **Block 1-N (Comments):** Text areas with optional image attachment. Auto-numbered on display and execution.

**Editing:**
- Inline editing in the content card
- Reorder blocks (drag or up/down arrows)
- Add / remove blocks
- Attach image to any comment block (upload or URL)
- Edit headline image settings (font, colors, size, padding, alignment)

### 3. Headline Image Renderer

**Engine:** HTML/CSS rendered to PNG via Playwright (headless Chromium). No external API needed — runs locally on the server. Produces the same quality as htmlcsstoimage but at zero cost.

**How it works:**
1. Build an HTML page with the text + styling (Google Fonts loaded via CSS import)
2. Open in headless Chromium via Playwright
3. Set viewport to exact dimensions
4. Screenshot → PNG bytes
5. Return or save

**Settings (per project, overridable per content):**

| Setting | Default | Options |
|---------|---------|---------|
| Font | Sarabun | Any Google Font (loaded via CSS) |
| Font weight | 700 | 400 / 600 / 700 / 800 |
| Font size | 58px | Configurable (px) |
| Background color | #000000 | Any hex color or CSS gradient |
| Text color | #FFFFFF | Any hex color |
| Text shadow | `0 2px 4px rgba(0,0,0,0.3)` | Any CSS text-shadow value, or none |
| Width | 1200px | Configurable |
| Height | 900px | Configurable |
| Alignment | Center (vertical + horizontal) | — |
| Padding | 80px | Configurable |
| Line height | 1.5 | Configurable |

**HTML template** (built server-side, not user-exposed):
```html
<link href="https://fonts.googleapis.com/css2?family={font}:wght@{weight}&display=swap" rel="stylesheet">
<div class="container">
  <p class="text">{formatted_text}</p>
</div>
```
- Container: full viewport, flexbox center/center, background color, padding
- Text: font family, size, color, weight, line-height, text-shadow
- Newlines in text converted to `<br>` tags

**Three modes of use:**

1. **Preview** — render and return PNG bytes without saving. For live preview in UI or Tim checking output before committing.
2. **Download** — same as preview but served with `Content-Disposition: attachment` header for browser download.
3. **Execute** — render, save to `/opt/documentor/media/{content_id}.png`, then use for FB posting.

**UI: Quick Image tool**
- Standalone page in the UI for rendering headline images without creating content
- Text input (multiline)
- Settings panel (font, colors, dimensions, etc.)
- "Preview" button → shows rendered image inline
- "Download" button → downloads PNG
- Optional: "Create Content from This" → turns it into a draft content piece

### 4. Execution (Post to Facebook)

When Execute is triggered (by Pond via UI or by Tim via API):

```
1. Render headline block text → image (PNG)
2. Upload image to FB page as photo post → get fb_post_id
3. For each comment block (sort_order 1, 2, 3...):
   a. Prepend number: "1 " + text, "2 " + text, etc.
   b. If block has image: post comment with image attachment
   c. If text only: post text comment
   d. Wait 3 seconds between comments
   e. Store fb_comment_id
4. Update content status → "posted"
5. Record posted_at timestamp
```

**Error handling:**
- If headline post fails → mark content as `failed`, stop
- If a comment fails → mark that block as `failed`, continue with next
- Failed content can be retried

### 5. Projects

Multi-project support. Each project:
- Has its own content queue
- Connects to one FB credential
- Has default image renderer settings
- Content is filtered by project in the UI

### 6. Credential Management

Simple FB page token management:
- Add credential: name, page ID, access token
- Test credential: `GET /{page_id}?fields=name` to verify
- One credential per project

---

## API Endpoints

### Contents

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/contents` | List contents (query: `project_id`, `status`, `limit`, `offset`) |
| POST | `/api/contents` | Create content with blocks |
| GET | `/api/contents/{id}` | Get content with all blocks |
| PUT | `/api/contents/{id}` | Update content metadata |
| DELETE | `/api/contents/{id}` | Delete content and its blocks |
| POST | `/api/contents/{id}/execute` | Execute: render headline + post to FB + comment blocks |

**POST /api/contents body:**
```json
{
  "project_id": "uuid",
  "title": "Internal title",
  "source": "manual",
  "blocks": [
    { "sort_order": 0, "text": "Headline text here" },
    { "sort_order": 1, "text": "First comment text" },
    { "sort_order": 2, "text": "Second comment text", "image_url": "https://..." }
  ]
}
```

### Blocks

| Method | Path | Description |
|--------|------|-------------|
| PUT | `/api/contents/{id}/blocks` | Bulk update blocks (reorder, edit, add, remove) |
| POST | `/api/contents/{id}/blocks` | Add a single block |
| PUT | `/api/blocks/{block_id}` | Update single block |
| DELETE | `/api/blocks/{block_id}` | Delete single block |
| POST | `/api/blocks/{block_id}/upload-image` | Upload image for a block |

### Image Renderer

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/image/preview` | Render text + settings → PNG bytes (no save, for preview) |
| POST | `/api/image/download` | Same as preview but with `Content-Disposition: attachment` |
| POST | `/api/image/render/{content_id}` | Render headline block for a content piece → save to media/ |
| GET | `/api/image/{content_id}` | Get saved headline image |

**POST /api/image/preview body:**
```json
{
  "text": "เอไอกำลังเปลี่ยนโลก\nการทำงานของเรา",
  "settings": {
    "font": "Sarabun",
    "font_weight": 700,
    "font_size": 58,
    "bg_color": "#000000",
    "text_color": "#FFFFFF",
    "text_shadow": "0 2px 4px rgba(0,0,0,0.3)",
    "width": 1200,
    "height": 900,
    "padding": 80,
    "line_height": 1.5
  }
}
```

### Projects

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/projects` | List all projects |
| POST | `/api/projects` | Create project |
| PUT | `/api/projects/{id}` | Update project |
| DELETE | `/api/projects/{id}` | Delete project |

### Credentials

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/credentials` | List all |
| POST | `/api/credentials` | Add new |
| POST | `/api/credentials/{id}/test` | Verify token |
| DELETE | `/api/credentials/{id}` | Remove |

---

## File Structure

```
/opt/documentor/
├── backend/
│   ├── main.py              # FastAPI app + static file serving
│   ├── config.py            # Settings
│   ├── database.py          # SQLite + async engine
│   ├── auth.py              # Simple auth
│   ├── models.py            # SQLAlchemy models (all in one file — it's small)
│   ├── routers/
│   │   ├── contents.py      # Content CRUD + execute
│   │   ├── blocks.py        # Block CRUD + image upload
│   │   ├── image.py         # Image renderer endpoints
│   │   ├── projects.py      # Project CRUD
│   │   └── credentials.py   # Credential CRUD
│   └── services/
│       ├── image_renderer.py # HTML/CSS → Playwright screenshot
│       └── fb_poster.py     # Facebook Graph API posting
├── frontend/
│   ├── index.html           # Main app (SPA)
│   └── login.html           # Login page
├── media/                   # Generated images + uploaded images
├── .env                     # Config
├── requirements.txt
└── PRD.md
```

---

## UI Layout

Simple, clean, mobile-friendly.

```
┌──────────────────────────────────────┐
│  Documentor    [Project ▼]  [Archive]│
├──────────────────────────────────────┤
│                                      │
│  ┌── Content Card ────────────────┐  │
│  │ "AI news roundup"    [Source]  │  │
│  │                                │  │
│  │ ┌─ Headline ────────────────┐  │  │
│  │ │ เอไอกำลังเปลี่ยนโลก      │  │  │
│  │ │ การทำงานของเรา             │  │  │
│  │ └──────────────────────────┘  │  │
│  │                                │  │
│  │ 1. First comment block...     │  │
│  │ 2. Second comment block...    │  │
│  │ 3. Third comment block...     │  │
│  │    [+ Add Block]              │  │
│  │                                │  │
│  │ [Execute ▶]  [Edit]  [Delete] │  │
│  └────────────────────────────────┘  │
│                                      │
│  ┌── Content Card ────────────────┐  │
│  │ ...                            │  │
│  └────────────────────────────────┘  │
│                                      │
└──────────────────────────────────────┘
```

---

## How Tim Uses Documentor

Tim interacts with Documentor purely via API:

```python
# Tim creates content
import urllib.request, json

def create_content(project_id, title, headline, comments, source="tim"):
    blocks = [{"sort_order": 0, "text": headline}]
    for i, comment in enumerate(comments, 1):
        block = {"sort_order": i, "text": comment["text"]}
        if "image_url" in comment:
            block["image_url"] = comment["image_url"]
        blocks.append(block)

    data = json.dumps({
        "project_id": project_id,
        "title": title,
        "source": source,
        "blocks": blocks,
    }).encode()

    req = urllib.request.Request(
        "http://127.0.0.1:8100/api/contents",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {AUTH_TOKEN}"},
    )
    return json.loads(urllib.request.urlopen(req).read())

# Tim executes content
def execute_content(content_id):
    req = urllib.request.Request(
        f"http://127.0.0.1:8100/api/contents/{content_id}/execute",
        method="POST",
        headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
    )
    return json.loads(urllib.request.urlopen(req).read())
```

---

## Scope

### MVP (This Build)
- Content CRUD with blocks (via API + UI)
- Headline image renderer (HTML/CSS → Playwright, replaces Pillow)
- Image preview + download (standalone, no content needed)
- Quick Image page in UI (type text → preview → download)
- Facebook posting (photo + numbered comments with optional images)
- Multi-project with per-project FB credentials + per-project image settings
- Content queue UI with archive
- Full API for Tim to operate programmatically

### Future
- Content templates / skills for Tim
- Post scheduling
- Other platforms (X, Instagram)
- Analytics

---

## Non-Goals

- No session scanning (removed — Tim writes content directly)
- No LLM configuration UI (Tim IS the AI — no external LLM needed)
- No keyword feed engine (Tim handles search + content creation via cron)
- No complex skill management UI (Tim receives skills via conversation)
