"""Multi-modal input types for image, audio, and document content.

These types convert local files or URLs into the message-block format
expected by Anthropic / OpenAI provider APIs.  No external dependencies
are required for basic usage; pypdf is an optional dep for PDF text extraction.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from dataclasses import dataclass, field
from typing import Any, Union


@dataclass
class ImageInput:
    """An image passed to a multi-modal LLM call.

    *source* can be a local file path or a public ``http(s)://`` URL.

    Parameters
    ----------
    source:    File path or URL.
    mime_type: Auto-detected from *source* extension when not provided.
    """

    source: str
    mime_type: str = ""

    def __post_init__(self) -> None:
        if not self.mime_type:
            if self.source.startswith(("http://", "https://")):
                self.mime_type = "image/jpeg"
            else:
                guessed, _ = mimetypes.guess_type(self.source)
                self.mime_type = guessed or "image/jpeg"

    def to_message_block(self) -> dict[str, Any]:
        if self.source.startswith(("http://", "https://")):
            return {"type": "image", "source": {"type": "url", "url": self.source}}
        with open(self.source, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": self.mime_type, "data": data},
        }


@dataclass
class DocumentInput:
    """A plain-text, Markdown, JSON, CSV, YAML, or PDF document.

    PDFs are text-extracted via pypdf if available; otherwise sent as
    base64-encoded bytes using the ``document`` block type.
    """

    source: str
    title: str = ""

    def __post_init__(self) -> None:
        if not self.title:
            self.title = os.path.basename(self.source)

    def to_message_block(self) -> dict[str, Any]:
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
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": data,
                    },
                    "title": self.title,
                }
        with open(self.source, encoding="utf-8", errors="replace") as f:
            text = f.read()
        return {
            "type": "document",
            "source": {"type": "text", "text": text},
            "title": self.title,
        }


@dataclass
class AudioInput:
    """An audio file (mp3, wav, ogg, flac, etc.).

    Currently used for providers that accept base64-encoded audio blobs.
    """

    source: str
    mime_type: str = ""

    def __post_init__(self) -> None:
        if not self.mime_type:
            guessed, _ = mimetypes.guess_type(self.source)
            self.mime_type = guessed or "audio/mpeg"

    def to_message_block(self) -> dict[str, Any]:
        with open(self.source, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return {
            "type": "audio",
            "source": {"type": "base64", "media_type": self.mime_type, "data": data},
        }


MultiModalInput = Union[ImageInput, DocumentInput, AudioInput]


def build_multimodal_message(
    text: str,
    inputs: list[MultiModalInput],
) -> list[dict[str, Any]]:
    """Build a multi-part ``content`` list for provider message APIs.

    Media blocks are prepended; the text prompt is appended as the final part.
    """
    parts: list[dict[str, Any]] = [inp.to_message_block() for inp in inputs]
    if text:
        parts.append({"type": "text", "text": text})
    return parts
