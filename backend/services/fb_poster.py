import urllib.request
import urllib.parse
import json
import asyncio
from pathlib import Path

FB_GRAPH = "https://graph.facebook.com/v22.0"


def _fb_request(url: str, data: dict | None = None, files: dict | None = None, method: str = "GET") -> dict:
    """Synchronous FB Graph API request."""
    if method == "GET" or (method == "POST" and not files):
        if data:
            encoded = urllib.parse.urlencode(data).encode()
            req = urllib.request.Request(url, data=encoded, method="POST")
        else:
            req = urllib.request.Request(url, method=method)
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read())

    # Multipart for file uploads
    import mimetypes
    boundary = "----DocumentorBoundary"
    body = b""
    for key, val in (data or {}).items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}\r\n".encode()
    for key, filepath in (files or {}).items():
        mime = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
        filename = Path(filepath).name
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"; filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode()
        with open(filepath, "rb") as f:
            body += f.read()
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    resp = urllib.request.urlopen(req, timeout=60)
    return json.loads(resp.read())


async def post_photo(page_id: str, access_token: str, image_path: str, caption: str = "", published: bool = True) -> dict:
    """Post a photo to a FB page.

    published=True (default): single-step upload via /page/photos — photo and feed post
    appear immediately. Returns the photo response (which includes post_id).

    published=False: two-step draft flow so the post shows in Meta Business Suite > Drafts:
      1) POST /page/photos with published=false (no message) → returns unpublished photo_id
      2) POST /page/feed with attached_media[0]={media_fbid:photo_id} + published=false +
         message → returns the actual draft post id

    Posting a single-step photo with published=false (Documentor's old behaviour) only
    creates an "unpublished photo" in the page's Unpublished album — invisible to
    Business Suite. The 2-step flow is what the FB UI itself uses for photo drafts.
    """
    photos_url = f"{FB_GRAPH}/{page_id}/photos"
    if published:
        data = {"access_token": access_token}
        if caption:
            data["message"] = caption
        return await asyncio.to_thread(
            _fb_request, photos_url, data=data, files={"source": image_path}, method="POST"
        )

    # Step 1: upload as unpublished photo
    photo_data = {"access_token": access_token, "published": "false"}
    photo_resp = await asyncio.to_thread(
        _fb_request, photos_url, data=photo_data, files={"source": image_path}, method="POST"
    )
    photo_id = photo_resp.get("id")
    if not photo_id:
        raise RuntimeError(f"FB photo upload returned no id: {photo_resp}")

    # Step 2: create the feed post attached to that photo, marked as DRAFT so it lands in
    # Meta Business Suite > Content > Drafts (without unpublished_content_type=DRAFT it's
    # tagged INLINE_CREATED and stays hidden from Business Suite UI).
    feed_url = f"{FB_GRAPH}/{page_id}/feed"
    feed_data = {
        "access_token": access_token,
        "published": "false",
        "unpublished_content_type": "DRAFT",
        "attached_media[0]": json.dumps({"media_fbid": photo_id}),
    }
    if caption:
        feed_data["message"] = caption
    feed_resp = await asyncio.to_thread(_fb_request, feed_url, data=feed_data, method="POST")
    return feed_resp


async def post_comment(post_id: str, access_token: str, message: str, image_path: str | None = None) -> dict:
    """Post a comment on a FB post. Optionally with image."""
    url = f"{FB_GRAPH}/{post_id}/comments"
    data = {"access_token": access_token, "message": message}
    files = {}
    if image_path:
        files["source"] = image_path
    result = await asyncio.to_thread(
        _fb_request, url, data=data, files=files if files else None, method="POST"
    )
    return result


async def verify_page(page_id: str, access_token: str) -> dict:
    """Verify a page token works. Returns page info."""
    url = f"{FB_GRAPH}/{page_id}?fields=name,id&access_token={access_token}"
    return await asyncio.to_thread(_fb_request, url)


async def get_ig_account(page_id: str, access_token: str) -> str | None:
    """Get Instagram business account ID linked to a FB page."""
    url = f"{FB_GRAPH}/{page_id}?fields=instagram_business_account&access_token={access_token}"
    resp = await asyncio.to_thread(_fb_request, url)
    ig = resp.get("instagram_business_account")
    return ig["id"] if ig else None


async def post_ig_photo(ig_account_id: str, access_token: str, image_url: str, caption: str = "") -> dict:
    """Post a photo to Instagram. 2-step: create container → publish.
    image_url must be a publicly accessible HTTPS URL.
    """
    # Step 1: Create media container
    url = f"{FB_GRAPH}/{ig_account_id}/media"
    data = {"access_token": access_token, "image_url": image_url}
    if caption:
        data["caption"] = caption
    container = await asyncio.to_thread(_fb_request, url, data=data, method="POST")
    container_id = container["id"]

    # Step 2: Wait for container to be ready, then publish
    # IG needs a moment to process the image
    for _ in range(10):
        await asyncio.sleep(3)
        status_url = f"{FB_GRAPH}/{container_id}?fields=status_code&access_token={access_token}"
        status = await asyncio.to_thread(_fb_request, status_url)
        if status.get("status_code") == "FINISHED":
            break

    # Step 3: Publish
    pub_url = f"{FB_GRAPH}/{ig_account_id}/media_publish"
    pub_data = {"access_token": access_token, "creation_id": container_id}
    result = await asyncio.to_thread(_fb_request, pub_url, data=pub_data, method="POST")
    return result


async def post_ig_comment(media_id: str, access_token: str, message: str) -> dict:
    """Post a comment on an IG media."""
    url = f"{FB_GRAPH}/{media_id}/comments"
    data = {"access_token": access_token, "message": message}
    return await asyncio.to_thread(_fb_request, url, data=data, method="POST")
