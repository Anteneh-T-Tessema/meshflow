"""Multi-modal input types for image, audio, and document content.

These types convert local files, URLs, raw bytes, or PIL images into the
message-block formats expected by Anthropic and OpenAI provider APIs.
No external dependencies are required for basic usage; pypdf and Pillow
are optional deps for PDF text extraction and PIL.Image support.

Quick start::

    from meshflow.multimodal.inputs import ImageInput, DocumentInput

    # From a file path
    img = ImageInput("screenshot.png")

    # From raw bytes (e.g. from an HTTP download)
    img = ImageInput.from_bytes(response.content, "image/jpeg")

    # From a PIL Image (no disk write required)
    from PIL import Image
    img = ImageInput.from_pil(Image.open("photo.jpg"))

    # From a URL
    img = ImageInput.from_url("https://example.com/chart.png")

    # From a numpy array (saves as PNG bytes)
    img = ImageInput.from_numpy(array)
"""
from __future__ import annotations

import base64
import io
import mimetypes
import os
from dataclasses import dataclass, field
from typing import Any, Union


# ── ImageInput ────────────────────────────────────────────────────────────────

@dataclass
class ImageInput:
    """An image passed to a multi-modal LLM call.

    *source* can be a local file path or a public ``http(s)://`` URL.
    Use the class-method constructors for in-memory data.

    Parameters
    ----------
    source:    File path or URL.  Empty when built via ``from_bytes``/``from_pil``.
    mime_type: Auto-detected from *source* extension when not provided.
    _data:     Raw bytes backing (set by ``from_bytes``/``from_pil``); not for direct use.
    """

    source: str = ""
    mime_type: str = ""
    _data: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        if not self.mime_type and self.source:
            if self.source.startswith(("http://", "https://")):
                self.mime_type = "image/jpeg"
            else:
                guessed, _ = mimetypes.guess_type(self.source)
                self.mime_type = guessed or "image/jpeg"

    # ── Constructors ──────────────────────────────────────────────────────────

    @classmethod
    def from_bytes(cls, data: bytes, mime_type: str = "image/jpeg") -> "ImageInput":
        """Create an ImageInput from raw image bytes.

        Useful when the image comes from an HTTP response, a database BLOB,
        or any in-memory source — no temporary file needed.

        Example::

            import httpx
            resp = httpx.get("https://example.com/chart.png")
            img = ImageInput.from_bytes(resp.content, "image/png")
        """
        obj = cls(mime_type=mime_type, _data=data)
        return obj

    @classmethod
    def from_pil(cls, image: Any, mime_type: str = "image/png") -> "ImageInput":
        """Create an ImageInput from a PIL/Pillow Image object.

        Converts the image to bytes in-memory — no disk write required.

        Example::

            from PIL import Image
            img = ImageInput.from_pil(Image.open("photo.jpg").resize((512, 512)))
        """
        buf = io.BytesIO()
        fmt = "PNG" if "png" in mime_type else "JPEG"
        image.save(buf, format=fmt)
        return cls.from_bytes(buf.getvalue(), mime_type)

    @classmethod
    def from_url(cls, url: str, mime_type: str = "") -> "ImageInput":
        """Create an ImageInput from a public image URL.

        The URL is passed directly to the provider without downloading.
        Not all providers support URL sources — use ``from_bytes`` for
        guaranteed compatibility.

        Example::

            img = ImageInput.from_url("https://example.com/diagram.png")
        """
        return cls(source=url, mime_type=mime_type or "image/jpeg")

    @classmethod
    def from_numpy(cls, array: Any, mime_type: str = "image/png") -> "ImageInput":
        """Create an ImageInput from a NumPy array.

        Requires either Pillow or OpenCV (``cv2``) to encode the array.

        Example::

            import numpy as np
            img = ImageInput.from_numpy(np.zeros((256, 256, 3), dtype=np.uint8))
        """
        try:
            from PIL import Image as _Image  # type: ignore[import]
            pil_img = _Image.fromarray(array)
            return cls.from_pil(pil_img, mime_type)
        except ImportError:
            pass
        try:
            import cv2  # type: ignore[import]
            _, buf = cv2.imencode(".png", array)
            return cls.from_bytes(buf.tobytes(), "image/png")
        except ImportError:
            raise ImportError(
                "Either Pillow or opencv-python is required for ImageInput.from_numpy(). "
                "Install with: pip install Pillow"
            )

    @classmethod
    def from_screenshot(cls) -> "ImageInput":
        """Capture the current screen and return it as an ImageInput.

        Requires Pillow (``pip install Pillow``).  Useful for agents that
        need to observe the current state of a GUI or browser.

        Example::

            screenshot = ImageInput.from_screenshot()
            result = await agent.run_multimodal("What is visible on screen?", [screenshot])
        """
        try:
            from PIL import ImageGrab  # type: ignore[import]
        except ImportError:
            raise ImportError(
                "Pillow is required for ImageInput.from_screenshot(). "
                "Install with: pip install Pillow"
            )
        img = ImageGrab.grab()
        return cls.from_pil(img, "image/png")

    # ── Block builders ────────────────────────────────────────────────────────

    def _get_b64(self) -> tuple[str, str]:
        """Return (base64_data, mime_type) from in-memory bytes or file."""
        if self._data:
            return base64.b64encode(self._data).decode(), self.mime_type
        with open(self.source, "rb") as f:
            raw = f.read()
        return base64.b64encode(raw).decode(), self.mime_type

    def to_message_block(self) -> dict[str, Any]:
        """Return an Anthropic-compatible content block."""
        if self.source.startswith(("http://", "https://")) and not self._data:
            return {"type": "image", "source": {"type": "url", "url": self.source}}
        b64, mime = self._get_b64()
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": b64},
        }

    def to_openai_block(self) -> dict[str, Any]:
        """Return an OpenAI-compatible content block (for GPT-4o, GPT-4-turbo).

        OpenAI uses ``{"type": "image_url", "image_url": {"url": "..."}}``
        for both remote URLs and ``data:`` URIs.
        """
        if self.source.startswith(("http://", "https://")) and not self._data:
            return {"type": "image_url", "image_url": {"url": self.source}}
        b64, mime = self._get_b64()
        data_uri = f"data:{mime};base64,{b64}"
        return {"type": "image_url", "image_url": {"url": data_uri}}


# ── DocumentInput ─────────────────────────────────────────────────────────────

@dataclass
class DocumentInput:
    """A plain-text, Markdown, JSON, CSV, YAML, or PDF document.

    PDFs are text-extracted via pypdf if available; otherwise sent as
    base64-encoded bytes using the ``document`` block type.
    """

    source: str = ""
    title: str = ""
    _text: str = field(default="", repr=False)
    _data: bytes = field(default=b"", repr=False)
    _mime: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not self.title and self.source:
            self.title = os.path.basename(self.source)

    # ── Constructors ──────────────────────────────────────────────────────────

    @classmethod
    def from_str(cls, text: str, title: str = "document.txt") -> "DocumentInput":
        """Create a DocumentInput from a plain-text string.

        Example::

            doc = DocumentInput.from_str(response.text, title="api_response.json")
        """
        obj = cls(title=title, _text=text)
        return obj

    @classmethod
    def from_bytes(cls, data: bytes, mime_type: str = "application/pdf",
                   title: str = "document") -> "DocumentInput":
        """Create a DocumentInput from raw bytes (e.g. a downloaded PDF)."""
        return cls(title=title, _data=data, _mime=mime_type)

    # ── Block builders ────────────────────────────────────────────────────────

    def to_message_block(self) -> dict[str, Any]:
        """Return an Anthropic-compatible content block."""
        # In-memory text
        if self._text:
            return {
                "type": "document",
                "source": {"type": "text", "text": self._text},
                "title": self.title,
            }
        # In-memory bytes
        if self._data:
            data = base64.b64encode(self._data).decode()
            return {
                "type": "document",
                "source": {"type": "base64", "media_type": self._mime or "application/pdf", "data": data},
                "title": self.title,
            }
        # File path
        ext = os.path.splitext(self.source)[1].lower()
        if ext == ".pdf":
            try:
                import pypdf  # type: ignore[import]
                reader = pypdf.PdfReader(self.source)
                text = "\n".join(p.extract_text() or "" for p in reader.pages)
                return {
                    "type": "document",
                    "source": {"type": "text", "text": text},
                    "title": self.title,
                }
            except ImportError:
                with open(self.source, "rb") as f:
                    data = base64.b64encode(f.read()).decode()
                return {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": data},
                    "title": self.title,
                }
        with open(self.source, encoding="utf-8", errors="replace") as f:
            text = f.read()
        return {
            "type": "document",
            "source": {"type": "text", "text": text},
            "title": self.title,
        }

    def to_openai_block(self) -> dict[str, Any]:
        """Return an OpenAI-compatible content block.

        OpenAI doesn't have a native ``document`` type — documents are sent
        as text content blocks.  PDFs passed as bytes are base64-encoded and
        wrapped in a text block with a header.
        """
        if self._text:
            return {"type": "text", "text": f"[{self.title}]\n{self._text}"}
        if self._data:
            # OpenAI does not support binary document blobs; send as base64 note
            b64 = base64.b64encode(self._data).decode()
            return {"type": "text", "text": f"[{self.title} — base64]\n{b64[:2000]}…"}
        with open(self.source, encoding="utf-8", errors="replace") as f:
            text = f.read()
        return {"type": "text", "text": f"[{self.title}]\n{text}"}


# ── AudioInput ────────────────────────────────────────────────────────────────

@dataclass
class AudioInput:
    """An audio file (mp3, wav, ogg, flac, etc.).

    Currently used for providers that accept base64-encoded audio blobs.
    """

    source: str = ""
    mime_type: str = ""
    _data: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        if not self.mime_type and self.source:
            guessed, _ = mimetypes.guess_type(self.source)
            self.mime_type = guessed or "audio/mpeg"

    @classmethod
    def from_bytes(cls, data: bytes, mime_type: str = "audio/mpeg") -> "AudioInput":
        """Create an AudioInput from raw audio bytes."""
        return cls(mime_type=mime_type, _data=data)

    def to_message_block(self) -> dict[str, Any]:
        if self._data:
            data = base64.b64encode(self._data).decode()
        else:
            with open(self.source, "rb") as f:
                data = base64.b64encode(f.read()).decode()
        return {
            "type": "audio",
            "source": {"type": "base64", "media_type": self.mime_type, "data": data},
        }

    def to_openai_block(self) -> dict[str, Any]:
        """Return an OpenAI-compatible audio content block (Whisper-style)."""
        if self._data:
            data = base64.b64encode(self._data).decode()
        else:
            with open(self.source, "rb") as f:
                data = base64.b64encode(f.read()).decode()
        ext = (self.mime_type.split("/")[-1] or "mp3").replace("mpeg", "mp3")
        return {
            "type": "input_audio",
            "input_audio": {"data": data, "format": ext},
        }


# ── Union type ────────────────────────────────────────────────────────────────

MultiModalInput = Union[ImageInput, DocumentInput, AudioInput]


# ── Message builders ─────────────────────────────────────────────────────────

def build_multimodal_message(
    text: str,
    inputs: list[MultiModalInput],
    provider: str = "anthropic",
) -> list[dict[str, Any]]:
    """Build a multi-part ``content`` list for provider message APIs.

    Media blocks are prepended; the text prompt is appended as the final part.

    Parameters
    ----------
    text:     The text prompt.
    inputs:   List of :class:`ImageInput`, :class:`DocumentInput`, or
              :class:`AudioInput` objects.
    provider: ``"anthropic"`` (default) or ``"openai"``.  Controls the block
              schema — Anthropic uses ``{"type": "image", "source": ...}``
              while OpenAI uses ``{"type": "image_url", "image_url": ...}``.

    Example::

        # Anthropic (Claude)
        blocks = build_multimodal_message("What is in this image?", [img])

        # OpenAI (GPT-4o)
        blocks = build_multimodal_message("Describe this chart.", [img],
                                          provider="openai")
    """
    is_openai = provider.lower() in ("openai", "gpt", "azure")
    parts: list[dict[str, Any]] = []
    for inp in inputs:
        if is_openai and hasattr(inp, "to_openai_block"):
            parts.append(inp.to_openai_block())
        else:
            parts.append(inp.to_message_block())
    if text:
        parts.append({"type": "text", "text": text})
    return parts
