"""Sprint 86 — multimodal from_bytes/from_pil/OpenAI format, Workflow.run_multimodal, batch_run."""
from __future__ import annotations

import base64
import os
import tempfile

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")

# minimal 1×1 white PNG (89 bytes)
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ═══════════════════════════════════════════════════════════════════════════════
# ImageInput constructors
# ═══════════════════════════════════════════════════════════════════════════════

class TestImageInputFromBytes:
    def test_from_bytes_returns_image_input(self):
        from meshflow import ImageInput
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        assert isinstance(img, ImageInput)

    def test_from_bytes_mime_stored(self):
        from meshflow import ImageInput
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        assert img.mime_type == "image/png"

    def test_from_bytes_no_source(self):
        from meshflow import ImageInput
        img = ImageInput.from_bytes(_PNG_1X1, "image/jpeg")
        assert img.source == ""

    def test_from_bytes_to_message_block_anthropic(self):
        from meshflow import ImageInput
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        block = img.to_message_block()
        assert block["type"] == "image"
        assert block["source"]["type"] == "base64"
        assert block["source"]["media_type"] == "image/png"
        # Data should be valid base64 of our PNG
        decoded = base64.b64decode(block["source"]["data"])
        assert decoded == _PNG_1X1

    def test_from_bytes_to_openai_block(self):
        from meshflow import ImageInput
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        block = img.to_openai_block()
        assert block["type"] == "image_url"
        assert "image_url" in block
        assert block["image_url"]["url"].startswith("data:image/png;base64,")

    def test_default_mime_type(self):
        from meshflow import ImageInput
        img = ImageInput.from_bytes(_PNG_1X1)
        assert img.mime_type == "image/jpeg"


class TestImageInputFromUrl:
    def test_from_url_stores_url(self):
        from meshflow import ImageInput
        img = ImageInput.from_url("https://example.com/chart.png")
        assert img.source == "https://example.com/chart.png"

    def test_from_url_to_message_block_is_url_type(self):
        from meshflow import ImageInput
        img = ImageInput.from_url("https://example.com/chart.png")
        block = img.to_message_block()
        assert block["source"]["type"] == "url"
        assert block["source"]["url"] == "https://example.com/chart.png"

    def test_from_url_to_openai_block(self):
        from meshflow import ImageInput
        img = ImageInput.from_url("https://example.com/chart.png")
        block = img.to_openai_block()
        assert block["type"] == "image_url"
        assert block["image_url"]["url"] == "https://example.com/chart.png"


class TestImageInputFromFile:
    def test_from_file_path_to_message_block(self, tmp_path):
        from meshflow import ImageInput
        path = str(tmp_path / "test.png")
        with open(path, "wb") as f:
            f.write(_PNG_1X1)
        img = ImageInput(source=path)
        block = img.to_message_block()
        assert block["type"] == "image"
        assert block["source"]["type"] == "base64"

    def test_from_file_path_to_openai_block(self, tmp_path):
        from meshflow import ImageInput
        path = str(tmp_path / "test.png")
        with open(path, "wb") as f:
            f.write(_PNG_1X1)
        img = ImageInput(source=path)
        block = img.to_openai_block()
        assert block["type"] == "image_url"
        assert block["image_url"]["url"].startswith("data:")


class TestImageInputFromPil:
    def test_from_pil_works_with_pillow(self):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")
        from meshflow import ImageInput
        pil_img = Image.new("RGB", (4, 4), color=(255, 0, 0))
        img = ImageInput.from_pil(pil_img, "image/png")
        assert img._data  # bytes set
        block = img.to_message_block()
        assert block["source"]["type"] == "base64"

    def test_from_pil_openai_block(self):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")
        from meshflow import ImageInput
        pil_img = Image.new("RGB", (4, 4))
        img = ImageInput.from_pil(pil_img, "image/png")
        block = img.to_openai_block()
        assert block["image_url"]["url"].startswith("data:image/png;base64,")


# ═══════════════════════════════════════════════════════════════════════════════
# DocumentInput constructors
# ═══════════════════════════════════════════════════════════════════════════════

class TestDocumentInputFromStr:
    def test_from_str_stores_text(self):
        from meshflow import DocumentInput
        doc = DocumentInput.from_str("Hello world", "note.txt")
        assert doc._text == "Hello world"
        assert doc.title == "note.txt"

    def test_from_str_to_message_block(self):
        from meshflow import DocumentInput
        doc = DocumentInput.from_str("Hello world", "note.txt")
        block = doc.to_message_block()
        assert block["type"] == "document"
        assert block["source"]["type"] == "text"
        assert block["source"]["text"] == "Hello world"
        assert block["title"] == "note.txt"

    def test_from_str_to_openai_block(self):
        from meshflow import DocumentInput
        doc = DocumentInput.from_str("Hello world", "note.txt")
        block = doc.to_openai_block()
        assert block["type"] == "text"
        assert "Hello world" in block["text"]
        assert "note.txt" in block["text"]


class TestDocumentInputFromBytes:
    def test_from_bytes_stored(self):
        from meshflow import DocumentInput
        doc = DocumentInput.from_bytes(b"%PDF-1.4", "application/pdf", "test.pdf")
        assert doc._data == b"%PDF-1.4"
        assert doc._mime == "application/pdf"
        assert doc.title == "test.pdf"

    def test_from_bytes_to_message_block(self):
        from meshflow import DocumentInput
        doc = DocumentInput.from_bytes(b"%PDF-1.4", "application/pdf", "test.pdf")
        block = doc.to_message_block()
        assert block["type"] == "document"
        assert block["source"]["type"] == "base64"


class TestDocumentInputFromFile:
    def test_from_text_file(self, tmp_path):
        from meshflow import DocumentInput
        path = str(tmp_path / "report.txt")
        open(path, "w").write("quarterly report content")
        doc = DocumentInput(source=path)
        block = doc.to_message_block()
        assert block["source"]["text"] == "quarterly report content"


# ═══════════════════════════════════════════════════════════════════════════════
# AudioInput.from_bytes
# ═══════════════════════════════════════════════════════════════════════════════

class TestAudioInputFromBytes:
    def test_from_bytes(self):
        from meshflow import AudioInput
        audio = AudioInput.from_bytes(b"\xff\xfb\x90\x04", "audio/mpeg")
        assert audio._data == b"\xff\xfb\x90\x04"
        assert audio.mime_type == "audio/mpeg"

    def test_from_bytes_to_message_block(self):
        from meshflow import AudioInput
        audio = AudioInput.from_bytes(b"\xff\xfb\x90\x04", "audio/mpeg")
        block = audio.to_message_block()
        assert block["type"] == "audio"
        assert block["source"]["type"] == "base64"

    def test_from_bytes_to_openai_block(self):
        from meshflow import AudioInput
        audio = AudioInput.from_bytes(b"\xff\xfb\x90\x04", "audio/mpeg")
        block = audio.to_openai_block()
        assert block["type"] == "input_audio"
        assert "input_audio" in block


# ═══════════════════════════════════════════════════════════════════════════════
# build_multimodal_message — provider-aware
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildMultimodalMessage:
    def test_anthropic_image_block(self):
        from meshflow import ImageInput, build_multimodal_message
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        blocks = build_multimodal_message("describe this", [img], provider="anthropic")
        assert blocks[0]["type"] == "image"
        assert blocks[-1]["type"] == "text"
        assert blocks[-1]["text"] == "describe this"

    def test_openai_image_block(self):
        from meshflow import ImageInput, build_multimodal_message
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        blocks = build_multimodal_message("describe this", [img], provider="openai")
        assert blocks[0]["type"] == "image_url"
        assert blocks[-1]["text"] == "describe this"

    def test_default_provider_is_anthropic(self):
        from meshflow import ImageInput, build_multimodal_message
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        blocks = build_multimodal_message("q", [img])
        assert blocks[0]["type"] == "image"  # Anthropic format

    def test_openai_alias(self):
        from meshflow import ImageInput, build_multimodal_message
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        for alias in ("openai", "gpt", "azure"):
            blocks = build_multimodal_message("q", [img], provider=alias)
            assert blocks[0]["type"] == "image_url", f"failed for provider={alias}"

    def test_empty_inputs_text_only(self):
        from meshflow import build_multimodal_message
        blocks = build_multimodal_message("just text", [])
        assert len(blocks) == 1
        assert blocks[0]["text"] == "just text"

    def test_multiple_inputs_order(self):
        from meshflow import ImageInput, DocumentInput, build_multimodal_message
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        doc = DocumentInput.from_str("context", "ctx.txt")
        blocks = build_multimodal_message("analyze", [img, doc])
        assert blocks[0]["type"] == "image"
        assert blocks[1]["type"] == "document"
        assert blocks[2]["type"] == "text"

    def test_exported_from_meshflow(self):
        from meshflow import build_multimodal_message
        assert callable(build_multimodal_message)


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow.run_multimodal
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowRunMultimodal:
    def test_run_multimodal_completes(self):
        from meshflow import Workflow, Agent, ImageInput
        wf = Workflow()
        wf.add(Agent("analyst"))
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        result = wf.run_multimodal("What is in this image?", [img])
        assert result is not None

    def test_run_multimodal_result_has_output(self):
        from meshflow import Workflow, Agent, ImageInput
        wf = Workflow()
        wf.add(Agent("analyst"))
        img = ImageInput.from_bytes(_PNG_1X1, "image/png")
        result = wf.run_multimodal("Describe.", [img])
        assert hasattr(result, "output") or hasattr(result, "total_cost_usd")

    def test_run_multimodal_document(self):
        from meshflow import Workflow, Agent, DocumentInput
        wf = Workflow()
        wf.add(Agent("extractor"))
        doc = DocumentInput.from_str("Revenue: $1.2M", "report.txt")
        result = wf.run_multimodal("Extract key figures.", [doc])
        assert result is not None

    def test_run_multimodal_empty_inputs_degrades_gracefully(self):
        from meshflow import Workflow, Agent
        wf = Workflow()
        wf.add(Agent("analyst"))
        result = wf.run_multimodal("Text-only task.", [])
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow.batch_run
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowBatchRun:
    def _wf(self):
        from meshflow import Workflow, Agent
        wf = Workflow()
        wf.add(Agent("worker"))
        return wf

    def test_batch_run_returns_list(self):
        wf = self._wf()
        results = wf.batch_run(["task 1", "task 2", "task 3"])
        assert isinstance(results, list)
        assert len(results) == 3

    def test_batch_run_same_count_as_tasks(self):
        wf = self._wf()
        tasks = [f"task {i}" for i in range(6)]
        results = wf.batch_run(tasks, max_concurrency=3)
        assert len(results) == 6

    def test_batch_run_all_have_output(self):
        wf = self._wf()
        results = wf.batch_run(["summarise A", "summarise B"])
        for r in results:
            assert hasattr(r, "output") or hasattr(r, "total_cost_usd")

    def test_batch_run_empty_tasks_returns_empty(self):
        wf = self._wf()
        results = wf.batch_run([])
        assert results == []

    def test_batch_run_single_task(self):
        wf = self._wf()
        results = wf.batch_run(["one task"])
        assert len(results) == 1

    def test_batch_run_concurrency_one(self):
        wf = self._wf()
        results = wf.batch_run(["a", "b", "c"], max_concurrency=1)
        assert len(results) == 3

    def test_batch_run_cost_accumulated(self):
        wf = self._wf()
        results = wf.batch_run(["x", "y"])
        # In mock mode, costs are 0 but the field should exist
        for r in results:
            assert hasattr(r, "total_cost_usd")


# ═══════════════════════════════════════════════════════════════════════════════
# Backward compatibility — existing ImageInput(source=path) still works
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackwardCompat:
    def test_url_source_still_works(self):
        from meshflow import ImageInput
        img = ImageInput(source="https://example.com/photo.jpg")
        block = img.to_message_block()
        assert block["source"]["type"] == "url"

    def test_file_source_still_works(self, tmp_path):
        from meshflow import ImageInput
        path = str(tmp_path / "img.png")
        with open(path, "wb") as f:
            f.write(_PNG_1X1)
        img = ImageInput(source=path)
        block = img.to_message_block()
        assert block["source"]["type"] == "base64"

    def test_document_from_str_source_still_works(self, tmp_path):
        from meshflow import DocumentInput
        path = str(tmp_path / "doc.txt")
        open(path, "w").write("content")
        doc = DocumentInput(source=path)
        block = doc.to_message_block()
        assert block["source"]["text"] == "content"

    def test_build_multimodal_message_no_provider_arg(self):
        from meshflow import ImageInput, build_multimodal_message
        img = ImageInput.from_url("https://example.com/x.jpg")
        blocks = build_multimodal_message("q", [img])
        assert len(blocks) == 2
