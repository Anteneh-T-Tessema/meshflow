"""Sprint 36 — Prompt management: versioned prompts, A/B testing, registry."""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.prompts.core import (
    PromptVersion,
    PromptTemplate,
    PromptRegistry,
    PromptABTest,
    _extract_variables,
)


# ── _extract_variables ────────────────────────────────────────────────────────

class TestExtractVariables:
    def test_single_variable(self):
        assert _extract_variables("Hello {name}") == ["name"]

    def test_multiple_variables(self):
        assert _extract_variables("Dear {title} {name}") == ["title", "name"]

    def test_no_variables(self):
        assert _extract_variables("No vars here") == []

    def test_deduplicates(self):
        assert _extract_variables("{x} and {x} again") == ["x"]

    def test_preserves_order(self):
        assert _extract_variables("{a} {b} {c}") == ["a", "b", "c"]


# ── PromptVersion ─────────────────────────────────────────────────────────────

class TestPromptVersion:
    def test_version_id_auto_generated(self):
        v = PromptVersion(name="test", content="hello")
        assert len(v.version_id) == 12

    def test_variables_auto_extracted(self):
        v = PromptVersion(name="test", content="Hello {name}, your score is {score}.")
        assert "name" in v.variables
        assert "score" in v.variables

    def test_render_basic(self):
        v = PromptVersion(name="test", content="Hello {name}!")
        assert v.render(name="Alice") == "Hello Alice!"

    def test_render_multiple_vars(self):
        v = PromptVersion(name="test", content="Dear {title} {last_name},")
        result = v.render(title="Dr.", last_name="Smith")
        assert "Dr." in result and "Smith" in result

    def test_render_raises_on_missing_var(self):
        v = PromptVersion(name="test", content="Hello {name}!")
        with pytest.raises(ValueError, match="Missing prompt variables"):
            v.render()

    def test_render_no_vars(self):
        v = PromptVersion(name="test", content="Static content.")
        assert v.render() == "Static content."

    def test_round_trip_dict(self):
        v = PromptVersion(name="p", content="Hello {x}", tags=["a", "b"])
        v2 = PromptVersion.from_dict(v.to_dict())
        assert v2.name == "p"
        assert v2.content == "Hello {x}"
        assert v2.tags == ["a", "b"]
        assert v2.version_id == v.version_id

    def test_different_content_different_version_id(self):
        v1 = PromptVersion(name="p", content="Hello", created_at=1.0)
        v2 = PromptVersion(name="p", content="Hi", created_at=1.0)
        assert v1.version_id != v2.version_id


# ── PromptTemplate ────────────────────────────────────────────────────────────

class TestPromptTemplate:
    def test_name_and_content(self):
        v = PromptVersion(name="my-prompt", content="Do {task}")
        tmpl = PromptTemplate(v)
        assert tmpl.name == "my-prompt"
        assert tmpl.content == "Do {task}"

    def test_render(self):
        v = PromptVersion(name="p", content="Score: {score}")
        tmpl = PromptTemplate(v)
        assert tmpl.render(score="9/10") == "Score: 9/10"

    def test_str(self):
        v = PromptVersion(name="p", content="Raw content")
        tmpl = PromptTemplate(v)
        assert str(tmpl) == "Raw content"

    def test_repr(self):
        v = PromptVersion(name="my-prompt", content="content")
        tmpl = PromptTemplate(v)
        assert "my-prompt" in repr(tmpl)


# ── PromptRegistry ────────────────────────────────────────────────────────────

class TestPromptRegistry:
    def _reg(self) -> PromptRegistry:
        return PromptRegistry(":memory:")

    def test_create_and_get(self):
        reg = self._reg()
        v = reg.create("greet", "Hello {name}!")
        tmpl = reg.get("greet")
        assert tmpl.content == "Hello {name}!"
        assert tmpl.version_id == v.version_id

    def test_get_missing_raises_key_error(self):
        reg = self._reg()
        with pytest.raises(KeyError):
            reg.get("nonexistent")

    def test_get_or_none_missing(self):
        reg = self._reg()
        assert reg.get_or_none("nonexistent") is None

    def test_update_creates_new_version(self):
        reg = self._reg()
        v1 = reg.create("p", "Version 1")
        time.sleep(0.001)
        v2 = reg.update("p", "Version 2")
        assert v1.version_id != v2.version_id
        latest = reg.get("p")
        assert latest.content == "Version 2"

    def test_get_by_version_id(self):
        reg = self._reg()
        v1 = reg.create("p", "V1 content")
        time.sleep(0.001)
        reg.update("p", "V2 content")
        old = reg.get("p", version=v1.version_id)
        assert old.content == "V1 content"

    def test_list_versions(self):
        reg = self._reg()
        reg.create("p", "V1")
        time.sleep(0.001)
        reg.update("p", "V2")
        versions = reg.list_versions("p")
        assert len(versions) == 2
        assert versions[0].content == "V2"  # newest first

    def test_list_names(self):
        reg = self._reg()
        reg.create("alpha", "content A")
        reg.create("beta", "content B")
        names = reg.list_names()
        assert "alpha" in names
        assert "beta" in names

    def test_delete_all_versions(self):
        reg = self._reg()
        reg.create("p", "V1")
        reg.create("p", "V2")
        deleted = reg.delete("p")
        assert deleted == 2
        assert reg.get_or_none("p") is None

    def test_delete_specific_version(self):
        reg = self._reg()
        v1 = reg.create("p", "V1")
        time.sleep(0.001)
        reg.update("p", "V2")
        reg.delete("p", version=v1.version_id)
        versions = reg.list_versions("p")
        assert len(versions) == 1
        assert versions[0].content == "V2"

    def test_tags_stored_and_retrieved(self):
        reg = self._reg()
        reg.create("p", "content", tags=["production", "hipaa"])
        tmpl = reg.get("p")
        assert "production" in tmpl._version.tags

    def test_metadata_stored(self):
        reg = self._reg()
        reg.create("p", "content", metadata={"author": "alice"})
        tmpl = reg.get("p")
        assert tmpl._version.metadata.get("author") == "alice"


# ── PromptABTest ──────────────────────────────────────────────────────────────

class TestPromptABTest:
    def _setup(self) -> tuple[PromptRegistry, PromptABTest]:
        reg = PromptRegistry(":memory:")
        v1 = reg.create("test-prompt", "Version A: {query}")
        time.sleep(0.001)
        v2 = reg.update("test-prompt", "Version B: {query}")
        ab = PromptABTest(reg, "test-prompt", variant_a=v1.version_id, variant_b=v2.version_id)
        return reg, ab

    def test_pick_returns_variant_and_template(self):
        _, ab = self._setup()
        variant, tmpl = ab.pick()
        assert variant in ("a", "b")
        assert isinstance(tmpl, PromptTemplate)

    def test_pick_counts_tracked(self):
        _, ab = self._setup()
        ab.pick()
        ab.pick()
        stats = ab.stats()
        total_picks = stats["a"]["picks"] + stats["b"]["picks"]
        assert total_picks == 2

    def test_record_outcome(self):
        _, ab = self._setup()
        ab.record_outcome("a", score=0.9)
        ab.record_outcome("a", score=0.7)
        stats = ab.stats()
        assert stats["a"]["outcomes"] == 2
        assert stats["a"]["avg_score"] == pytest.approx(0.8)

    def test_record_outcome_invalid_variant(self):
        _, ab = self._setup()
        with pytest.raises(ValueError):
            ab.record_outcome("c", score=0.5)

    def test_winner_no_data(self):
        _, ab = self._setup()
        assert ab.winner() is None

    def test_winner_clear_winner(self):
        _, ab = self._setup()
        ab.record_outcome("a", score=0.9)
        ab.record_outcome("b", score=0.3)
        assert ab.winner() == "a"

    def test_winner_too_close(self):
        _, ab = self._setup()
        ab.record_outcome("a", score=0.8)
        ab.record_outcome("b", score=0.79)  # < 0.05 difference
        assert ab.winner() is None

    def test_split_bias(self):
        reg = PromptRegistry(":memory:")
        reg.create("p", "A")
        time.sleep(0.001)
        reg.update("p", "B")
        ab = PromptABTest(reg, "p", split=1.0)  # always pick A
        for _ in range(10):
            variant, _ = ab.pick()
        stats = ab.stats()
        assert stats["a"]["picks"] == 10
        assert stats["b"]["picks"] == 0

    def test_version_ids_in_stats(self):
        _, ab = self._setup()
        stats = ab.stats()
        assert "version_id" in stats["a"]
        assert "version_id" in stats["b"]


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_imports(self):
        from meshflow.prompts import PromptVersion, PromptTemplate, PromptRegistry, PromptABTest
        assert all(x is not None for x in [PromptVersion, PromptTemplate, PromptRegistry, PromptABTest])
