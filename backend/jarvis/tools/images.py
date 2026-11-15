"""Local image generation. Talks to a server you run yourself:
  automatic1111  - Stable Diffusion WebUI / Forge / SD.Next started with --api  (POST /sdapi/v1/txt2img)
  openai_compat  - anything exposing POST /v1/images/generations (LocalAI, some ComfyUI bridges, OpenAI)
Images are written to JARVIS's own data directory, never into your folders, so this is a read-level tool."""
from __future__ import annotations

import base64
import re
import time

import httpx

from ..config import get_secret
from .registry import Tool, ToolContext, ToolRegistry

MAX_SIDE = 1536


def _clamp(v: int) -> int:
    return max(64, min(MAX_SIDE, int(v) // 8 * 8))


def generate_image(ctx: ToolContext, prompt: str, width: int = 768, height: int = 768, negative_prompt: str = "", steps: int = 24) -> dict:
    s = ctx.app.settings
    if s.image_provider == "none" or not s.image_base_url:
        raise RuntimeError("Image generation is not configured. Set JARVIS_IMAGE_PROVIDER (automatic1111 or openai_compat) and JARVIS_IMAGE_BASE_URL to your local image server, then restart.")
    width, height, base = _clamp(width), _clamp(height), s.image_base_url.rstrip("/")
    with httpx.Client(timeout=280, transport=ctx.app.http_transport) as c:
        if s.image_provider == "automatic1111":
            r = c.post(f"{base}/sdapi/v1/txt2img", json={"prompt": prompt, "negative_prompt": negative_prompt, "width": width, "height": height, "steps": max(1, min(int(steps), 80))})
            r.raise_for_status()
            b64 = r.json()["images"][0]
        elif s.image_provider == "openai_compat":
            key = get_secret("JARVIS_IMAGE_API_KEY") or get_secret("OPENAI_API_KEY")
            body = {"prompt": prompt, "size": f"{width}x{height}", "n": 1, "response_format": "b64_json", **({"model": s.image_model} if s.image_model else {})}
            r = c.post(f"{base}/images/generations", json=body, headers={"Authorization": f"Bearer {key}"} if key else {})
            r.raise_for_status()
            b64 = r.json()["data"][0]["b64_json"]
        else:
            raise RuntimeError(f"Unknown JARVIS_IMAGE_PROVIDER '{s.image_provider}'")
    data = base64.b64decode(b64.split(",", 1)[-1])
    if not data.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF")):
        raise RuntimeError("The image server did not return a PNG, JPEG or WebP image")
    ext = "png" if data.startswith(b"\x89PNG") else "jpg" if data.startswith(b"\xff\xd8") else "webp"
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "image"
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.{ext}"
    (s.images_dir / name).write_bytes(data)
    return {"prompt": prompt, "file": str(s.images_dir / name), "url": f"/api/images/{name}", "width": width, "height": height, "provider": s.image_provider}


def register(reg: ToolRegistry) -> None:
    I = {"type": "integer"}
    reg.register(Tool("generate_image", "Generate an image from a text prompt with the user's local image model. Show the result as Markdown: ![prompt](url).",
                      {"prompt": {"type": "string"}, "width": I, "height": I, "negative_prompt": {"type": "string"}, "steps": I}, generate_image, required=["prompt"], timeout=300))
