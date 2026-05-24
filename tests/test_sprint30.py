"""Sprint 30 — Multi-modal: image/audio/document inputs + Agent.run_multimodal."""

from __future__ import annotations

import base64
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.multimodal.inputs import (
    ImageInput,
    DocumentInput,
    AudioInput,
    build_multimodal_message,
)


# ── ImageInput ────────────────────────────────────────────────────────────────

class TestImageInput:
    def test_url_mime_default(self):
        img = ImageInput(source="https://example.com/photo.jpg")
        assert img.mime_type == "image/jpeg"

    def test_url_block_type(self):
        img = ImageInput(source="https://example.com/photo.png")
        block = img.to_message_block()
        assert block["type"] == "image"
        assert block["source"]["type"] == "url"
        assert block["source"]["url"] == "https://example.com/photo.png"

    def test_local_file_block(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
            path = f.name
        try:
            img = ImageInput(source=path)
            block = img.to_message_block()
            assert block["type"] == "image"
            assert block["source"]["type"] == "base64"
            assert block["source"]["media_type"] == "image/png"
            # valid base64
            base64.b64decode(block["source"]["data"])
        finally:
            os.unlink(path)

    def test_mime_type_auto_jpeg(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff")
            path = f.name
        try:
            img = ImageInput(source=path)
            assert "jpeg" in img.mime_type or "jpg" in img.mime_type
        finally:
            os.unlink(path)

    def test_explicit_mime_override(self):
        img = ImageInput(source="https://example.com/img", mime_type="image/webp")
        assert img.mime_type == "image/webp"


# ── DocumentInput ─────────────────────────────────────────────────────────────

class TestDocumentInput:
    def test_text_file_block(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Hello document")
            path = f.name
        try:
            doc = DocumentInput(source=path)
            block = doc.to_message_block()
            assert block["type"] == "document"
            assert block["source"]["type"] == "text"
            assert "Hello document" in block["source"]["text"]
        finally:
            os.unlink(path)

    def test_markdown_file_block(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Title\nContent here")
            path = f.name
        try:
            doc = DocumentInput(source=path)
            block = doc.to_message_block()
            assert block["source"]["text"].startswith("# Title")
        finally:
            os.unlink(path)

    def test_json_file_block(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"key": "value"}')
            path = f.name
        try:
            doc = DocumentInput(source=path)
            block = doc.to_message_block()
            assert '"key"' in block["source"]["text"]
        finally:
            os.unlink(path)

    def test_title_auto_from_filename(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            path = f.name
        try:
            doc = DocumentInput(source=path)
            assert doc.title == os.path.basename(path)
        finally:
            os.unlink(path)

    def test_title_explicit(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"content")
            path = f.name
        try:
            doc = DocumentInput(source=path, title="My Document")
            assert doc.title == "My Document"
            block = doc.to_message_block()
            assert block["title"] == "My Document"
        finally:
            os.unlink(path)

    def test_pdf_fallback_to_base64(self):
        """Without pypdf installed, PDF falls back to base64 block."""
        import importlib
        has_pypdf = importlib.util.find_spec("pypdf") is not None

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 minimal")
            path = f.name
        try:
            doc = DocumentInput(source=path)
            block = doc.to_message_block()
            assert block["type"] == "document"
            if not has_pypdf:
                assert block["source"]["type"] == "base64"
                assert block["source"]["media_type"] == "application/pdf"
        finally:
            os.unlink(path)


# ── AudioInput ────────────────────────────────────────────────────────────────

class TestAudioInput:
    def test_mp3_mime_auto(self):
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"\xff\xfb" + b"\x00" * 10)
            path = f.name
        try:
            audio = AudioInput(source=path)
            assert "mpeg" in audio.mime_type or "mp3" in audio.mime_type
        finally:
            os.unlink(path)

    def test_block_type(self):
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"\xff\xfb" + b"\x00" * 10)
            path = f.name
        try:
            audio = AudioInput(source=path)
            block = audio.to_message_block()
            assert block["type"] == "audio"
            assert block["source"]["type"] == "base64"
            base64.b64decode(block["source"]["data"])  # valid base64
        finally:
            os.unlink(path)

    def test_explicit_mime(self):
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(b"OggS")
            path = f.name
        try:
            audio = AudioInput(source=path, mime_type="audio/ogg")
            assert audio.mime_type == "audio/ogg"
        finally:
            os.unlink(path)


# ── build_multimodal_message ──────────────────────────────────────────────────

class TestBuildMultimodalMessage:
    def test_text_only(self):
        parts = build_multimodal_message("hello", [])
        assert len(parts) == 1
        assert parts[0]["type"] == "text"
        assert parts[0]["text"] == "hello"

    def test_image_then_text(self):
        img = ImageInput(source="https://example.com/img.jpg")
        parts = build_multimodal_message("describe this", [img])
        assert len(parts) == 2
        assert parts[0]["type"] == "image"
        assert parts[1]["type"] == "text"

    def test_no_text_still_works(self):
        img = ImageInput(source="https://example.com/img.jpg")
        parts = build_multimodal_message("", [img])
        assert len(parts) == 1
        assert parts[0]["type"] == "image"

    def test_multiple_inputs(self):
        img = ImageInput(source="https://example.com/img.jpg")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("context")
            path = f.name
        try:
            doc = DocumentInput(source=path)
            parts = build_multimodal_message("analyze", [img, doc])
            types = [p["type"] for p in parts]
            assert types == ["image", "document", "text"]
        finally:
            os.unlink(path)


# ── Agent.run_multimodal ───────────────────────────────────────────────────────

class TestAgentRunMultimodal:
    @pytest.mark.asyncio
    async def test_run_multimodal_with_url_image(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="vision-agent", role="executor")
        img = ImageInput(source="https://example.com/diagram.png")
        result = await agent.run_multimodal("Describe this image", inputs=[img])
        assert "result" in result
        assert result["multimodal_inputs"] == 1
        assert isinstance(result["result"], str)

    @pytest.mark.asyncio
    async def test_run_multimodal_with_document(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Report\nKey findings: growth is positive.")
            path = f.name
        try:
            agent = Agent(name="doc-agent", role="researcher")
            doc = DocumentInput(source=path)
            result = await agent.run_multimodal("Summarize this document", inputs=[doc])
            assert result["multimodal_inputs"] == 1
            assert not result["blocked"]
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_run_multimodal_returns_tokens(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="tok-agent", role="executor")
        img = ImageInput(source="https://example.com/x.jpg")
        result = await agent.run_multimodal("task", inputs=[img])
        assert "tokens" in result
        assert isinstance(result["tokens"], int)

    @pytest.mark.asyncio
    async def test_run_multimodal_no_inputs(self):
        os.environ["MESHFLOW_MOCK"] = "1"
        from meshflow.agents.builder import Agent

        agent = Agent(name="plain-agent", role="executor")
        result = await agent.run_multimodal("plain text task", inputs=[])
        assert "result" in result


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_top_level_imports(self):
        from meshflow.multimodal import (
            ImageInput, DocumentInput, AudioInput,
            MultiModalInput, build_multimodal_message,
        )
        assert all(x is not None for x in [
            ImageInput, DocumentInput, AudioInput,
            MultiModalInput, build_multimodal_message,
        ])
