import asyncio
from playwright.async_api import async_playwright
from backend.config import DEFAULT_IMAGE_SETTINGS

_browser = None
_lock = asyncio.Lock()


async def _get_browser():
    global _browser
    if _browser is None or not _browser.is_connected():
        async with _lock:
            if _browser is None or not _browser.is_connected():
                pw = await async_playwright().start()
                _browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
                )
    return _browser


def _build_html(text: str, settings: dict) -> str:
    s = {**DEFAULT_IMAGE_SETTINGS, **settings}
    formatted = text.replace("\\n", "<br>").replace("\n", "<br>")
    font_family = s["font"]
    font_weight = s["font_weight"]
    font_url = f"https://fonts.googleapis.com/css2?family={font_family.replace(' ', '+')}:wght@{font_weight}&display=swap"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <link href="{font_url}" rel="stylesheet">
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ width: {s['width']}px; height: {s['height']}px; overflow: hidden; }}
    .container {{
      width: {s['width']}px;
      height: {s['height']}px;
      background: {s['bg_color']};
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: {s['padding']}px;
    }}
    .text {{
      color: {s['text_color']};
      font-size: {s['font_size']}px;
      font-family: '{font_family}', sans-serif;
      font-weight: {font_weight};
      line-height: {s['line_height']};
      text-shadow: {s.get('text_shadow', 'none')};
      word-wrap: break-word;
      overflow-wrap: break-word;
    }}
  </style>
</head>
<body>
  <div class="container">
    <p class="text">{formatted}</p>
  </div>
</body>
</html>"""


async def render_image(text: str, settings: dict | None = None) -> bytes:
    """Render text to PNG bytes using headless Chromium."""
    s = {**DEFAULT_IMAGE_SETTINGS, **(settings or {})}
    html = _build_html(text, s)
    browser = await _get_browser()
    page = await browser.new_page(viewport={"width": s["width"], "height": s["height"]})
    try:
        await page.set_content(html, wait_until="networkidle")
        # Wait a bit for fonts to load
        await page.wait_for_timeout(500)
        png_bytes = await page.screenshot(type="png")
        return png_bytes
    finally:
        await page.close()


async def render_and_save(text: str, save_path: str, settings: dict | None = None) -> str:
    """Render and save to file. Returns the file path."""
    png_bytes = await render_image(text, settings)
    with open(save_path, "wb") as f:
        f.write(png_bytes)
    return save_path
