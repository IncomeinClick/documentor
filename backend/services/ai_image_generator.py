"""AI infographic image generation — OpenAI gpt-image-2 (text-to-image, no reference).

Three steps: caption → hook (GPT-4o) → image prompt (GPT-4o) → image (gpt-image-2).
1:1 square so the post displays in full on FB feed, IG grid, and LinkedIn alike.

The prompt-writer extracts entities from the caption (brands, public figures, products)
and bakes them into the visual — Claude → Anthropic logo, Sam Altman → stylized
portrait, etc. This keeps every generation visually distinct instead of all looking
like the same dark template.

Pond's own face is still excluded (FB profile pic shows him already). Public figures
and brand logos ARE encouraged when the caption is about them.

Layout + theme variants rotate so a portfolio of posts doesn't all look identical:
- list_cards / stat_hero / entity_hero (3 layouts)
- dark / light (2 themes — dark is IncomeInClick brand default)
"""
import base64
import json
import random
import asyncio
from openai import OpenAI

TEXT_MODEL = "gpt-5.5"
IMAGE_MODEL = "gpt-image-2"
IMAGE_SIZE = "1024x1024"

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


HOOK_SYSTEM_PROMPT = """You generate scroll-stopping headlines (hooks) for Facebook infographic posts.

Given a caption, produce ONE hook. Constraints:
- 1-3 lines, 4-12 words per line — short, punchy, all caps acceptable for emphasis
- Match the language of the caption (Thai if Thai, English if English)
- Stop the scroll: provocative claim, curiosity gap, contrarian take, or strong promise
- Avoid clickbait clichés ("you won't believe", "this one trick")
- No emojis in the hook
- Use \\n to separate lines if multiple lines

Return JSON: {"hook": "..."}"""


LAYOUT_VARIANTS = ["list_cards", "stat_hero", "entity_hero"]
THEME_VARIANTS = ["light", "dark"]


PROMPT_SYSTEM_PROMPT = """You write image-generation prompts for IncomeInClick's social infographics.

The brand serves a Thai SaaS-solopreneur audience — content is about building SaaS with AI / Claude Code. You'll receive a caption + hook + chosen layout + chosen theme. Your job: produce ONE detailed gpt-image-2 prompt.

## Step 1: Extract entities from the caption
Identify and incorporate as visual elements:
- BRAND/PRODUCT logos: Claude (Anthropic geometric mark), OpenAI, ChatGPT, Stripe, n8n, Vercel, Cursor, Replit, Supabase, etc. — render the actual recognizable logo
- PUBLIC FIGURES: Sam Altman, Elon Musk, Dario Amodei, Pieter Levels, etc. — render a stylized portrait (not photo-real, illustrative)
- SPECIFIC FEATURES/CONCEPTS: e.g., "Dreaming feature" → an evocative icon (closed eye + thought bubble), "MCP" → a connector/protocol motif

Mention these entities EXPLICITLY in your output prompt so the image model renders them.

## Step 2: Use the layout variant you receive
- "list_cards": headline at top + 3-5 numbered content cards below, each = small icon + short label + 1-2 lines. Best for "how X works" / "5 things you need to know".
- "stat_hero": one giant number/statistic dominates the visual (top center), 2-3 supporting context lines beneath. Best for revenue / metric / "47 days to $10K MRR" posts.
- "entity_hero": a recognizable entity (brand logo, stylized portrait, product mockup) is the visual hero (large, centered or left), with headline + 2-3 caption lines on the other side. Best for "Claude announced X" / "[Person] said Y" / "[Product] launched Z" posts.

## Step 3: Use the theme you receive
- "dark" (DEFAULT for IncomeInClick): background = deep near-black (#0a0a0a), text = white, accent = green (#00e676) with subtle cyan (#22d3ee) glow at corners. This is the brand default.
- "light": background = warm white/cream (#fafafa to #f5f5f0), text = near-black, ONE accent color = IncomeInClick green (#00e676). Use only for special light variants when explicitly requested.

## Step 4: Red is ONLY for failure markers — NEVER as primary accent

**Brand rule (HARD):** Green #00e676 is ALWAYS the primary accent for IncomeInClick. Regardless of whether the post is positive, neutral, or negative (shutdown, failure, layoff, post-mortem, lawsuit, scam warning, etc.), green stays primary. Do NOT swap the whole accent to red just because the topic is negative.

**Red #ef4444 is allowed ONLY for these specific element types:**
- ❌ X / cross-out markers
- ⚠️ Warning triangle icons
- "BROKEN" / "FAILED" / "DOWN" / "BANNED" small status badges (small filled red dot + tiny red text)
- A failed/canceled checkmark
- A graph line going down (specifically the descending portion)
- A diagonal strikethrough across a canceled item
- A tiny red flag glyph

Constraints when red is used:
- Red appears on small specific elements only — never as the primary headline color, hero stat color, or background glow
- Green stays the dominant accent in the image — viewers should still see "this is an IncomeInClick post" at a glance
- Red total coverage in the image should be tiny (~5% or less of accent surface)
- The big hero stat, headline emphasis words, and brand glow always stay green even when the topic is failure

## Brand style (always)
- 1:1 square 1024x1024
- Bold sans-serif typography (Kanit / Sarabun aesthetic for Thai with crisp text rendering)
- PRIMARY accent ALWAYS green #00e676 — never replaced by another color regardless of topic tone. Red #ef4444 is allowed ONLY for tiny failure-marker elements per Step 4, never as primary.
- High contrast headline; most important keyword in accent color
- Modern, premium, scroll-stopping
- Do NOT include any IncomeInClick wordmark, "incomeinclick.com" URL, or website footer — leave the corners clean

## Face / logo rules
- DO render brand logos when caption mentions specific products (Claude, OpenAI, Stripe, etc.) — use the actual recognizable mark
- DO render stylized portraits of public figures when caption is about them (Sam Altman, Elon, Dario Amodei, etc.) — illustrative, not photographic
- DO NOT render Pond's own face — the FB profile picture already shows him; including in every image is repetitive
- Generic human silhouettes / "developer at desk" stock-style is OK if needed for context but prefer actual entities when available

## Output format
Return JSON: {"prompt": "..."}

The prompt should be a single paragraph that EXPLICITLY:
1. States 1:1 square 1024x1024
2. States the chosen theme + exact background colors
3. Names the chosen layout variant by structure (e.g., "headline at top with 4 numbered cards below")
4. Quotes the headline text EXACTLY
5. Describes each content element with its text and visual hint
6. Names every entity to render (e.g., "the Anthropic Claude logo top-right, rendered in white on dark", "a stylized illustrative portrait of Dario Amodei centered-left")
7. Specifies typography (Kanit/Sarabun for Thai, bold sans-serif, crisp text rendering)
8. Confirms ONE accent color (state which)"""


async def generate_hook(caption: str, lang: str = "th") -> str:
    client = _get_client()
    # GPT-5.x doesn't accept custom temperature — model picks its own default
    resp = await asyncio.to_thread(
        lambda: client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": HOOK_SYSTEM_PROMPT},
                {"role": "user", "content": f"Caption (lang={lang}):\n{caption}"},
            ],
            response_format={"type": "json_object"},
        )
    )
    data = json.loads(resp.choices[0].message.content)
    return data["hook"].strip()


async def generate_image_prompt(
    caption: str,
    hook: str,
    lang: str = "th",
    layout: str | None = None,
    theme: str = "dark",
) -> str:
    if layout is None:
        layout = random.choice(LAYOUT_VARIANTS)
    if theme not in THEME_VARIANTS:
        theme = "dark"
    client = _get_client()
    user_msg = (
        f"Caption (lang={lang}):\n{caption}\n\n"
        f"Hook:\n{hook}\n\n"
        f"Layout variant: {layout}\n"
        f"Theme: {theme}"
    )
    resp = await asyncio.to_thread(
        lambda: client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": PROMPT_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
        )
    )
    data = json.loads(resp.choices[0].message.content)
    return data["prompt"].strip()


async def generate_image(prompt: str) -> bytes:
    """Returns PNG bytes."""
    client = _get_client()

    def _call():
        resp = client.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt,
            size=IMAGE_SIZE,
            quality="medium",
            n=1,
        )
        return base64.b64decode(resp.data[0].b64_json)

    return await asyncio.to_thread(_call)
