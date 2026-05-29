"""Sprint 70 — Agent template registry tests."""

from __future__ import annotations

import os
import tempfile
import pytest

from meshflow.registry.templates import AgentTemplate, TemplateRegistry


# ── AgentTemplate ─────────────────────────────────────────────────────────────


def test_template_to_yaml_round_trip():
    tmpl = AgentTemplate(
        name="market-researcher",
        role="researcher",
        model="claude-sonnet-4-6",
        description="Deep market research agent.",
        tags=["research", "market"],
        tools=["web_search"],
        skills=["data_analysis"],
    )
    yaml_str = tmpl.to_yaml()
    assert "market-researcher" in yaml_str
    assert "researcher" in yaml_str

    loaded = AgentTemplate.from_dict(tmpl.to_dict())
    assert loaded.name == tmpl.name
    assert loaded.role == tmpl.role
    assert loaded.tools == ["web_search"]
    assert loaded.tags == ["research", "market"]


def test_template_from_yaml_file():
    tmpl = AgentTemplate(name="test-agent", role="executor", description="A test agent.")
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(tmpl.to_yaml())
        path = f.name
    try:
        loaded = AgentTemplate.from_yaml(path)
        assert loaded.name == "test-agent"
    finally:
        os.unlink(path)


def test_template_to_agent():
    tmpl = AgentTemplate(name="my-agent", role="executor", model="claude-sonnet-4-6")
    agent = tmpl.to_agent()
    assert agent.name == "my-agent"


def test_template_str():
    tmpl = AgentTemplate(name="foo", version="2.1.0")
    assert "foo" in str(tmpl)
    assert "v2.1.0" in str(tmpl)


# ── TemplateRegistry ──────────────────────────────────────────────────────────


@pytest.fixture
def reg(tmp_path):
    return TemplateRegistry(registry_dir=str(tmp_path))


def test_publish_and_list(reg):
    tmpl = AgentTemplate(name="alpha", description="Alpha agent.")
    reg.publish(tmpl)
    listed = reg.list()
    assert len(listed) == 1
    assert listed[0].name == "alpha"


def test_pull_existing(reg):
    tmpl = AgentTemplate(name="beta", description="Beta agent.")
    reg.publish(tmpl)
    retrieved = reg.pull("beta")
    assert retrieved.name == "beta"


def test_pull_missing_raises_key_error(reg):
    with pytest.raises(KeyError):
        reg.pull("does-not-exist")


def test_get_returns_none_for_missing(reg):
    assert reg.get("no-such-template") is None


def test_delete_existing(reg):
    tmpl = AgentTemplate(name="to-delete")
    reg.publish(tmpl)
    removed = reg.delete("to-delete")
    assert removed is True
    assert reg.get("to-delete") is None


def test_delete_nonexistent(reg):
    assert reg.delete("ghost") is False


def test_publish_overwrite(reg):
    tmpl1 = AgentTemplate(name="agent", description="v1")
    tmpl2 = AgentTemplate(name="agent", description="v2")
    reg.publish(tmpl1)
    reg.publish(tmpl2)
    assert len(reg.list()) == 1
    assert reg.pull("agent").description == "v2"


# ── search ────────────────────────────────────────────────────────────────────


def test_search_by_description(reg):
    reg.publish(AgentTemplate(name="research-agent", role="researcher",
                               description="Deep market and competitive research."))
    reg.publish(AgentTemplate(name="writer-agent", role="executor",
                               description="Professional content writing."))
    results = reg.search("market research")
    assert len(results) >= 1
    assert results[0].name == "research-agent"


def test_search_by_tag(reg):
    reg.publish(AgentTemplate(name="sec-agent", description="", tags=["security", "audit"]))
    reg.publish(AgentTemplate(name="data-agent", description="", tags=["data", "analysis"]))
    results = reg.search("security")
    assert any(t.name == "sec-agent" for t in results)


def test_search_empty_query_returns_all(reg):
    reg.publish(AgentTemplate(name="a1", description="first"))
    reg.publish(AgentTemplate(name="a2", description="second"))
    results = reg.search("")
    assert len(results) == 2


def test_search_no_match_returns_empty(reg):
    reg.publish(AgentTemplate(name="a1", description="first"))
    results = reg.search("zzz_no_match_xyz")
    assert results == []


def test_stats(reg):
    reg.publish(AgentTemplate(name="t1"))
    reg.publish(AgentTemplate(name="t2"))
    s = reg.stats()
    assert s["template_count"] == 2
