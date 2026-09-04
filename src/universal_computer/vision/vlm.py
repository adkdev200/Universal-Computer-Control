"""Vision-language model provider (optional, provider-agnostic).

Ships an OpenAI-compatible chat-completions implementation (works with
OpenAI, Azure OpenAI gateways, vLLM, LM Studio, Ollama's OpenAI endpoint, ...)
that sends a screenshot plus a description and asks for a bounding box. The
API key is read from an environment variable *named* in the config, never
from the config file itself.
"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

from PIL.Image import Image

from universal_computer.config import VLMConfig
from universal_computer.logging import get_logger
from universal_computer.models.element import BoundingBox
from universal_computer.vision.base import VisionResult

logger = get_logger("vision.vlm")

_PROMPT = (
    "You are a precise UI-element locator. Find the UI element in this screenshot "
    "that best matches the description. Respond with ONLY a JSON object, no "
    "markdown, of the form: {\"found\": true|false, \"bbox_2d\": [x1, y1, x2, y2], "
    "\"confidence\": 0.0-1.0} where bbox_2d is in screenshot pixel coordinates "
    "with origin at the top-left corner.\nDescription: {description}"
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class OpenAICompatibleVLM:
    """Vision-language model over an OpenAI-compatible HTTP API."""

    name = "openai-compatible"

    def __init__(self, config: VLMConfig) -> None:
        self.config = config

    def is_available(self) -> bool:
        if not self.config.enabled:
            self._probe_error = "disabled in config"
            return False
        import importlib.util  # noqa: PLC0415

        if importlib.util.find_spec("httpx") is None:
            self._probe_error = "httpx not installed (pip install 'universal-computer-control[vlm]')"
            return False
        import os  # noqa: PLC0415

        if not os.environ.get(self.config.api_key_env):
            self._probe_error = (
                f"environment variable {self.config.api_key_env} (API key) is not set"
            )
            return False
        return True

    async def locate_element(self, screenshot: bytes | Image | Path, description: str) -> VisionResult:
        try:
            import httpx  # noqa: PLC0415
        except ImportError:
            return VisionResult(found=False, method="vlm", details="httpx not installed")
        data_url = self._to_data_url(screenshot)
        if data_url is None:
            return VisionResult(found=False, method="vlm", details="unsupported screenshot input")
        api_key = self._api_key()
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT.format(description=description)},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "max_tokens": 300,
            "temperature": 0.0,
        }
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:  # noqa: BLE001 - network/model errors are not fatal
            logger.warning("VLM request failed: %s", exc)
            return VisionResult(found=False, method="vlm", details=f"request failed: {exc}")
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return VisionResult(found=False, method="vlm", details="unexpected API response shape")
        return self._parse_content(content, description)

    def _api_key(self) -> str:
        import os  # noqa: PLC0415

        return os.environ.get(self.config.api_key_env, "")

    def _to_data_url(self, screenshot: bytes | Image | Path) -> str | None:
        try:
            if isinstance(screenshot, bytes):
                raw = screenshot
            elif isinstance(screenshot, Path):
                raw = screenshot.read_bytes()
            elif isinstance(screenshot, Image):
                buffer = io.BytesIO()
                screenshot.save(buffer, format="PNG")
                raw = buffer.getvalue()
            else:
                return None
        except (OSError, ValueError) as exc:
            logger.warning("Could not encode screenshot: %s", exc)
            return None
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")

    def _parse_content(self, content: str, description: str) -> VisionResult:
        match = _JSON_RE.search(content or "")
        if not match:
            return VisionResult(
                found=False, method="vlm", details=f"no JSON in model reply: {content[:120]}"
            )
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return VisionResult(found=False, method="vlm", details="malformed JSON in model reply")
        found = bool(payload.get("found"))
        bbox_raw = payload.get("bbox_2d")
        confidence = payload.get("confidence")
        if not found or not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) != 4:
            return VisionResult(
                found=False, method="vlm", details=f"model did not find {description!r}"
            )
        try:
            left, top, right, bottom = (int(round(float(v))) for v in bbox_raw)
        except (TypeError, ValueError):
            return VisionResult(found=False, method="vlm", details="invalid bbox values")
        try:
            conf = float(confidence) if confidence is not None else 0.5
        except (TypeError, ValueError):
            conf = 0.5
        return VisionResult(
            found=True,
            bbox=BoundingBox.from_ltrb(
                min(left, right), min(top, bottom), max(left, right), max(top, bottom)
            ),
            confidence=max(0.0, min(1.0, conf)),
            method="vlm",
            details=f"located {description!r} via {self.config.model}",
        )
